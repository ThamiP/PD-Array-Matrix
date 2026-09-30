"""Single-timeframe PD array engine: detection + lifecycle.

``TFEngine.process`` is called once per *closed* candle of its timeframe. The
order of operations is part of the spec and matches the Pine implementation:

1. ATR update
2. lifecycle step for every live array (arrays born on this candle are not stepped)
3. structure breaks against the last swing high / low
4. Fair Value Gap and Volume Imbalance detection
5. Order Block triggers (structure break or displacement)
6. swing confirmation -> Liquidity Pools and Rejection Blocks
7. dealing range (trailing extremes) and capacity pruning
"""

from __future__ import annotations

from typing import List, Optional

from .model import (
    DISCOUNT_ORDER,
    PREMIUM_ORDER,
    Candle,
    Config,
    Dir,
    Event,
    Kind,
    PDArray,
    State,
    Swing,
    TFSnapshot,
    Transition,
)

_TRANSITION_EVENT = {
    Transition.TESTED: "tested",
    Transition.FILLED: "filled",
    Transition.SWEPT: "swept",
    Transition.RUN: "run",
    Transition.INVALIDATED: "invalidated",
    Transition.EXPIRED: "expired",
}


def step(a: PDArray, c: Candle) -> Transition:
    """Advance one array's lifecycle with one closed candle of its timeframe."""
    if a.kind == Kind.LP:
        if a.dir == Dir.BEAR:  # buyside liquidity resting above a high
            if c.h > a.top:
                a.state = State.RUN if c.c > a.top else State.SWEPT
                return Transition.RUN if a.state == State.RUN else Transition.SWEPT
        else:  # sellside liquidity resting below a low
            if c.l < a.bottom:
                a.state = State.RUN if c.c < a.bottom else State.SWEPT
                return Transition.RUN if a.state == State.RUN else Transition.SWEPT
        return Transition.NONE

    if a.kind == Kind.OB:
        if a.dir == Dir.BULL:
            a.extreme = c.h if a.extreme is None else max(a.extreme, c.h)
            if a.liq_ref is not None and a.extreme > a.liq_ref:
                a.took_liq = True
        else:
            a.extreme = c.l if a.extreme is None else min(a.extreme, c.l)
            if a.liq_ref is not None and a.extreme < a.liq_ref:
                a.took_liq = True

    if a.dir == Dir.BULL:
        inside = c.l <= a.top
        break_close = c.c < a.bottom
        through = c.l < a.bottom
        ce = c.l <= a.mid
    else:
        inside = c.h >= a.bottom
        break_close = c.c > a.top
        through = c.h > a.top
        ce = c.h >= a.mid

    if break_close:
        a.state = State.INVALIDATED
        return Transition.INVALIDATED

    tr = Transition.NONE
    if inside:
        if not a.was_inside:
            a.taps += 1
        if ce:
            a.ce = True
        if a.kind in (Kind.FVG, Kind.VI) and through and a.state != State.FILLED:
            a.state = State.FILLED
            tr = Transition.FILLED
        elif a.state == State.FRESH:
            a.state = State.TESTED
            tr = Transition.TESTED
    a.was_inside = inside
    return tr


