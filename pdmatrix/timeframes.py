"""Timeframe helpers: parsing, HTF candle aggregation and ICT macro windows.

Higher timeframe candles are built from the execution (chart) timeframe the
same way the Pine script does it with ``timeframe.change()``: when a chart bar
opens a new HTF period the previous HTF candle is complete and gets processed.
The first (partial) HTF period is always discarded.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from .model import Candle

_MONTH_SECONDS = 2628000  # TradingView's nominal month


def tf_seconds(tf: str) -> int:
    tf = tf.strip().upper()
    unit = tf[-1]
    if unit in "DWM":
        mult = int(tf[:-1] or 1)
        return mult * {"D": 86400, "W": 604800, "M": _MONTH_SECONDS}[unit]
    if unit == "H":
        return int(tf[:-1] or 1) * 3600
    return int(tf) * 60


def tf_name(tf: str) -> str:
    s = tf_seconds(tf)
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}H"
    if s < 604800:
        return f"{s // 86400}D"
    if s < _MONTH_SECONDS:
        return f"{s // 604800}W"
    return f"{round(s / _MONTH_SECONDS)}M"


def bucket_key(t_ms: int, tf: str, tz: str = "America/New_York", session_start_min: int = 0) -> Tuple:
    """Identifies the HTF period a timestamp belongs to."""
    local = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).astimezone(ZoneInfo(tz))
    shifted = local - timedelta(minutes=session_start_min)
    tfu = tf.strip().upper()
    unit = tfu[-1]
    if unit == "M":
        mult = int(tfu[:-1] or 1)
        return ("M", shifted.year, (shifted.month - 1) // mult)
    if unit == "W":
        mult = int(tfu[:-1] or 1)
        iso = shifted.date().isocalendar()
        return ("W", iso[0], (iso[1] - 1) // mult)
    if unit == "D":
        mult = int(tfu[:-1] or 1)
        return ("D", shifted.date().toordinal() // mult)
    minutes = tf_seconds(tf) // 60
    mod = shifted.hour * 60 + shifted.minute
    return ("I", shifted.date().toordinal(), mod // minutes)


class Aggregator:
    """Builds closed HTF candles from a stream of chart candles."""

    def __init__(self, tf: str, tz: str = "America/New_York", session_start_min: int = 0):
        self.tf = tf
        self.tz = tz
        self.session_start_min = session_start_min
        self._key: Optional[Tuple] = None
        self._forming: Optional[Candle] = None
        self._first = True

    @property
    def forming(self) -> Optional[Candle]:
        return self._forming

    def push(self, c: Candle) -> Optional[Candle]:
        """Feed one chart candle; returns the HTF candle that just completed, if any."""
        key = bucket_key(c.t, self.tf, self.tz, self.session_start_min)
        done = None
        if self._key is None or key != self._key:
            if self._forming is not None and not self._first:
                done = self._forming
            if self._forming is not None:
                self._first = False
            self._key = key
            self._forming = Candle(c.t, c.o, c.h, c.l, c.c, c.tc)
        else:
            f = self._forming
            self._forming = Candle(f.t, f.o, max(f.h, c.h), min(f.l, c.l), c.c, c.tc)
        return done


def parse_macros(spec: str) -> List[Tuple[int, int]]:
    """"0950-1010,1320-1340" -> [(590, 610), (800, 820)] (minutes of day)."""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        a, b = part.split("-")
        out.append((int(a[:2]) * 60 + int(a[2:]), int(b[:2]) * 60 + int(b[2:])))
    return out


def in_macro(t_ms: int, tc_ms: int, windows: List[Tuple[int, int]], tz: str = "America/New_York") -> bool:
    """True when the bar [t, tc) overlaps any macro window. Bars >= 1 day always pass."""
    dur = (tc_ms - t_ms) // 60000
    if dur >= 1440:
        return True
    local = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).astimezone(ZoneInfo(tz))
    m0 = local.hour * 60 + local.minute
    return any(m0 < we and m0 + dur > ws for ws, we in windows)
