import random

from pdmatrix import (
    DISCOUNT_ORDER,
    PREMIUM_ORDER,
    Aggregator,
    Candle,
    Config,
    Dir,
    Kind,
    MatrixEngine,
    PDArray,
    State,
    TFEngine,
    bucket_key,
    discount_rank,
    in_macro,
    parse_macros,
    premium_rank,
    tf_name,
)

from .helpers import FIVE_MIN, bars, ny_ms


def test_matrix_order_matches_the_slide():
    assert [k.name for k in PREMIUM_ORDER] == ["LP", "RB", "OB", "FVG", "VI", "BB", "MB"]
    assert [k.name for k in DISCOUNT_ORDER] == ["MB", "BB", "VI", "FVG", "OB", "RB", "LP"]
    assert premium_rank(Kind.LP) == 1 and premium_rank(Kind.MB) == 7
    assert discount_rank(Kind.MB) == 1 and discount_rank(Kind.LP) == 7


def _arr(i, kind, d, bottom, top, **kw):
    return PDArray(id=i, tf="60", kind=kind, dir=d, top=top, bottom=bottom, mid=(top + bottom) / 2,
                   i_origin=0, t_origin=0, i_formed=0, **kw)


def test_snapshot_places_arrays_by_polarity_and_location():
    e = TFEngine("60")
    e.trail_high, e.trail_low = 120.0, 100.0  # EQ 110
    e.live = [
        _arr(1, Kind.FVG, Dir.BEAR, 115, 116),  # premium FVG
        _arr(2, Kind.FVG, Dir.BEAR, 118, 119),  # farther premium FVG
        _arr(3, Kind.LP, Dir.BEAR, 120, 120),  # buyside liquidity
        _arr(4, Kind.OB, Dir.BULL, 103, 104),  # discount OB
        _arr(5, Kind.MB, Dir.BULL, 108, 109),  # discount MB near EQ
        _arr(6, Kind.OB, Dir.BULL, 112, 113),  # bullish OB in premium: misaligned, not in matrix
        _arr(7, Kind.FVG, Dir.BEAR, 104, 105, inverted=True),  # bearish iFVG in discount: not in matrix
    ]
    s = e.snapshot(111.0)
    assert s.eq == 110.0 and round(s.position, 2) == 0.55
    assert s.premium[premium_rank(Kind.FVG)].id == 1  # nearest to price wins the rank
    assert s.premium[premium_rank(Kind.LP)].id == 3
    assert s.discount[discount_rank(Kind.OB)].id == 4
    assert s.discount[discount_rank(Kind.MB)].id == 5
    assert all(a is None or a.dir == Dir.BEAR for a in s.premium.values())
    assert all(a is None or a.dir == Dir.BULL for a in s.discount.values())
    assert 6 not in [a.id for a in list(s.premium.values()) + list(s.discount.values()) if a]
    assert s.next_up.id == 6 and s.next_down.id == 5


def test_tf_names():
    assert [tf_name(x) for x in ["1", "15", "60", "240", "D", "W", "M"]] == ["1m", "15m", "1H", "4H", "1D", "1W", "1M"]


def test_aggregator_drops_partial_first_period_and_builds_ohlc():
    start = ny_ms(2026, 9, 28, 9, 5)
    rows = [
        (1, 2, 0.5, 1.5),  # 09:05 partial 09:00 bucket
        (1.5, 2.5, 1.0, 2.0),  # 09:10
        (2.0, 3.0, 1.8, 2.8),  # 09:15 -> new bucket
        (2.8, 3.5, 2.5, 3.1),  # 09:20
        (3.1, 3.2, 1.2, 1.4),  # 09:25
        (1.4, 1.6, 1.1, 1.5),  # 09:30 -> completes 09:15 bucket
    ]
    agg = Aggregator("15")
    done = [agg.push(c) for c in bars(rows, start=start)]
    assert done[:5] == [None] * 5
    c = done[5]
    assert c.t == ny_ms(2026, 9, 28, 9, 15) and c.tc == ny_ms(2026, 9, 28, 9, 30)
    assert (c.o, c.h, c.l, c.c) == (2.0, 3.5, 1.2, 1.4)


def test_bucket_keys_for_daily_weekly_monthly():
    a = ny_ms(2026, 9, 28, 23, 55)
    b = ny_ms(2026, 9, 29, 0, 0)
    assert bucket_key(a, "D") != bucket_key(b, "D")
    assert bucket_key(a, "W") == bucket_key(b, "W")
    assert bucket_key(a, "M") == bucket_key(b, "M") != bucket_key(ny_ms(2026, 10, 1, 0, 0), "M")
    # session start shifts the daily boundary (e.g. 18:00 New York for futures)
    assert bucket_key(ny_ms(2026, 9, 28, 18, 0), "D", session_start_min=18 * 60) == \
        bucket_key(ny_ms(2026, 9, 29, 17, 55), "D", session_start_min=18 * 60)