class TFEngine:
    """Detects PD arrays on one timeframe and runs their lifecycle."""

    def __init__(self, tf: str, cfg: Optional[Config] = None, id_seed: Optional[List[int]] = None):
        self.tf = tf
        self.cfg = cfg or Config()
        self.candles: List[Candle] = []
        self.n = 0
        self.atr: Optional[float] = None
        self.live: List[PDArray] = []
        self.history: List[PDArray] = []  # every array ever created, in creation order
        self.highs: List[Swing] = []
        self.lows: List[Swing] = []
        self.last_high: Optional[Swing] = None
        self.last_low: Optional[Swing] = None
        self.trail_high: Optional[float] = None
        self.trail_low: Optional[float] = None
        self.t_trail_high: Optional[int] = None
        self.t_trail_low: Optional[int] = None
        self.bias = 0  # direction of the last structure break
        self._last_ob_origin = {Dir.BULL: -1, Dir.BEAR: -1}
        self._ids = id_seed if id_seed is not None else [0]

    # ------------------------------------------------------------------ utils
    def at(self, i: int) -> Candle:
        return self.candles[i]

    def _new(self, **kw) -> PDArray:
        self._ids[0] += 1
        a = PDArray(id=self._ids[0], tf=self.tf, **kw)
        self.live.append(a)
        self.history.append(a)
        return a

    def _end(self, a: PDArray, c: Candle, i: int) -> None:
        a.i_end = i
        a.t_end = c.tc if c.tc is not None else c.t

    @property
    def eq(self) -> Optional[float]:
        if self.trail_high is None or self.trail_low is None:
            return None
        return (self.trail_high + self.trail_low) / 2.0

    def in_premium(self, a: PDArray) -> bool:
        eq = self.eq
        return eq is not None and a.mid > eq

    def in_discount(self, a: PDArray) -> bool:
        eq = self.eq
        return eq is not None and a.mid < eq

    def position(self, price: float) -> Optional[float]:
        if self.trail_high is None or self.trail_low is None or self.trail_high <= self.trail_low:
            return None
        return (price - self.trail_low) / (self.trail_high - self.trail_low)

    # ---------------------------------------------------------------- process
    def process(self, c: Candle) -> List[Event]:
        cfg = self.cfg
        i = self.n
        self.candles.append(c)
        self.n += 1
        events: List[Event] = []

        # 1. ATR (running mean until atr_len candles, Wilder smoothing after)
        prev = self.candles[i - 1] if i > 0 else None
        tr = c.h - c.l if prev is None else max(c.h - c.l, abs(c.h - prev.c), abs(c.l - prev.c))
        if self.atr is None:
            self.atr = tr
        elif self.n <= cfg.atr_len:
            self.atr = (self.atr * (self.n - 1) + tr) / self.n
        else:
            self.atr = (self.atr * (cfg.atr_len - 1) + tr) / cfg.atr_len
        atr = self.atr

        # 2. lifecycle of everything already on the books
        for a in list(self.live):
            t = step(a, c)
            if t != Transition.NONE:
                events.append(Event(_TRANSITION_EVENT[t], self.tf, i, a))
            if t in (Transition.SWEPT, Transition.RUN, Transition.INVALIDATED):
                self._end(a, c, i)
            if t == Transition.INVALIDATED:
                child = self._successor(a, i)
                if child is not None:
                    events.append(Event("converted", self.tf, i, a, child=child))
            elif a.live and i - a.i_formed > cfg.max_age:
                a.state = State.EXPIRED
                self._end(a, c, i)
                events.append(Event("expired", self.tf, i, a))
        self.live = [a for a in self.live if a.live]

        # 3. structure
        bos_up = bos_dn = False
        if self.last_high is not None and not self.last_high.broken and c.c > self.last_high.price:
            self.last_high.broken = True
            bos_up = True
            self.bias = 1
            events.append(Event("bos", self.tf, i, dir=Dir.BULL, price=self.last_high.price))
        if self.last_low is not None and not self.last_low.broken and c.c < self.last_low.price:
            self.last_low.broken = True
            bos_dn = True
            self.bias = -1
            events.append(Event("bos", self.tf, i, dir=Dir.BEAR, price=self.last_low.price))

        # 4. Fair Value Gaps (3 candles) and Volume Imbalances (2 candles)
        bull_disp = bear_disp = False
        if i >= 2:
            c0, c1 = self.at(i - 2), self.at(i - 1)
            big = abs(c1.c - c1.o) >= cfg.disp_atr * atr
            if c.l > c0.h and c.l - c0.h >= cfg.fvg_min_atr * atr:
                a = self._new(kind=Kind.FVG, dir=Dir.BULL, top=c.l, bottom=c0.h, mid=(c.l + c0.h) / 2,
                              i_origin=i - 1, t_origin=c1.t, i_formed=i)
                events.append(Event("formed", self.tf, i, a))
                bull_disp = big and c1.c > c1.o
            if c.h < c0.l and c0.l - c.h >= cfg.fvg_min_atr * atr:
                a = self._new(kind=Kind.FVG, dir=Dir.BEAR, top=c0.l, bottom=c.h, mid=(c0.l + c.h) / 2,
                              i_origin=i - 1, t_origin=c1.t, i_formed=i)
                events.append(Event("formed", self.tf, i, a))
                bear_disp = big and c1.c < c1.o
        if i >= 1:
            p = self.at(i - 1)
            gap_up = c.body_bot - p.body_top
            if gap_up > 0 and gap_up >= cfg.vi_min_atr * atr and (not cfg.vi_wick_overlap or c.l <= p.h):
                a = self._new(kind=Kind.VI, dir=Dir.BULL, top=c.body_bot, bottom=p.body_top,
                              mid=(c.body_bot + p.body_top) / 2, i_origin=i - 1, t_origin=p.t, i_formed=i)
                events.append(Event("formed", self.tf, i, a))
            gap_dn = p.body_bot - c.body_top
            if gap_dn > 0 and gap_dn >= cfg.vi_min_atr * atr and (not cfg.vi_wick_overlap or c.h >= p.l):
                a = self._new(kind=Kind.VI, dir=Dir.BEAR, top=p.body_bot, bottom=c.body_top,
                              mid=(p.body_bot + c.body_top) / 2, i_origin=i - 1, t_origin=p.t, i_formed=i)
                events.append(Event("formed", self.tf, i, a))

        # 5. Order Blocks: last opposite candle before a structure break / displacement
        if bos_up or bull_disp:
            a = self._order_block(Dir.BULL, i)
            if a is not None:
                events.append(Event("formed", self.tf, i, a))
        if bos_dn or bear_disp:
            a = self._order_block(Dir.BEAR, i)
            if a is not None:
                events.append(Event("formed", self.tf, i, a))

        # 6. swings -> liquidity pools + rejection blocks, 7. dealing range
        if self.trail_high is None:
            self.trail_high, self.t_trail_high = c.h, c.t
            self.trail_low, self.t_trail_low = c.l, c.t
        if c.h > self.trail_high:
            self.trail_high, self.t_trail_high = c.h, c.t
        if c.l < self.trail_low:
            self.trail_low, self.t_trail_low = c.l, c.t
        events.extend(self._swings(i))

        # capacity: expire the oldest live arrays beyond max_live
        while len(self.live) > cfg.max_live:
            oldest = min(self.live, key=lambda x: (x.i_formed, x.id))
            oldest.state = State.EXPIRED
            self._end(oldest, c, i)
            events.append(Event("expired", self.tf, i, oldest))
            self.live.remove(oldest)
        return events

    # ------------------------------------------------------------- internals
    def _successor(self, a: PDArray, i: int) -> Optional[PDArray]:
        """Failed arrays flip polarity: FVG -> iFVG, OB -> Breaker / Mitigation Block."""
        if a.kind == Kind.FVG and not a.inverted:
            kind, inverted = Kind.FVG, True
        elif a.kind == Kind.OB:
            kind, inverted = (Kind.BB if a.took_liq else Kind.MB), False
        else:
            return None
        child = self._new(kind=kind, dir=Dir(-a.dir), top=a.top, bottom=a.bottom, mid=a.mid,
                          i_origin=a.i_origin, t_origin=a.t_origin, i_formed=i, inverted=inverted,
                          parent_id=a.id)
        return child

    def _order_block(self, d: Dir, i: int) -> Optional[PDArray]:
        cfg = self.cfg
        j = None
        for k in range(i - 1, max(-1, i - 1 - cfg.ob_lookback), -1):
            ck = self.at(k)
            if (d == Dir.BULL and ck.bear) or (d == Dir.BEAR and ck.bull):
                j = k
                break
        if j is None or j <= self._last_ob_origin[d]:
            return None
        cj = self.at(j)
        top, bottom = (cj.h, cj.l) if cfg.ob_zone == "wick" else (cj.body_top, cj.body_bot)
        after = self.candles[j + 1:i + 1]
        if d == Dir.BULL:
            if any(x.c < bottom for x in after):
                return None
            extreme = max(x.h for x in after)
            ref = next((s.price for s in reversed(self.highs) if s.i < j), None)
            took = ref is not None and extreme > ref
        else:
            if any(x.c > top for x in after):
                return None
            extreme = min(x.l for x in after)
            ref = next((s.price for s in reversed(self.lows) if s.i < j), None)
            took = ref is not None and extreme < ref
        self._last_ob_origin[d] = j
        return self._new(kind=Kind.OB, dir=d, top=top, bottom=bottom, mid=(cj.o + cj.c) / 2,
                         i_origin=j, t_origin=cj.t, i_formed=i, liq_ref=ref, extreme=extreme, took_liq=took)

    def _swings(self, i: int) -> List[Event]:
        cfg = self.cfg
        L = cfg.swing_len
        events: List[Event] = []
        if i < 2 * L:
            return events
        k = i - L
        ck = self.at(k)
        left = self.candles[k - L:k]
        right = self.candles[k + 1:i + 1]
        is_ph = all(x.h < ck.h for x in left) and all(x.h <= ck.h for x in right)
        is_pl = all(x.l > ck.l for x in left) and all(x.l >= ck.l for x in right)
        tol = cfg.eq_tol_atr * (self.atr or 0.0)
        rng = ck.h - ck.l

        if is_ph:
            sw = Swing(k, ck.t, ck.h)
            self.highs.append(sw)
            self.last_high = sw
            self.trail_high, self.t_trail_high = ck.h, ck.t
            pool = self._pool(Dir.BEAR, ck.h, tol)
            if pool is not None:
                pool.touches += 1
                pool.top = pool.bottom = pool.mid = max(pool.top, ck.h)
            else:
                a = self._new(kind=Kind.LP, dir=Dir.BEAR, top=ck.h, bottom=ck.h, mid=ck.h,
                              i_origin=k, t_origin=ck.t, i_formed=i)
                events.append(Event("formed", self.tf, i, a))
            if rng > 0 and (ck.h - ck.body_top) / rng >= cfg.rb_wick_pct:
                a = self._new(kind=Kind.RB, dir=Dir.BEAR, top=ck.h, bottom=ck.body_top,
                              mid=(ck.h + ck.body_top) / 2, i_origin=k, t_origin=ck.t, i_formed=i)
                for x in right:
                    step(a, x)
                events.append(Event("formed", self.tf, i, a))

        if is_pl:
            sw = Swing(k, ck.t, ck.l)
            self.lows.append(sw)
            self.last_low = sw
            self.trail_low, self.t_trail_low = ck.l, ck.t
            pool = self._pool(Dir.BULL, ck.l, tol)
            if pool is not None:
                pool.touches += 1
                pool.top = pool.bottom = pool.mid = min(pool.bottom, ck.l)
            else:
                a = self._new(kind=Kind.LP, dir=Dir.BULL, top=ck.l, bottom=ck.l, mid=ck.l,
                              i_origin=k, t_origin=ck.t, i_formed=i)
                events.append(Event("formed", self.tf, i, a))
            if rng > 0 and (ck.body_bot - ck.l) / rng >= cfg.rb_wick_pct:
                a = self._new(kind=Kind.RB, dir=Dir.BULL, top=ck.body_bot, bottom=ck.l,
                              mid=(ck.body_bot + ck.l) / 2, i_origin=k, t_origin=ck.t, i_formed=i)
                for x in right:
                    step(a, x)
                events.append(Event("formed", self.tf, i, a))
        return events

    def _pool(self, d: Dir, level: float, tol: float) -> Optional[PDArray]:
        best = None
        for a in self.live:
            if a.kind == Kind.LP and a.dir == d and abs(a.top - level) <= tol:
                if best is None or abs(a.top - level) < abs(best.top - level):
                    best = a
        return best

    # --------------------------------------------------------------- matrix
    def snapshot(self, price: float) -> TFSnapshot:
        """The matrix column for this timeframe at ``price``.

        Premium ranks hold bearish arrays whose mean threshold sits above EQ,
        discount ranks hold bullish arrays below EQ. For each rank the live
        array closest to price is shown.
        """
        eq = self.eq
        snap = TFSnapshot(tf=self.tf, range_high=self.trail_high, range_low=self.trail_low, eq=eq,
                          position=self.position(price), bias=self.bias)
        for rank, kind in enumerate(PREMIUM_ORDER, start=1):
            snap.premium[rank] = self._nearest(kind, Dir.BEAR, price, premium=True)
        for rank, kind in enumerate(DISCOUNT_ORDER, start=1):
            snap.discount[rank] = self._nearest(kind, Dir.BULL, price, premium=False)
        above = [a for a in self.live if a.bottom > price]
        below = [a for a in self.live if a.top < price]
        snap.next_up = min(above, key=lambda a: (a.bottom, a.id), default=None)
        snap.next_down = max(below, key=lambda a: (a.top, -a.id), default=None)
        return snap

    def _nearest(self, kind: Kind, d: Dir, price: float, premium: bool) -> Optional[PDArray]:
        eq = self.eq
        if eq is None:
            return None
        best = None
        for a in self.live:
            if a.kind != kind or a.dir != d:
                continue
            if premium and not a.mid > eq:
                continue
            if not premium and not a.mid < eq:
                continue
            if best is None or a.distance(price) < best.distance(price):
                best = a
        return best
