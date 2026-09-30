from pdmatrix import Config, Dir, Kind, State, TFEngine

from .helpers import feed, of

LOOSE = dict(swing_len=1, fvg_min_atr=0.0, vi_min_atr=0.0, disp_atr=100.0)


def test_bullish_fvg_full_lifecycle_and_inversion():
    e = TFEngine("5", Config(**LOOSE))
    feed(e, [
        (10.0, 11.0, 9.5, 10.8),
        (10.8, 13.0, 10.7, 12.9),
        (12.9, 13.5, 12.0, 13.4),  # candle 3 low 12 > candle 1 high 11 -> bullish FVG 11..12
    ])
    fvg = of(e, Kind.FVG, Dir.BULL)[0]
    assert (fvg.bottom, fvg.top, fvg.mid) == (11.0, 12.0, 11.5)
    assert (fvg.i_origin, fvg.i_formed, fvg.state) == (1, 2, State.FRESH)

    feed(e, [(13.4, 13.6, 11.8, 12.2)])  # trades into the gap
    assert fvg.state == State.TESTED and fvg.taps == 1 and not fvg.ce
    feed(e, [(12.2, 12.4, 11.4, 11.9)])  # still inside, reaches consequent encroachment
    assert fvg.taps == 1 and fvg.ce
    feed(e, [(11.9, 12.0, 10.9, 11.2)])  # wick through the whole gap, close back inside
    assert fvg.state == State.FILLED
    events = feed(e, [(11.2, 11.3, 10.5, 10.7)])  # close below the low -> inversion
    assert fvg.state == State.INVALIDATED and fvg.i_end == 6
    conv = [ev for ev in events if ev.type == "converted"]
    assert len(conv) == 1 and conv[0].array is fvg
    ifvg = conv[0].child
    assert ifvg.kind == Kind.FVG and ifvg.inverted and ifvg.dir == Dir.BEAR
    assert (ifvg.bottom, ifvg.top, ifvg.parent_id, ifvg.label) == (11.0, 12.0, fvg.id, "iFVG")

    feed(e, [(10.7, 11.5, 10.6, 11.4)])
    assert ifvg.state == State.TESTED
    events = feed(e, [(11.4, 12.6, 11.3, 12.5)])  # close above the iFVG -> dead, no successor
    assert ifvg.state == State.INVALIDATED
    assert not any(ev.type == "converted" and ev.array is ifvg for ev in events)


def test_bearish_fvg_detection():
    e = TFEngine("5", Config(**LOOSE))
    feed(e, [(13.4, 13.5, 12.0, 12.1), (12.1, 12.2, 10.0, 10.1), (10.1, 11.0, 9.8, 10.2)])
    fvg = of(e, Kind.FVG, Dir.BEAR)[0]
    assert (fvg.bottom, fvg.top) == (11.0, 12.0)
    feed(e, [(10.2, 12.5, 10.1, 12.3)])  # close above the high -> bullish iFVG
    assert fvg.state == State.INVALIDATED
    child = [a for a in e.live if a.parent_id == fvg.id][0]
    assert child.dir == Dir.BULL and child.inverted


def test_fvg_min_size_filter():
    e = TFEngine("5", Config(**{**LOOSE, "fvg_min_atr": 5.0}))
    feed(e, [(10.0, 11.0, 9.5, 10.8), (10.8, 13.0, 10.7, 12.9), (12.9, 13.5, 12.0, 13.4)])
    assert of(e, Kind.FVG) == []


def test_volume_imbalance_requires_body_gap_with_wick_overlap():
    e = TFEngine("5", Config(**LOOSE))
    feed(e, [(10.0, 10.6, 9.9, 10.5), (10.7, 11.2, 10.4, 11.1)])
    vi = of(e, Kind.VI, Dir.BULL)[0]
    assert (vi.bottom, vi.top) == (10.5, 10.7)

    e = TFEngine("5", Config(**LOOSE))
    feed(e, [(10.0, 10.6, 9.9, 10.5), (10.7, 11.2, 10.65, 11.1)])  # wicks do not overlap
    assert of(e, Kind.VI) == []
    e = TFEngine("5", Config(**{**LOOSE, "vi_wick_overlap": False}))
    feed(e, [(10.0, 10.6, 9.9, 10.5), (10.7, 11.2, 10.65, 11.1)])
    assert len(of(e, Kind.VI, Dir.BULL)) == 1


