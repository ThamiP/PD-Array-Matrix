"""'One Setup for Life' execution model (bearish delivery, mirrored for longs).

    0. Context   price trades into a higher-timeframe Premium PD array
                 (bearish array above that TF's equilibrium)
    1. Sweep     execution-TF buyside liquidity pool is swept / run
    2. Entry 1   a bullish FVG is closed through -> Inversion FVG.
                 "IFVG: close below the low. Enter at the next candle's open."
    3. Entry 2   a bullish Order Block fails after running liquidity -> Breaker.
                 "Breaker: close below the low. Enter at the next candle's open."
    Stop         at the sweep high, or above the inversion / breaker zone
    Target       1:2 risk-to-reward
    Timing       signal candle must overlap an ICT macro window (New York time)
    Today only   a new trading day clears any leg, context and unfilled signal
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .model import Candle, Dir, Event, Kind, SetupConfig, Trade
from .timeframes import bucket_key, in_macro, parse_macros

ContextFn = Callable[[int, Candle], bool]


@dataclass
class _Leg:
    dir: int
    i_sweep: int
    extreme: float
    stage: int = 1  # 1 = swept, 2 = entry 1 taken, 3 = done


class SetupModel:
    def __init__(self, cfg: Optional[SetupConfig] = None, tf_ms: int = 60_000,
                 context_fn: Optional[ContextFn] = None):
        self.cfg = cfg or SetupConfig()
        self.tf_ms = tf_ms
        self.context_fn = context_fn or (lambda d, c: True)
        self.windows = parse_macros(self.cfg.macros)
        self.ctx_i: Dict[int, Optional[int]] = {-1: None, 1: None}
        self.legs: Dict[int, Optional[_Leg]] = {-1: None, 1: None}
        self.trades: List[Trade] = []
        self._pending: List[Trade] = []
        self._day = None

    # ------------------------------------------------------------------
    def macro_ok(self, c: Candle) -> bool:
        if not self.cfg.require_macro:
            return True
        tc = c.tc if c.tc is not None else c.t + self.tf_ms
        return in_macro(c.t, tc, self.windows, self.cfg.macro_tz)

    def stage(self, d: int) -> int:
        leg = self.legs[d]
        return 0 if leg is None else leg.stage

    # ------------------------------------------------------------------
    def on_bar(self, i: int, c: Candle, events: List[Event]) -> List[Trade]:
        """Process one closed execution-TF candle (after the TF engine ran on it).

        Returns trades that were *signalled* on this bar (filled next bar).
        """
        cfg = self.cfg
        if cfg.daily_reset:
            day = bucket_key(c.t, "D", cfg.macro_tz, cfg.session_start_min)
            if self._day is not None and day != self._day:
                # today-only setups: nothing carries over from the previous trading day
                self.legs = {-1: None, 1: None}
                self.ctx_i = {-1: None, 1: None}
                for tr in self._pending:
                    tr.outcome = "cancelled"
                self._pending = []
            self._day = day
        self._fill_and_manage(i, c)

        new: List[Trade] = []
        for d in (-1, 1):
            if self.context_fn(d, c):
                self.ctx_i[d] = i
            # expire an old leg first so a fresh sweep can replace it
            leg = self.legs[d]
            if leg is not None and (leg.stage >= 3 or i - leg.i_sweep > cfg.max_bars):
                self.legs[d] = leg = None

            pool_dir = Dir.BEAR if d == -1 else Dir.BULL
            for e in events:
                if e.array is None or e.array.kind != Kind.LP or e.array.dir != pool_dir:
                    continue
                if e.type == "swept" or (e.type == "run" and cfg.sweep_mode == "any"):
                    ctx = self.ctx_i[d]
                    if ctx is not None and i - ctx <= cfg.ctx_window and (leg is None or leg.stage == 1):
                        leg = _Leg(d, i, c.h if d == -1 else c.l)
                        self.legs[d] = leg
            if leg is None:
                continue
            if leg.stage == 1:
                leg.extreme = max(leg.extreme, c.h) if d == -1 else min(leg.extreme, c.l)

            # the first conversion of each type on this candle counts; entry 1 is
            # always evaluated before entry 2 so same-candle order is irrelevant
            children = [e.child for e in events
                        if e.type == "converted" and e.child is not None and e.child.dir == Dir(d)]
            ifvg = next((ch for ch in children if ch.kind == Kind.FVG and ch.inverted), None)
            brk = next((ch for ch in children
                        if ch.kind == Kind.BB or (cfg.mb_as_breaker and ch.kind == Kind.MB)), None)
            ok = self.macro_ok(c)
            if ifvg is not None and leg.stage == 1 and ok:
                new.append(self._signal(d, "IFVG", i, c, leg, ifvg))
                leg.stage = 2
            if brk is not None and leg.stage in (1, 2) and ok:
                new.append(self._signal(d, "Breaker", i, c, leg, brk))
                leg.stage = 3
        return new

    def _signal(self, d: int, kind: str, i: int, c: Candle, leg: _Leg, zone) -> Trade:
        cfg = self.cfg
        if cfg.stop_mode == "zone":
            stop = zone.top if d == -1 else zone.bottom
        else:
            stop = leg.extreme
        stop = stop + cfg.stop_buffer if d == -1 else stop - cfg.stop_buffer
        tr = Trade(dir=d, kind=kind, i_signal=i, stop=stop, zone_id=zone.id, t_signal=c.t)
        self.trades.append(tr)
        self._pending.append(tr)
        return tr

    def _fill_and_manage(self, i: int, c: Candle) -> None:
        for tr in self._pending:
            tr.entry = c.o
            tr.i_fill = i
            risk = (tr.stop - c.o) * (-tr.dir)
            if risk <= 0:
                tr.outcome = "cancelled"
                continue
            tr.target = c.o + tr.dir * self.cfg.rr * risk
            tr.outcome = "open"
        self._pending = []
        for tr in self.trades:
            if tr.outcome != "open":
                continue
            stop_hit = c.h >= tr.stop if tr.dir == -1 else c.l <= tr.stop
            tgt_hit = c.l <= tr.target if tr.dir == -1 else c.h >= tr.target
            if stop_hit:  # conservative: stop first when both print in one candle
                tr.outcome, tr.i_exit = "loss", i
            elif tgt_hit:
                tr.outcome, tr.i_exit = "win", i

    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, float]:
        wins = sum(1 for t in self.trades if t.outcome == "win")
        losses = sum(1 for t in self.trades if t.outcome == "loss")
        closed = wins + losses
        return {
            "signals": len(self.trades),
            "wins": wins,
            "losses": losses,
            "open": sum(1 for t in self.trades if t.outcome == "open"),
            "win_rate": wins / closed if closed else 0.0,
            "net_r": wins * self.cfg.rr - losses,
        }
