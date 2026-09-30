from pdmatrix import Config, Dir, Kind, MatrixEngine, SetupConfig, SetupModel, TFEngine
from pdmatrix.model import PDArray

from .helpers import FIVE_MIN, T0, bars, ny_ms

CFG = Config(swing_len=1, fvg_min_atr=0.0, vi_min_atr=0.0, disp_atr=100.0)

# Bearish "One Setup for Life":
#   buyside liquidity at 103 is run (01), the rally's bullish FVG is closed
#   through (02, iFVG entry), then the bullish OB fails into a breaker (03).
SHORT_PATH = [
    (100.0, 101.0, 99.5, 100.5),
    (100.5, 103.0, 100.4, 102.8),  # swing high 103 -> buyside liquidity
    (102.8, 102.9, 101.5, 101.8),
    (101.8, 102.0, 100.8, 101.0),  # last down candle -> bullish OB 100.8..102.0
    (101.0, 102.5, 100.9, 102.4),
    (102.4, 104.5, 102.3, 104.4),  # runs 103 (sweep), bullish FVG 102.0..102.3
    (104.4, 104.6, 102.45, 104.0),  # high of the move 104.6 (no second FVG)
    (104.0, 104.1, 102.1, 102.2),  # taps the FVG
    (102.2, 102.3, 101.5, 101.6),  # 8: closes below the FVG -> iFVG -> entry 1 signal
    (101.6, 101.8, 101.0, 101.2),  # 9: entry 1 filled at the open (101.6)
    (101.2, 101.3, 100.1, 100.4),  # 10: closes below the OB -> breaker -> entry 2 signal
    (100.4, 100.5, 95.0, 95.2),  # 11: entry 2 filled at 100.4, entry 1 target (95.6) hit
]


def run(rows, scfg, start=T0, context=True):
    eng = TFEngine("5", CFG)
    model = SetupModel(scfg, FIVE_MIN, lambda d, c: context)
    for i, c in enumerate(bars(rows, start=start)):
        model.on_bar(i, c, eng.process(c))
    return eng, model


def test_short_setup_sweep_inversion_breaker():
    eng, model = run(SHORT_PATH, SetupConfig(require_macro=False))
    assert [t.kind for t in model.trades] == ["IFVG", "Breaker"]
    t1, t2 = model.trades
    assert t1.dir == -1 and t1.i_signal == 8 and t1.i_fill == 9
    assert t1.entry == 101.6 and t1.stop == 104.6  # stop at the high of the sweep
    assert round(t1.target, 6) == 95.6  # 1:2
    assert t1.outcome == "win" and t1.i_exit == 11
    assert t2.i_signal == 10 and t2.entry == 100.4 and t2.stop == 104.6
    assert round(t2.target, 6) == 92.0 and t2.outcome == "open"
    assert model.stage(-1) == 0  # the completed leg is cleared on the next bar
    breaker = next(a for a in eng.history if a.id == t2.zone_id)
    assert breaker.kind == Kind.BB and breaker.dir == Dir.BEAR


def test_zone_stop_mode_uses_the_inversion_zone():
    _, model = run(SHORT_PATH, SetupConfig(require_macro=False, stop_mode="zone"))
    t1 = model.trades[0]
    assert t1.stop == 102.3 and round(t1.target, 6) == 100.2
    assert t1.outcome == "win" and t1.i_exit == 10


def test_macro_window_gate():
    # starting 09:10 NY puts both signal candles (09:50, 10:00) inside the 09:50-10:10 macro
    _, model = run(SHORT_PATH, SetupConfig(require_macro=True))
    assert len(model.trades) == 2
    # starting 12:20 NY puts them at 13:00 / 13:10: outside every macro
    _, model = run(SHORT_PATH, SetupConfig(require_macro=True), start=ny_ms(2026, 9, 28, 12, 20))
    assert model.trades == []


def test_no_context_no_setup():
    _, model = run(SHORT_PATH, SetupConfig(require_macro=False), context=False)
    assert model.trades == []