def test_macro_windows():
    w = parse_macros("0950-1010,1320-1340")
    assert w == [(590, 610), (800, 820)]
    t = ny_ms(2026, 9, 28, 9, 50)
    assert in_macro(t, t + FIVE_MIN, w)
    t = ny_ms(2026, 9, 28, 9, 45)
    assert not in_macro(t, t + FIVE_MIN, w)  # 09:45-09:50 ends where the window starts
    t = ny_ms(2026, 9, 28, 9, 0)
    assert in_macro(t, t + 3_600_000, w)  # a 1H bar overlapping 09:50
    t = ny_ms(2026, 9, 28, 12, 15)
    assert not in_macro(t, t + FIVE_MIN, w)
    assert in_macro(t, t + 86_400_000, w)  # daily bars always pass


def _random_walk(n, seed=7, start=None, step=FIVE_MIN):
    rnd = random.Random(seed)
    start = start or ny_ms(2026, 6, 1, 0, 0)
    px = 2300.0
    out = []
    for k in range(n):
        o = px
        c = o + rnd.gauss(0, 1.2) + (0.8 if (k // 400) % 2 else -0.8) * 0.2
        h = max(o, c) + abs(rnd.gauss(0, 0.7))
        l = min(o, c) - abs(rnd.gauss(0, 0.7))
        # occasional gaps between bodies (volume imbalances)
        if rnd.random() < 0.05:
            o += rnd.choice((-1, 1)) * 0.4
            h, l = max(h, o), min(l, o)
        t = start + k * step
        out.append(Candle(t, o, h, l, c, t + step))
        px = c
    return out


def test_multi_timeframe_invariants_on_random_walk():
    candles = _random_walk(6000)
    m = MatrixEngine("5", ["15", "60", "240", "D", "3"], Config())
    m.run(candles)
    assert m.skipped == ["3"]
    assert m.timeframes == ["5", "15", "60", "240", "D"]
    # every HTF engine got the completed periods: the first one is always dropped
    # (it may be partial) and the last one is still forming
    assert m.engines["15"].n == len(candles) // 3 - 2
    assert m.engines["60"].n == len(candles) // 12 - 2
    for tf, eng in m.engines.items():
        assert eng.history, f"{tf} detected nothing"
        kinds = {a.kind for a in eng.history}
        assert {Kind.LP, Kind.FVG, Kind.OB}.issubset(kinds), (tf, kinds)
        assert len(eng.live) <= m.cfg.max_live
        for a in eng.history:
            assert a.top >= a.bottom
            if a.live:
                assert a.i_end is None
            else:
                assert a.i_end is not None and a.i_end >= a.i_formed
                assert a not in eng.live
            if a.parent_id is not None:
                parent = next(p for p in eng.history if p.id == a.parent_id)
                assert parent.state == State.INVALIDATED and parent.dir == -a.dir
                assert a.kind in (Kind.BB, Kind.MB) or (a.kind == Kind.FVG and a.inverted)
    # HTF candle equals the aggregate of its 5m bars
    eng = m.engines["60"]
    first = eng.candles[0]
    members = [c for c in candles if first.t <= c.t < first.tc]
    assert len(members) == 12
    assert first.h == max(c.h for c in members) and first.l == min(c.l for c in members)
    assert first.o == members[0].o and first.c == members[-1].c

    snap = m.snapshot()
    for s in snap.values():
        for a in s.premium.values():
            assert a is None or (a.dir == Dir.BEAR and a.mid > s.eq)
        for a in s.discount.values():
            assert a is None or (a.dir == Dir.BULL and a.mid < s.eq)
    text = m.render()
    assert "P1 Liquidity Pool" in text and "D7 Liquidity Pool" in text and "EQ" in text
    seq_up = m.sequence(direction=1)
    assert all(a.bottom <= b.bottom for a, b in zip(seq_up, seq_up[1:]))
    assert all(a.bottom > m.last.c for a in seq_up)


def test_engine_is_deterministic():
    candles = _random_walk(1500, seed=3)
    a = MatrixEngine("5", ["15", "60"]).run(candles)
    b = MatrixEngine("5", ["15", "60"]).run(candles)
    assert a.render() == b.render()
    assert [(e.type, e.tf, e.i) for e in a.events] == [(e.type, e.tf, e.i) for e in b.events]
