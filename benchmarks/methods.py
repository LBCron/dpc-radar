"""Baseline methods: what Limen did before and does today.

They are here to be beaten. A new method goes in this file (or anywhere,
registered in ``METHODS``) and is run with ``python -m benchmarks.run <name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from benchmarks.bench import Frame, Method
from dpc_radar import CUM24, FrameCheck, RainGrid


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

    This is :meth:`dpc_radar.RainGrid.check`, the library's own rule, run on the
    benchmark: two CUM24 frames 30 minutes apart can differ by at most half an
    hour of rain; where they differ by more than ``max_step`` on at least
    ``min_pixels`` pixels, the frame that jumps *up* is rejected whole and the
    one that jumps *down* (the clean frame after a corrupted one) is kept
    intact. Fewer jumps: just those pixels are masked.

    Limits: it needs the frame before; it cannot see artefacts that do not
    move between frames (sea clutter, a steady bump); in this benchmark it
    decides on the case's area, not on the whole of Italy.
    """

    max_step: float = 150.0
    min_pixels: int = 5
    name: str = "jump-sign"
    description: str = "RainGrid.check: reject the frame that jumps up > 150 mm (#6)"

    def _check(self, frame: Frame, previous: Frame) -> FrameCheck:
        now, before = _grid(frame), _grid(previous)
        return now.check(before, max_step=self.max_step, min_pixels=self.min_pixels)

    def frame_ok(self, frame: Frame, previous: Frame) -> bool | None:
        return self._check(frame, previous).usable

    def clean(self, frame: Frame, previous: Frame) -> np.ndarray:
        result = self._check(frame, previous)
        if result.grid is None:
            return np.full_like(frame.data, np.nan)  # rejected: nothing measured
        return result.grid.data

    def reliable(self, lon: float, lat: float) -> bool | None:
        return None


def _grid(frame: Frame) -> RainGrid:
    """A benchmark frame as the library's grid (product and time do not matter here)."""
    return RainGrid(CUM24, datetime.min.replace(tzinfo=UTC), frame.data, frame.transform, frame.crs)


METHODS: dict[str, Method] = {m.name: m for m in (AcceptAll(), LimenMaxStep(), JumpSign())}