def test_wick_only_sweep_mode_ignores_a_run():
    _, model = run(SHORT_PATH, SetupConfig(require_macro=False, sweep_mode="wick"))
    assert model.trades == []


def _mirror(rows, pivot=200.0):
    return [(pivot - o, pivot - l, pivot - h, pivot - c) for o, h, l, c in rows]


def test_long_setup_is_the_mirror_image():
    _, model = run(_mirror(SHORT_PATH), SetupConfig(require_macro=False))
    assert [(t.dir, t.kind) for t in model.trades] == [(1, "IFVG"), (1, "Breaker")]
    t1 = model.trades[0]
    assert t1.entry == 200 - 101.6 and t1.stop == 200 - 104.6
    assert round(t1.target, 6) == round(200 - 95.6, 6) and t1.outcome == "win"


def test_context_requires_htf_premium_array_touch():
    m = MatrixEngine("5", ["60"], CFG, SetupConfig(require_macro=False))
    htf = m.engines["60"]
    htf.trail_high, htf.trail_low = 110.0, 90.0  # EQ 100
    zone = PDArray(id=99, tf="60", kind=Kind.OB, dir=Dir.BEAR, top=104.0, bottom=103.0, mid=103.5,
                   i_origin=0, t_origin=0, i_formed=0)
    htf.live = [zone]
    touch, miss = bars([(102.5, 103.2, 102.4, 102.9), (101.0, 102.0, 100.8, 101.5)])
    assert m._context(-1, touch) and not m._context(-1, miss)
    assert not m._context(1, touch)  # bearish premium array never arms a long
    zone.dir = Dir.BULL
    assert not m._context(-1, touch)
    zone.dir, zone.mid = Dir.BEAR, 99.0  # below EQ: not a premium array
    assert not m._context(-1, touch)


def test_same_candle_inversion_and_breaker_are_order_independent():
    from pdmatrix import Candle, Event

    def arr(i, kind, inverted=False):
        return PDArray(id=i, tf="5", kind=kind, dir=Dir.BEAR, top=11.0, bottom=10.0, mid=10.5,
                       i_origin=0, t_origin=0, i_formed=0, inverted=inverted)

    sweep = arr(1, Kind.LP)
    model = SetupModel(SetupConfig(require_macro=False), FIVE_MIN)
    c0, c1, c2 = bars([(10.5, 12.0, 10.4, 11.8), (11.8, 11.9, 9.5, 9.6), (9.6, 9.7, 9.0, 9.1)])
    model.on_bar(0, c0, [Event("swept", "5", 0, sweep)])
    brk, ifvg = arr(2, Kind.BB), arr(3, Kind.FVG, inverted=True)
    # breaker event listed first: entry 1 (IFVG) must still be taken before entry 2
    model.on_bar(1, c1, [Event("converted", "5", 1, arr(4, Kind.OB), child=brk),
                         Event("converted", "5", 1, arr(5, Kind.FVG), child=ifvg)])
    assert [(t.kind, t.zone_id) for t in model.trades] == [("IFVG", 3), ("Breaker", 2)]
    assert all(t.stop == 12.0 for t in model.trades)


def test_setups_are_today_only():
    # sweep at 23:50, inversion at 00:05: the leg does not survive the new trading day
    late = ny_ms(2026, 9, 28, 23, 25)
    _, model = run(SHORT_PATH, SetupConfig(require_macro=False), start=late)
    assert model.trades == []
    _, model = run(SHORT_PATH, SetupConfig(require_macro=False, daily_reset=False), start=late)
    assert [t.kind for t in model.trades] == ["IFVG", "Breaker"]
    # an 18:00 session start keeps 23:25-00:20 inside one trading day
    _, model = run(SHORT_PATH, SetupConfig(require_macro=False, session_start_min=18 * 60), start=late)
    assert [t.kind for t in model.trades] == ["IFVG", "Breaker"]
