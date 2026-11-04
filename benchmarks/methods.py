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


METHODS: dict[str, Method] = {m.name: m for m in (AcceptAll(), LimenMaxStep())}
