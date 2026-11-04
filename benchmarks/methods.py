"""Baseline methods: what Limen did before and does today.

They are here to be beaten. A new method goes in this file (or anywhere,
registered in ``METHODS``) and is run with ``python -m benchmarks.run <name>``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from benchmarks.bench import Frame, Method


@dataclass(frozen=True)
class AcceptAll:
    """Trust every pixel of every frame."""

    name: str = "accept-all"
    description: str = "trust everything (no cleaning)"

    def frame_ok(self, frame: Frame, previous: Frame) -> bool | None:
        return True

    def clean(self, frame: Frame, previous: Frame) -> np.ndarray:
        return frame.data

    def reliable(self, lon: float, lat: float) -> bool | None:
        return None


@dataclass(frozen=True)
class LimenMaxStep:
    """Limen since #235 (October 2026): a pixel that moved more than
    ``max_step`` mm since the frame before is "not measured". No decision on
    the whole frame, no notion of where the radar is blind. Points are read with
    the 3x3 median, as everywhere in this benchmark.
    """

    max_step: float = 150.0
    name: str = "limen-max-step"
    description: str = "Limen today: mask pixels that jump > 150 mm in 30 min (limen#235)"

    def frame_ok(self, frame: Frame, previous: Frame) -> bool | None:
        return None

    def clean(self, frame: Frame, previous: Frame) -> np.ndarray:
        a, b = frame.data, previous.data
        bad = np.isfinite(a) & np.isfinite(b) & (np.abs(a - b) > self.max_step)
        out = a.copy()
        out[bad] = np.nan
        return out

    def reliable(self, lon: float, lat: float) -> bool | None:
        return None


@dataclass(frozen=True)
class JumpSign:
    """A decision on the whole frame, from the sign of the jump (#6, first step).

    Two CUM24 frames 30 minutes apart can differ by at most half an hour of
    rain. Where they differ by more than ``max_step``, one of the two is wrong,
    and in every corruption seen so far the wrong one is the *higher* one: the
    corruption adds rain, it never removes it. So:

    * jumps up on at least ``min_pixels`` pixels: this frame is the wrong one,
      reject it whole (the caller falls back to the frame before);
    * jumps down: the frame before was the wrong one; keep this one intact;
    * fewer than ``min_pixels`` jumps: mask just those pixels, as Limen does.

    Limits: it needs the frame before; it cannot see artefacts that do not
    move between frames (sea clutter, a steady bump); in this benchmark it
    decides on the case's area, not on the whole of Italy.
    """

    max_step: float = 150.0
    min_pixels: int = 5
    name: str = "jump-sign"
    description: str = "reject the frame that jumps up > 150 mm; keep the one after it (#6)"

    def _jumps(self, frame: Frame, previous: Frame) -> tuple[np.ndarray, np.ndarray]:
        diff = frame.data - previous.data  # NaN where either has no data
        bad = np.isfinite(diff) & (np.abs(diff) > self.max_step)
        return diff, bad

    def frame_ok(self, frame: Frame, previous: Frame) -> bool | None:
        diff, bad = self._jumps(frame, previous)
        if bad.sum() < self.min_pixels:
            return True
        return bool(np.median(diff[bad]) < 0)

    def clean(self, frame: Frame, previous: Frame) -> np.ndarray:
        diff, bad = self._jumps(frame, previous)
        if bad.sum() >= self.min_pixels:
            if np.median(diff[bad]) < 0:
                return frame.data  # the frame before was the wrong one
            return np.full_like(frame.data, np.nan)  # this one is wrong: nothing measured
        out = frame.data.copy()
        out[bad] = np.nan
        return out

    def reliable(self, lon: float, lat: float) -> bool | None:
        return None


METHODS: dict[str, Method] = {m.name: m for m in (AcceptAll(), LimenMaxStep(), JumpSign())}
