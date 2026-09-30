"""Run the matrix over a CSV of OHLC candles.

    python -m pdmatrix candles.csv --tf 5 --htf 15,60,240,D

CSV columns: time,open,high,low,close (time as epoch seconds/ms or ISO-8601).
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from typing import List

from .matrix import MatrixEngine
from .model import Candle, Config, SetupConfig
from .timeframes import tf_name, tf_seconds


def _parse_time(v: str) -> int:
    v = v.strip()
    try:
        x = float(v)
        return int(x if x > 1e11 else x * 1000)
    except ValueError:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)


def load_csv(path: str, tf: str) -> List[Candle]:
    step = tf_seconds(tf) * 1000
    out = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            r = {k.strip().lower(): v for k, v in row.items()}
            t = _parse_time(r.get("time") or r.get("timestamp") or r["date"])
            out.append(Candle(t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), t + step))
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="pdmatrix", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--tf", default="5", help="timeframe of the CSV candles (execution TF)")
    ap.add_argument("--htf", default="15,60,240,D,W", help="higher timeframes, comma separated")
    ap.add_argument("--tz", default="America/New_York")
    ap.add_argument("--swing-len", type=int, default=Config.swing_len)
    ap.add_argument("--no-macro", action="store_true", help="do not require an ICT macro window for entries")
    ap.add_argument("--events", type=int, default=20, help="print the last N lifecycle events")
    args = ap.parse_args(argv)

    cfg = Config(swing_len=args.swing_len)
    scfg = SetupConfig(require_macro=not args.no_macro)
    m = MatrixEngine(args.tf, [x for x in args.htf.split(",") if x], cfg, scfg, tz=args.tz)
    m.run(load_csv(args.csv, args.tf))

    print(m.render())
    if m.skipped:
        print(f"\n(skipped timeframes not above the execution TF: {', '.join(m.skipped)})")
    shown = [e for e in m.events if e.type != "bos"][-args.events:] if args.events > 0 else []
    if shown:
        print("\nLast lifecycle events:")
    for e in shown:
        a = e.array
        extra = f" -> {e.child.label} ({e.child.kind.name})" if e.child else ""
        print(f"  {tf_name(e.tf):>4} #{e.i:<6} {a.label:<5} {a.bottom:.5g}-{a.top:.5g}  {e.type}{extra}")
    s = m.setup.stats()
    print(f"\nOne Setup for Life: {s['signals']} signals, {s['wins']}W/{s['losses']}L, "
          f"win rate {s['win_rate'] * 100:.0f}%, net {s['net_r']:+.1f}R")


if __name__ == "__main__":
    main()
