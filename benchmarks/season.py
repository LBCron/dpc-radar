"""Run a method on every pair of whole frames in a folder, not only on the cases.

    uv run --group bench python -m benchmarks.season DIR [method]

DIR holds full CUM24 frames named ``CUM24_YYYYMMDDHHMM.tif`` (the cache of
``benchmarks.fetch`` or ``docs/make_images.py``). For every frame whose
previous one (30 minutes earlier) is also there, it prints what the method
decides on the whole of Italy, so a rule tuned on ten cases is checked on
weeks of frames. A rejected frame at :00 is printed apart: in October 2026
only the :30 series was corrupted.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from benchmarks.bench import Frame
from benchmarks.methods import METHODS


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.season")
    parser.add_argument("folder", type=Path)
    parser.add_argument("method", nargs="?", default="jump-sign", choices=list(METHODS))
    args = parser.parse_args()
    method = METHODS[args.method]

    files = {
        datetime.strptime(p.stem.split("_")[1], "%Y%m%d%H%M"): p
        for p in args.folder.glob("CUM24_*.tif")
    }
    pairs = rejected = 0
    rejected_at_00: list[str] = []
    for t in sorted(files):
        before = t - timedelta(minutes=30)
        if before not in files:
            continue
        pairs += 1
        frame, previous = Frame.read(files[t]), Frame.read(files[before])
        ok = method.frame_ok(frame, previous)
        valid = np.isfinite(frame.data) & np.isfinite(previous.data)
        corr = float(np.corrcoef(frame.data[valid], previous.data[valid])[0, 1])
        if ok is False:
            rejected += 1
            if t.minute == 0:
                rejected_at_00.append(f"{t:%Y-%m-%d %H:%M}")
            peak = float(np.nanmax(frame.data))
            print(f"{t:%Y-%m-%d %H:%M}  rejected  peak {peak:9.1f} mm  corr {corr:.2f}")
    print(f"\n{method.name}: {pairs} pairs, {rejected} frames rejected")
    print(f"rejected at :00: {', '.join(rejected_at_00) or 'none'}")


if __name__ == "__main__":
    main()
