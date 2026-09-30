# ICT PD Array Matrix — multi-timeframe lifecycle

A TradingView indicator (Pine Script v6), plus a Python reference engine, that builds
ICT's **PD Array Matrix** on every timeframe you choose. Each PD array runs through a
full lifecycle, and the indicator also runs the **"One Setup for Life"** execution
model: HTF premium array → liquidity sweep → IFVG entry → breaker entry, 1:2.

```
PD Array Matrix      5m ▲         15m ▼       1H ▲          4H ▼          1D ▲
Position             P 81%        P 63%       P 87%         P 91%         P 93%
P1 Liquidity Pool    2266.87 ○    2267.91 ○   2268.77 ○     2271.88 ○     2271.88 ○
P2 Rejection Block   2266.12 ◐    2265.45 ◐   2268.47 ◐     2272.73 ◐     ·
P3 Order Block       2263.67 ◐    2264.53 ○   2261.26 ◐     2255.18 ◐     2247.43 ◐
P4 Fair Value Gap    2262.93 ●    i2261.45 ◐  2261.51 ○     i2260.87 ●    i2227.37 ◐
P5 Volume Imbalance  2266.96 ●    ·           ·             ·             ·
P6 Breaker Block     2263.30 ◐    ·           ·             ·             ·
P7 Mitigation Block  ·            ·           ·             ·             ·
EQ                   2261.78      2261.64     2247.47       2223.12       2212.70
D1 Mitigation Block  ·            ·           ·             ·             ·
D2 Breaker Block     2260.58 ◐    2261.39 ◐   ·             ·             ·
D3 Volume Imbalance  ·            ·           ·             ·             ·
D4 Fair Value Gap    2258.85 ○    2255.11 ●   2249.66 ○     2222.39 ○     ·
D5 Order Block       2261.51 ○    2258.55 ◐   2236.07 ○     2192.14 ○     2214.80 ◐
D6 Rejection Block   2256.27 ◐    2227.46 ◐   2179.35 ◐     ·             ·
D7 Liquidity Pool    2259.41 ○    2255.37 ○   2226.16 ○     2174.36 ○     2153.52 ○
Next ▲               BB 2263.30   OB 2264.53  RB 2268.47    OB 2270.16    FVG 2270.69
Next ▼               FVG 2262.24  BB 2261.39  iFVG 2258.63  iFVG 2258.63  OB 2235.94
```
(output of `python -m pdmatrix` on synthetic data; the Pine table has the same layout)
`○` fresh · `◐` tested · `●` filled · `i` inversion FVG. Hover a cell in TradingView for
the array's full lifecycle: state, taps, 50% reached, touches, whether the OB took
liquidity, and age.

## What's in here

| Path | What it is |
|---|---|
| `pine/PD_Array_Matrix.pine` | The TradingView indicator: matrix table, chart zones, setup model, alerts |
| `docs/RULES.md` | The rulebook: every rule traced to the source slides, lifecycle state machines |
| `pdmatrix/` | Python reference engine (no dependencies), mirroring the Pine logic rule for rule |
| `tests/` | 32 pytest cases that pin down detection, lifecycle, MTF aggregation, the matrix, and the setup model |

## The rules in one screen

**Matrix** (slide 4). Premium, top to EQ: Liquidity Pool, Rejection Block, Order Block,
FVG, Volume Imbalance, Breaker, Mitigation Block. Discount mirrors it, from EQ down.
Premium cells hold bearish arrays above the timeframe's EQ, and discount cells hold
bullish arrays below it.

**Lifecycle, per timeframe, on that timeframe's candle close:**

```
Fresh ──tap──▶ Tested ──wick through gap──▶ Filled
  │               │                            │
  └───────────────┴──── close beyond ──────────┴──▶ Invalidated ──▶ successor
                                                        FVG → iFVG
                                                        OB  → Breaker (leg took liquidity)
                                                        OB  → Mitigation Block (it didn't)
Liquidity pool: Fresh ──wick beyond, close back──▶ Swept  |  ──close beyond──▶ Run
Anything: too old (maxAge) or over capacity (maxLive) ──▶ Expired
```

**One Setup for Life** (the setup chart):

- **Context:** HTF premium PD array touched.
- **01** Buyside liquidity swept.
- **02** Bullish FVG closed through, so it becomes an IFVG. Enter at the next open.
- **03** Bullish OB fails into a breaker. Enter at the next open.
- **Stop:** at the sweep high, or above the zone.
- **Target:** 1:2.
- **Timing:** only inside the NY macro windows.
- Longs mirror all of this.

The full details, and every choice the slides leave open, are in
[`docs/RULES.md`](docs/RULES.md).

## TradingView

1. Open the Pine Editor, paste `pine/PD_Array_Matrix.pine`, and click **Add to chart**.
2. **Timeframes:** the chart timeframe plus five HTF slots (default 15, 60, 240, D, W).
   Each slot has a column toggle and a draw toggle. Slots at or below the chart
   timeframe are skipped.
3. **Alerts:** create a single alert on the indicator with *Any alert() function call*.
   It covers setup entries, inversions and conversions, and (optionally) sweeps and new
   arrays.

Notes:

- HTF candles are aggregated from chart bars. This avoids repainting and keeps every
  lifecycle on its own timeframe's close. The trade-off is that HTF depth equals the
  chart history loaded. On a 1-minute chart, weekly arrays only come from the last few
  weeks. Use a higher chart timeframe, or load more history, for deep HTF context.
- Lifecycle transitions are confirmed on candle close. The chart timeframe updates on
  bar close, and an HTF updates when its next period opens.
- The default drawing budget, 6 timeframes × (30 live + 6 ended) arrays plus 20 trades,
  stays within TradingView's 500-box limit. If you raise *Max live arrays per TF* or
  *Keep ended arrays* a lot, the oldest boxes get recycled.

## Python reference engine

Useful for backtesting and for checking the rules on your own data.

```bash
python -m pdmatrix candles.csv --tf 5 --htf 15,60,240,D,W      # CSV: time,open,high,low,close
python -m pytest                                               # 32 tests
```

```python
from pdmatrix import MatrixEngine, Config, SetupConfig
m = MatrixEngine("5", ["15", "60", "240", "D"], Config(), SetupConfig(rr=2.0)).run(candles)
print(m.render())                 # the matrix table
m.engines["60"].live              # live 1H PD arrays with state / taps / ce / touches
m.sequence(direction=1)           # every live array above price, in delivery order
m.setup.trades, m.setup.stats()   # One Setup for Life signals and outcomes
```

`Candle(t, o, h, l, c, tc)` takes epoch-millisecond open and close times. Daily, weekly
and monthly buckets use New York time by default. Pass `session_start_min=18*60` for
futures sessions that open at 18:00.