def test_volume_imbalance_is_terminal_when_closed_through():
    e = TFEngine("5", Config(**LOOSE))
    feed(e, [(11.0, 11.1, 10.4, 10.5), (10.3, 10.6, 9.9, 10.0)])  # bearish VI 10.3..10.5
    vi = of(e, Kind.VI, Dir.BEAR)[0]
    assert (vi.bottom, vi.top) == (10.3, 10.5)
    events = feed(e, [(10.0, 10.8, 9.95, 10.7)])
    assert vi.state == State.INVALIDATED
    assert not any(ev.type == "converted" for ev in events)


LP_PATH = [
    (10.0, 10.5, 9.8, 10.2),
    (10.2, 11.0, 10.1, 10.8),  # swing high 11.0
    (10.8, 10.9, 10.3, 10.4),
    (10.4, 10.6, 10.0, 10.1),  # swing low 10.0
    (10.1, 10.98, 10.05, 10.9),  # equal high 10.98
    (10.9, 10.95, 10.5, 10.6),
]


def test_liquidity_pool_equal_highs_then_sweep():
    e = TFEngine("5", Config(**LOOSE))
    feed(e, LP_PATH)
    bsl = of(e, Kind.LP, Dir.BEAR)
    assert len(bsl) == 1, "equal highs merge into one pool"
    assert bsl[0].top == 11.0 and bsl[0].touches == 2 and bsl[0].label == "BSL"
    ssl = of(e, Kind.LP, Dir.BULL)
    assert len(ssl) == 1 and ssl[0].bottom == 10.0

    events = feed(e, [(10.6, 11.2, 10.55, 10.9)])  # wick above, close back below
    assert bsl[0].state == State.SWEPT and bsl[0] not in e.live
    assert any(ev.type == "swept" and ev.array is bsl[0] for ev in events)


def test_liquidity_pool_run_on_close_beyond():
    e = TFEngine("5", Config(**LOOSE))
    feed(e, LP_PATH + [(10.6, 11.3, 10.55, 11.1)])
    assert of(e, Kind.LP, Dir.BEAR)[0].state == State.RUN


def test_rejection_block_lifecycle():
    e = TFEngine("5", Config(**LOOSE))
    feed(e, [
        (10.0, 10.5, 9.8, 10.2),
        (10.2, 11.0, 10.1, 10.3),  # long upper wick at the swing high
        (10.3, 10.6, 10.0, 10.1),  # confirms the swing, trades into the wick
    ])
    rb = of(e, Kind.RB, Dir.BEAR)[0]
    assert (rb.bottom, rb.top) == (10.3, 11.0)
    assert rb.state == State.TESTED and rb.taps == 1  # retro-stepped over the confirming candle
    feed(e, [(10.1, 10.2, 9.9, 10.0), (10.0, 10.5, 9.95, 10.4)])
    assert rb.taps == 2
    events = feed(e, [(10.4, 11.3, 10.35, 11.2)])  # close above the wick high
    assert rb.state == State.INVALIDATED
    assert not any(ev.type == "converted" and ev.array is rb for ev in events)


def test_small_wick_swing_is_not_a_rejection_block():
    e = TFEngine("5", Config(**LOOSE))
    feed(e, LP_PATH)
    assert of(e, Kind.RB, Dir.BEAR) == []


def test_order_block_fails_into_breaker_after_running_liquidity():
    e = TFEngine("5", Config(**LOOSE))
    feed(e, [
        (10.0, 10.4, 9.9, 10.3),
        (10.3, 11.0, 10.2, 10.9),  # swing high 11.0 (buyside liquidity)
        (10.9, 10.95, 10.5, 10.6),
        (10.6, 10.7, 10.2, 10.3),  # last down-close candle -> bullish OB 10.2..10.7
        (10.3, 10.8, 10.25, 10.75),
        (10.75, 11.3, 10.7, 11.25),  # closes above 11.0: structure break, liquidity taken
    ])
    ob = of(e, Kind.OB, Dir.BULL)[0]
    assert (ob.bottom, ob.top, ob.mid, ob.i_origin) == (10.2, 10.7, 10.45, 3)
    assert ob.liq_ref == 11.0 and ob.took_liq
    feed(e, [(11.25, 11.35, 10.9, 11.0), (11.0, 11.05, 10.4, 10.5)])
    assert ob.state == State.TESTED and ob.ce
    events = feed(e, [(10.5, 10.55, 9.9, 10.0)])  # close below the OB low
    assert ob.state == State.INVALIDATED
    bb = [ev.child for ev in events if ev.type == "converted" and ev.array is ob][0]
    assert bb.kind == Kind.BB and bb.dir == Dir.BEAR and (bb.bottom, bb.top) == (10.2, 10.7)


