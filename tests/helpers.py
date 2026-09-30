from datetime import datetime
from zoneinfo import ZoneInfo

from pdmatrix import Candle

NY = ZoneInfo("America/New_York")
FIVE_MIN = 300_000


def ny_ms(y, mo, d, h, mi):
    return int(datetime(y, mo, d, h, mi, tzinfo=NY).timestamp() * 1000)


# Monday 2026-09-28 09:10 New York
T0 = ny_ms(2026, 9, 28, 9, 10)


def bars(rows, start=T0, step=FIVE_MIN):
    return [Candle(start + k * step, o, h, l, c, start + (k + 1) * step) for k, (o, h, l, c) in enumerate(rows)]


def feed(engine, rows, **kw):
    events = []
    for c in bars(rows, **kw):
        events.extend(engine.process(c))
    return events


def of(engine, kind, dir=None, include_dead=True):
    pool = engine.history if include_dead else engine.live
    return [a for a in pool if a.kind == kind and (dir is None or a.dir == dir)]
