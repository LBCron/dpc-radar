"""The benchmark: known cases of the DPC radar data, and how a method scores on them.

A *method* is anything that decides what to trust in a frame. It gets the frame
and the one before it and answers three questions:

* ``frame_ok``: is the frame usable at all? (``None``: the method does not say)
* ``clean``: the frame with the pixels it does not trust set to NaN;
* ``reliable``: can the radar be trusted at a point? (``None``: does not say)

The runner reads each point the way Limen does (median of the 3x3 valid pixels
around it) and compares every answer with ``cases.toml``. "Does not say"
counts as not passed: a method that never decides scores zero on that check.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
import rasterio
from affine import Affine

HERE = Path(__file__).parent
CASES = HERE / "cases.toml"
FRAMES = HERE / "frames"

#: How far back the previous frame of each product is.
PERIOD = {"CUM3": 30, "CUM6": 30, "CUM12": 30, "CUM24": 30, "SRI": 5, "SRT1": 5}

Outcome = Literal["pass", "fail", "none"]


@dataclass(frozen=True)
class Frame:
    """One cropped frame: rain in mm (NaN where the radar has no data)."""

    data: np.ndarray
    transform: Affine

    @classmethod
    def read(cls, path: Path) -> Frame:
        with rasterio.open(path) as src:
            data = src.read(1).astype("float64")
            data[data < 0] = np.nan  # -9999: no data
            return cls(data, src.transform)

    def index(self, lon: float, lat: float) -> tuple[int, int]:
        col, row = ~self.transform * (lon, lat)
        return math.floor(row), math.floor(col)


@dataclass(frozen=True)
class Check:
    kind: Literal["frame", "value", "masked", "reliable"]
    name: str = ""
    lon: float = math.nan
    lat: float = math.nan
    expect: bool | None = None
    min: float | None = None
    max: float | None = None
    masked_ok: bool = False

    def label(self) -> str:
        if self.kind == "frame":
            return "frame usable" if self.expect else "frame rejected"
        if self.kind == "masked":
            return f"at most {self.max:.1%} of pixels masked"
        if self.kind == "reliable":
            return f"{self.name}: {'reliable' if self.expect else 'not reliable'}"
        rng = f"{self.min or 0:g}-{self.max:g} mm" if self.max is not None else f"≥ {self.min:g} mm"
        return f"{self.name}: {rng}" + (" or not measured" if self.masked_ok else "")


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    status: Literal["confirmed", "suspect"]
    product: str
    time: datetime
    bbox: tuple[float, float, float, float]
    source: str
    checks: list[Check] = field(default_factory=list)

    @property
    def previous_time(self) -> datetime:
        return self.time - timedelta(minutes=PERIOD[self.product])

    def paths(self) -> tuple[Path, Path]:
        folder = FRAMES / self.id
        return folder / "frame.tif", folder / "previous.tif"


def load_cases(path: Path = CASES) -> list[Case]:
    raw: dict[str, Any] = tomllib.loads(path.read_text())
    cases = []
    for c in raw["case"]:
        checks = [Check(**k) for k in c.get("check", [])]
        cases.append(
            Case(
                id=c["id"],
                title=c["title"],
                status=c["status"],
                product=c["product"],
                time=c["time"],
                bbox=tuple(c["bbox"]),
                source=c["source"],
                checks=checks,
            )
        )
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case id in cases.toml")
    return cases


class Method(Protocol):
    """What a method must answer. See ``methods.py`` for the baselines."""

    name: str
    description: str

    def frame_ok(self, frame: Frame, previous: Frame) -> bool | None: ...

    def clean(self, frame: Frame, previous: Frame) -> np.ndarray: ...

    def reliable(self, lon: float, lat: float) -> bool | None: ...


def sample(data: np.ndarray, frame: Frame, lon: float, lat: float) -> float | None:
    """The value at a point as Limen reads it: the median of the valid 3x3 pixels."""
    r, c = frame.index(lon, lat)
    tile = data[max(0, r - 1) : r + 2, max(0, c - 1) : c + 2]
    valid = tile[np.isfinite(tile)]
    return float(np.median(valid)) if valid.size else None


@dataclass(frozen=True)
class Result:
    case: Case
    check: Check
    outcome: Outcome
    got: str


def score(check: Check, method: Method, frame: Frame, previous: Frame, cleaned: np.ndarray):
    """One check: its outcome and what the method answered, in words."""
    if check.kind == "frame":
        ok = method.frame_ok(frame, previous)
        if ok is None:
            return "none", "no frame decision"
        return ("pass" if ok == check.expect else "fail"), ("usable" if ok else "rejected")
    if check.kind == "reliable":
        rel = method.reliable(check.lon, check.lat)
        if rel is None:
            return "none", "no reliability"
        return ("pass" if rel == check.expect else "fail"), ("reliable" if rel else "not reliable")
    if check.kind == "masked":
        valid = np.isfinite(frame.data)
        share = float((~np.isfinite(cleaned) & valid).sum() / max(valid.sum(), 1))
        assert check.max is not None
        return ("pass" if share <= check.max else "fail"), f"{share:.2%} masked"
    value = sample(cleaned, frame, check.lon, check.lat)
    if value is None:
        return ("pass" if check.masked_ok else "fail"), "not measured"
    low = -math.inf if check.min is None else check.min
    high = math.inf if check.max is None else check.max
    return ("pass" if low <= value <= high else "fail"), f"{value:.1f} mm"


def run(method: Method, cases: list[Case] | None = None) -> list[Result]:
    results = []
    for case in cases if cases is not None else load_cases():
        frame_path, previous_path = case.paths()
        frame, previous = Frame.read(frame_path), Frame.read(previous_path)
        cleaned = method.clean(frame, previous)
        for check in case.checks:
            outcome, got = score(check, method, frame, previous, cleaned)
            results.append(Result(case, check, outcome, got))
    return results