def test_order_block_fails_into_mitigation_block_without_sweep():
    e = TFEngine("5", Config(**{**LOOSE, "disp_atr": 0.3}))
    feed(e, [
        (10.0, 10.4, 9.9, 10.3),
        (10.3, 11.0, 10.2, 10.9),  # swing high 11.0
        (10.9, 10.95, 10.5, 10.6),
        (10.6, 10.7, 10.2, 10.3),  # last down-close candle
        (10.3, 10.95, 10.28, 10.9),  # displacement (bullish FVG with candle 6) but no new high
        (10.9, 10.98, 10.75, 10.85),
    ])
    ob = of(e, Kind.OB, Dir.BULL)[0]
    assert (ob.bottom, ob.top) == (10.2, 10.7) and not ob.took_liq
    feed(e, [(10.85, 10.9, 10.3, 10.4)])
    events = feed(e, [(10.4, 10.45, 9.9, 10.0)])
    mb = [ev.child for ev in events if ev.type == "converted" and ev.array is ob][0]
    assert mb.kind == Kind.MB and mb.dir == Dir.BEAR


def test_breaker_is_terminal_when_closed_back_through():
    e = TFEngine("5", Config(**LOOSE))
    feed(e, [
        (10.0, 10.4, 9.9, 10.3), (10.3, 11.0, 10.2, 10.9), (10.9, 10.95, 10.5, 10.6),
        (10.6, 10.7, 10.2, 10.3), (10.3, 10.8, 10.25, 10.75), (10.75, 11.3, 10.7, 11.25),
        (11.25, 11.35, 10.9, 11.0), (11.0, 11.05, 10.4, 10.5), (10.5, 10.55, 9.9, 10.0),
    ])
    bb = of(e, Kind.BB)[0]
    feed(e, [(10.0, 10.5, 9.95, 10.4)])
    assert bb.state == State.TESTED
    events = feed(e, [(10.4, 10.9, 10.35, 10.8)])
    assert bb.state == State.INVALIDATED
    assert not any(ev.type == "converted" and ev.array is bb for ev in events)


def test_order_block_not_duplicated_by_break_and_displacement():
    e = TFEngine("5", Config(**{**LOOSE, "disp_atr": 0.1}))
    feed(e, [
        (10.0, 10.4, 9.9, 10.3), (10.3, 11.0, 10.2, 10.9), (10.9, 10.95, 10.5, 10.6),
        (10.6, 10.7, 10.2, 10.3), (10.3, 10.8, 10.25, 10.75), (10.75, 11.3, 10.7, 11.25),
        (11.25, 12.0, 11.2, 11.9),
    ])
    assert len(of(e, Kind.OB, Dir.BULL)) == 1


def test_age_expiry():
    e = TFEngine("5", Config(**{**LOOSE, "max_age": 3}))
    feed(e, [(10.0, 11.0, 9.5, 10.8), (10.8, 13.0, 10.7, 12.9), (12.9, 13.5, 12.0, 13.4)])
    fvg = of(e, Kind.FVG, Dir.BULL)[0]
    feed(e, [(13.4, 13.8, 13.3, 13.7)] * 3)
    assert fvg.state == State.FRESH
    events = feed(e, [(13.7, 13.9, 13.6, 13.8)])
    assert fvg.state == State.EXPIRED and fvg.i_end == 6
    assert any(ev.type == "expired" and ev.array is fvg for ev in events)


def test_capacity_expires_oldest():
    e = TFEngine("5", Config(**{**LOOSE, "max_live": 2}))
    feed(e, [
        (10.0, 11.0, 9.5, 10.8), (10.8, 13.0, 10.7, 12.9), (12.9, 13.5, 12.0, 13.4),
        (13.4, 15.5, 13.45, 15.4), (15.4, 17.0, 15.6, 16.9), (16.9, 19.0, 17.1, 18.9),
    ])
    assert len(e.live) <= 2
    oldest = min(e.history, key=lambda a: a.id)
    assert oldest.state == State.EXPIRED


def test_structure_break_sets_bias():
    e = TFEngine("5", Config(**LOOSE))
    events = feed(e, [
        (10.0, 10.4, 9.9, 10.3), (10.3, 11.0, 10.2, 10.9), (10.9, 10.95, 10.5, 10.6),
        (10.6, 10.7, 10.2, 10.3), (10.3, 10.8, 10.25, 10.75), (10.75, 11.3, 10.7, 11.25),
    ])
    bos = [ev for ev in events if ev.type == "bos"]
    assert len(bos) == 1 and bos[0].dir == Dir.BULL and bos[0].price == 11.0
    assert e.bias == 1
