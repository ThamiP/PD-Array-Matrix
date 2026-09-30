"""Multi-timeframe PD Array Matrix: one lifecycle engine per timeframe."""

from __future__ import annotations

from typing import Dict, List, Optional

from .engine import TFEngine
from .model import (
    DISCOUNT_ORDER,
    KIND_NAME,
    PREMIUM_ORDER,
    Candle,
    Config,
    Dir,
    Event,
    Kind,
    PDArray,
    SetupConfig,
    State,
    TFSnapshot,
)
from .setup import SetupModel
from .timeframes import Aggregator, tf_name, tf_seconds

STATE_GLYPH = {State.FRESH: "○", State.TESTED: "◐", State.FILLED: "●"}


class MatrixEngine:
    """Feeds execution-TF candles, builds higher TFs, runs every lifecycle + the setup model."""

    def __init__(self, exec_tf: str, htfs: List[str], cfg: Optional[Config] = None,
                 setup_cfg: Optional[SetupConfig] = None, tz: str = "America/New_York",
                 session_start_min: int = 0):
        self.exec_tf = exec_tf
        self.cfg = cfg or Config()
        ids = [0]
        self.engines: Dict[str, TFEngine] = {exec_tf: TFEngine(exec_tf, self.cfg, ids)}
        self.aggs: Dict[str, Aggregator] = {}
        exec_s = tf_seconds(exec_tf)
        self.skipped: List[str] = []
        for tf in htfs:
            if tf_seconds(tf) <= exec_s or tf in self.engines:
                self.skipped.append(tf)  # lower/equal TFs cannot be built from the chart
                continue
            self.engines[tf] = TFEngine(tf, self.cfg, ids)
            self.aggs[tf] = Aggregator(tf, tz, session_start_min)
        self.setup = SetupModel(setup_cfg, exec_s * 1000, self._context)
        self.events: List[Event] = []
        self.last: Optional[Candle] = None

    @property
    def exec(self) -> TFEngine:
        return self.engines[self.exec_tf]

    @property
    def timeframes(self) -> List[str]:
        return sorted(self.engines, key=tf_seconds)

    # ------------------------------------------------------------------
    def _context(self, d: int, c: Candle) -> bool:
        """Price is trading inside a live HTF premium (short) / discount (long) PD array."""
        want = Dir.BEAR if d == -1 else Dir.BULL
        for tf, eng in self.engines.items():
            if tf == self.exec_tf:
                continue
            for a in eng.live:
                if a.dir != want:
                    continue
                if d == -1 and not eng.in_premium(a):
                    continue
                if d == 1 and not eng.in_discount(a):
                    continue
                if a.overlaps(c):
                    return True
        return False

    def on_bar(self, c: Candle) -> List[Event]:
        out: List[Event] = []
        for tf, agg in self.aggs.items():
            done = agg.push(c)
            if done is not None:
                out.extend(self.engines[tf].process(done))
        exec_events = self.exec.process(c)
        out.extend(exec_events)
        self.setup.on_bar(self.exec.n - 1, c, exec_events)
        self.events.extend(out)
        self.last = c
        return out

    def run(self, candles) -> "MatrixEngine":
        for c in candles:
            self.on_bar(c)
        return self

    # ------------------------------------------------------------------
    def snapshot(self, price: Optional[float] = None) -> Dict[str, TFSnapshot]:
        if price is None:
            price = self.last.c if self.last else 0.0
        return {tf: self.engines[tf].snapshot(price) for tf in self.timeframes}

    def sequence(self, price: Optional[float] = None, direction: int = 1) -> List[PDArray]:
        """All live arrays across TFs in the order price would deliver into them."""
        if price is None:
            price = self.last.c if self.last else 0.0
        arrays = [a for e in self.engines.values() for a in e.live]
        if direction > 0:
            return sorted((a for a in arrays if a.bottom > price), key=lambda a: a.bottom)
        return sorted((a for a in arrays if a.top < price), key=lambda a: -a.top)

    def render(self, price: Optional[float] = None, digits: int = 2) -> str:
        """Text rendering of the matrix (same layout as the Pine table)."""
        snaps = self.snapshot(price)
        tfs = self.timeframes
        rows: List[List[str]] = []

        def cell(a: Optional[PDArray]) -> str:
            if a is None:
                return "·"
            tag = "i" if a.kind == Kind.FVG and a.inverted else ""
            return f"{tag}{a.proximal:.{digits}f} {STATE_GLYPH.get(a.state, '')}"

        rows.append(["PD Array Matrix"] + [f"{tf_name(tf)} {'▲▼'[snaps[tf].bias < 0] if snaps[tf].bias else ''}".rstrip()
                                           for tf in tfs])
        pos = []
        for tf in tfs:
            p = snaps[tf].position
            pos.append("-" if p is None else f"{'P' if p > 0.5 else 'D'} {p * 100:.0f}%")
        rows.append(["Position"] + pos)
        for r, kind in enumerate(PREMIUM_ORDER, start=1):
            rows.append([f"P{r} {KIND_NAME[kind]}"] + [cell(snaps[tf].premium[r]) for tf in tfs])
        rows.append(["EQ"] + ["-" if snaps[tf].eq is None else f"{snaps[tf].eq:.{digits}f}" for tf in tfs])
        for r, kind in enumerate(DISCOUNT_ORDER, start=1):
            rows.append([f"D{r} {KIND_NAME[kind]}"] + [cell(snaps[tf].discount[r]) for tf in tfs])
        rows.append(["Next ▲"] + [("-" if s.next_up is None else f"{s.next_up.label} {s.next_up.bottom:.{digits}f}")
                                   for s in (snaps[tf] for tf in tfs)])
        rows.append(["Next ▼"] + [("-" if s.next_down is None else f"{s.next_down.label} {s.next_down.top:.{digits}f}")
                                     for s in (snaps[tf] for tf in tfs)])
        widths = [max(len(r[k]) for r in rows) for k in range(len(rows[0]))]
        return "\n".join("  ".join(v.ljust(w) for v, w in zip(r, widths)) for r in rows)
