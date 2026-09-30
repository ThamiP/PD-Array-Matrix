"""Core types for the ICT PD Array Matrix.

Everything here mirrors the Pine Script implementation in
``pine/PD_Array_Matrix.pine`` one-to-one so the Python engine can serve as an
executable specification (see ``docs/RULES.md``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Optional


class Kind(IntEnum):
    """PD array kinds. The value is the Pine constant (K_*)."""

    LP = 0  # Liquidity Pool (buyside above highs / sellside below lows)
    RB = 1  # Rejection Block
    OB = 2  # Order Block
    FVG = 3  # Fair Value Gap (``inverted`` flag => Inversion FVG)
    VI = 4  # Volume Imbalance
    BB = 5  # Breaker Block
    MB = 6  # Mitigation Block


KIND_NAME = {
    Kind.LP: "Liquidity Pool",
    Kind.RB: "Rejection Block",
    Kind.OB: "Order Block",
    Kind.FVG: "Fair Value Gap",
    Kind.VI: "Volume Imbalance",
    Kind.BB: "Breaker Block",
    Kind.MB: "Mitigation Block",
}

KIND_SHORT = {
    Kind.LP: "LP",
    Kind.RB: "RB",
    Kind.OB: "OB",
    Kind.FVG: "FVG",
    Kind.VI: "VI",
    Kind.BB: "BB",
    Kind.MB: "MB",
}

# The PD Array Matrix (slide 4). Premium is listed from the top of the dealing
# range down to equilibrium, discount from equilibrium down to the bottom.
PREMIUM_ORDER = [Kind.LP, Kind.RB, Kind.OB, Kind.FVG, Kind.VI, Kind.BB, Kind.MB]
DISCOUNT_ORDER = list(reversed(PREMIUM_ORDER))


def premium_rank(kind: Kind) -> int:
    """1 = extreme premium (Liquidity Pool) ... 7 = closest to EQ (Mitigation Block)."""
    return PREMIUM_ORDER.index(kind) + 1


def discount_rank(kind: Kind) -> int:
    """1 = closest to EQ (Mitigation Block) ... 7 = extreme discount (Liquidity Pool)."""
    return DISCOUNT_ORDER.index(kind) + 1


class Dir(IntEnum):
    BULL = 1  # support / discount array, delivers price up
    BEAR = -1  # resistance / premium array, delivers price down


class State(IntEnum):
    """Lifecycle states. The value is the Pine constant (S_*)."""

    FRESH = 0  # formed, never revisited
    TESTED = 1  # price traded back into the zone, zone still holds
    FILLED = 2  # gap arrays only: wick traded through the whole gap, no close beyond
    SWEPT = 3  # liquidity pools only: wick beyond the level, close back inside
    RUN = 4  # liquidity pools only: TF candle closed beyond the level
    INVALIDATED = 5  # TF candle closed beyond the distal edge
    EXPIRED = 6  # aged out (max_age) or pushed out by max_live


LIVE_STATES = frozenset({State.FRESH, State.TESTED, State.FILLED})


class Transition(IntEnum):
    NONE = 0
    TESTED = 1
    FILLED = 2
    SWEPT = 3
    RUN = 4
    INVALIDATED = 5
    EXPIRED = 6


@dataclass(frozen=True)
class Candle:
    t: int  # open time, epoch milliseconds
    o: float
    h: float
    l: float
    c: float
    tc: Optional[int] = None  # close time, epoch milliseconds

    @property
    def body_top(self) -> float:
        return max(self.o, self.c)

    @property
    def body_bot(self) -> float:
        return min(self.o, self.c)

    @property
    def bull(self) -> bool:
        return self.c > self.o

    @property
    def bear(self) -> bool:
        return self.c < self.o


@dataclass
class PDArray:
    id: int
    tf: str
    kind: Kind
    dir: Dir
    top: float
    bottom: float
    mid: float  # mean threshold / consequent encroachment level
    i_origin: int  # TF candle index the zone is anchored to
    t_origin: int
    i_formed: int  # TF candle index on which the array was confirmed
    state: State = State.FRESH
    taps: int = 0
    ce: bool = False  # mean threshold (OB body 50%) / CE (gap 50%) reached
    inverted: bool = False  # Inversion FVG
    was_inside: bool = False
    touches: int = 1  # liquidity pools: number of equal highs/lows in the pool
    liq_ref: Optional[float] = None  # order blocks: liquidity the leg targeted
    extreme: Optional[float] = None  # order blocks: furthest excursion since origin
    took_liq: bool = False  # order blocks: leg ran liquidity => fails into a breaker
    parent_id: Optional[int] = None
    i_end: Optional[int] = None
    t_end: Optional[int] = None

    @property
    def live(self) -> bool:
        return self.state in LIVE_STATES

    @property
    def proximal(self) -> float:
        """Edge price meets first when approaching the array from the trade side."""
        return self.bottom if self.dir == Dir.BEAR else self.top

    @property
    def distal(self) -> float:
        return self.top if self.dir == Dir.BEAR else self.bottom

    @property
    def label(self) -> str:
        name = KIND_SHORT[self.kind]
        if self.kind == Kind.FVG and self.inverted:
            name = "iFVG"
        if self.kind == Kind.LP:
            name = "BSL" if self.dir == Dir.BEAR else "SSL"
        return name

    def distance(self, price: float) -> float:
        if self.bottom <= price <= self.top:
            return 0.0
        return min(abs(price - self.top), abs(price - self.bottom))

    def overlaps(self, c: Candle) -> bool:
        return c.h >= self.bottom and c.l <= self.top


@dataclass
class Swing:
    i: int
    t: int
    price: float
    broken: bool = False


@dataclass
class Event:
    """Something that happened while processing one TF candle."""

    type: str  # formed | tested | filled | swept | run | invalidated | expired | converted | bos
    tf: str
    i: int
    array: Optional[PDArray] = None
    child: Optional[PDArray] = None  # for "converted": the successor array
    dir: Optional[Dir] = None  # for "bos"
    price: Optional[float] = None  # for "bos"


@dataclass
class Config:
    """Detection + lifecycle parameters (Pine inputs of the same name)."""

    swing_len: int = 2
    atr_len: int = 14
    eq_tol_atr: float = 0.10  # equal highs/lows tolerance (x ATR) for liquidity pools
    rb_wick_pct: float = 0.50  # rejection block: wick >= pct of candle range
    fvg_min_atr: float = 0.10
    vi_min_atr: float = 0.00
    vi_wick_overlap: bool = True
    disp_atr: float = 1.00  # displacement: body >= disp_atr x ATR
    ob_lookback: int = 10
    ob_zone: str = "wick"  # "wick" (full range) or "body"
    max_age: int = 200  # TF candles before a live array expires
    max_live: int = 30  # per timeframe


@dataclass
class SetupConfig:
    """'One Setup for Life' execution model parameters."""

    ctx_window: int = 30  # exec bars a premium/discount HTF touch stays valid
    max_bars: int = 40  # exec bars after the sweep before the setup expires
    stop_mode: str = "sweep"  # "sweep" (at the high/low) or "zone" (above the inversion/breaker)
    stop_buffer: float = 0.0  # price units added beyond the stop
    rr: float = 2.0
    require_macro: bool = True
    macros: str = "0750-0810,0850-0910,0950-1010,1050-1110,1150-1210,1320-1340,1450-1510,1515-1545,1550-1610"
    macro_tz: str = "America/New_York"
    sweep_mode: str = "any"  # "any" (swept or run) or "wick" (swept only)
    mb_as_breaker: bool = False
    daily_reset: bool = True  # setups are today-only: a new trading day clears legs, context, pending
    session_start_min: int = 0  # trading-day start in macro_tz minutes (18*60 for CME futures)


@dataclass
class Trade:
    dir: int  # -1 short, 1 long
    kind: str  # "IFVG" (entry 1) or "Breaker" (entry 2)
    i_signal: int
    stop: float
    zone_id: int
    entry: Optional[float] = None
    target: Optional[float] = None
    i_fill: Optional[int] = None
    outcome: str = "pending"  # pending | open | win | loss | cancelled
    i_exit: Optional[int] = None
    t_signal: Optional[int] = None

    @property
    def risk(self) -> Optional[float]:
        if self.entry is None:
            return None
        return abs(self.stop - self.entry)


@dataclass
class TFSnapshot:
    """One column of the matrix table."""

    tf: str
    range_high: Optional[float]
    range_low: Optional[float]
    eq: Optional[float]
    position: Optional[float]  # 0..1 inside the dealing range
    bias: int
    premium: dict = field(default_factory=dict)  # rank -> PDArray | None
    discount: dict = field(default_factory=dict)
    next_up: Optional[PDArray] = None
    next_down: Optional[PDArray] = None
