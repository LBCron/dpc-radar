"""One radar frame as an array, and what to trust in it.

A :class:`RainGrid` is a DPC frame read from its GeoTIFF: rain in mm (CUM,
SRT1) or mm/h (SRI), ``NaN`` where the radar has no data. Two grids exist and
must not be mixed: SRI and SRT1 are 1 km in a Transverse Mercator projection,
the CUM products 0.01° in EPSG:4326. Every method takes lon/lat in degrees and
converts them to the grid's own projection.

Read a place with :meth:`RainGrid.sample`: the median of the 3x3 pixels around
it, never one pixel. Check a frame against the one before it with
:meth:`RainGrid.check` before trusting it: on 9-11 October 2026 and again from
21 October to 3 November the DPC published every CUM24 frame at :30 with a
bell of up to 20,584 mm over Abruzzo, while the :00 frames were right.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

import numpy as np
from affine import Affine
from rasterio.crs import CRS
from rasterio.io import MemoryFile
from rasterio.transform import rowcol
from rasterio.warp import transform as warp_points
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

from dpc_radar.errors import DpcRadarError
from dpc_radar.products import NODATA, Product, product

BBox = tuple[float, float, float, float]
"""west, south, east, north, in degrees (EPSG:4326)."""

Verdict = Literal["consistent", "pixels_masked", "previous_wrong", "rejected", "not_comparable"]


@dataclass(frozen=True, slots=True)
class RainGrid:
    """One frame: ``data`` in mm or mm/h (float32, as published), ``NaN`` where there is no data."""

    product: Product
    time: datetime
    data: np.ndarray
    transform: Affine
    crs: CRS

    @classmethod
    def from_geotiff(cls, raw: bytes, name: str | Product, time: datetime) -> RainGrid:
        """A frame from the bytes of its GeoTIFF; ``-9999`` becomes ``NaN``."""
        try:
            with MemoryFile(raw) as mem, mem.open() as ds:
                data = ds.read(1).astype("float32")
                grid_transform, crs = ds.transform, ds.crs
        except Exception as exc:  # rasterio raises its own hierarchy
            raise DpcRadarError(f"not a readable GeoTIFF: {exc}") from exc
        data[data <= NODATA] = np.nan
        return cls(product(name), time, data, grid_transform, crs)

    def sample(self, points: list[tuple[float, float]]) -> list[float | None]:
        """The value at each lon/lat point: the median of the valid 3x3 pixels.

        The median and not the pixel, so that one wrong pixel never becomes a
        flood on one cell. Outside the frame, or where the 3x3 window holds no
        data, the answer is ``None``: not measured, which is not the same as
        dry.
        """
        if not points:
            return []
        xs, ys = warp_points("EPSG:4326", self.crs, [p[0] for p in points], [p[1] for p in points])
        rows, cols = rowcol(self.transform, xs, ys)
        h, w = self.data.shape
        out: list[float | None] = []
        for r, c in zip(rows, cols, strict=True):
            if not (0 <= r < h and 0 <= c < w):
                out.append(None)
                continue
            tile = self.data[max(0, r - 1) : r + 2, max(0, c - 1) : c + 2]
            valid = tile[np.isfinite(tile)]
            out.append(float(np.median(valid)) if valid.size else None)
        return out

    def peak(self, bbox: BBox, *, threshold: float) -> tuple[float, int]:
        """The highest value inside ``bbox``, and how many pixels reach ``threshold``.

        ``(0.0, 0)`` outside the frame or where there is no data: Limen's radar
        trigger asks "is it raining hard enough, on enough km², here?".
        """
        tile = self._window(bbox)
        valid = tile[np.isfinite(tile)]
        if valid.size == 0:
            return 0.0, 0
        return float(valid.max()), int((valid >= threshold).sum())

    def crop(self, bbox: BBox) -> RainGrid:
        """The part of the frame inside ``bbox`` (plus the pixel on each edge)."""
        r0, r1, c0, c1 = self._bounds(bbox)
        if r0 >= r1 or c0 >= c1:
            raise DpcRadarError(f"bbox {bbox} is outside the frame")
        data = self.data[r0:r1, c0:c1]
        return replace(self, data=data, transform=self.transform @ Affine.translation(c0, r0))

    def check(self, previous: RainGrid, *, max_step: float, min_pixels: int = 5) -> FrameCheck:
        """Whether to trust this frame, given the one before it.

        Two accumulations of the same length, one period apart, differ by the
        rain that entered the window minus the rain that left it: never more
        than one period of rain. Where they differ by more than ``max_step``,
        one of the two is wrong, and the sign says which: every corruption
        seen so far *adds* rain.

        * no pixel jumps: ``consistent``, the frame as it is;
        * fewer than ``min_pixels`` jump: ``pixels_masked``, those pixels set
          to ``NaN``;
        * more jump, and this frame is the higher: ``rejected``, use the
          previous frame instead;
        * more jump, and this frame is the lower: ``previous_wrong``, this frame
          as it is (it is the previous one that was corrupted);
        * different grids: ``not_comparable``, the frame as it is.

        ``max_step`` is the caller's choice (Limen uses 150 mm for CUM
        frames 30 minutes apart). Measured on the benchmark of known cases, this
        rule passes 17 of 18 checks and, on 96 pairs of whole frames, rejects
        the 44 corrupted ones and no clean one. It cannot see an artefact that
        does not move between frames.
        """
        if previous.data.shape != self.data.shape or previous.transform != self.transform:
            return FrameCheck("not_comparable", self, 0)
        diff = self.data - previous.data  # NaN where either has no data
        bad = np.isfinite(diff) & (np.abs(diff) > max_step)
        jumps = int(bad.sum())
        if jumps == 0:
            return FrameCheck("consistent", self, 0)
        if jumps < min_pixels:
            data = self.data.copy()
            data[bad] = np.nan
            return FrameCheck("pixels_masked", replace(self, data=data), jumps)
        if float(np.median(diff[bad])) < 0:
            return FrameCheck("previous_wrong", self, jumps)
        return FrameCheck("rejected", None, jumps)

    def discard_inconsistent(self, previous: RainGrid, *, max_step: float) -> tuple[RainGrid, int]:
        """This frame without the pixels that moved more than ``max_step``.

        Limen's rule from October 2026, kept for comparison: it cannot tell
        which of the two frames is wrong, so it masks the pixels in both cases,
        and it lets through the edges of a corrupted bell (jumps under
        ``max_step``). Prefer :meth:`check`.
        """
        if previous.data.shape != self.data.shape or previous.transform != self.transform:
            return self, 0
        bad = np.isfinite(self.data) & np.isfinite(previous.data)
        bad &= np.abs(self.data - previous.data) > max_step
        n = int(bad.sum())
        if n == 0:
            return self, 0
        data = self.data.copy()
        data[bad] = np.nan
        return replace(self, data=data), n

    def _bounds(self, bbox: BBox) -> tuple[int, int, int, int]:
        west, south, east, north = transform_bounds("EPSG:4326", self.crs, *bbox)
        window = from_bounds(west, south, east, north, transform=self.transform)
        r0 = max(0, int(window.row_off))
        c0 = max(0, int(window.col_off))
        r1 = min(self.data.shape[0], int(window.row_off + window.height) + 1)
        c1 = min(self.data.shape[1], int(window.col_off + window.width) + 1)
        return r0, r1, c0, c1

    def _window(self, bbox: BBox) -> np.ndarray:
        r0, r1, c0, c1 = self._bounds(bbox)
        if r0 >= r1 or c0 >= c1:
            return self.data[:0, :0]
        return self.data[r0:r1, c0:c1]


@dataclass(frozen=True, slots=True)
class FrameCheck:
    """The answer of :meth:`RainGrid.check`.

    ``grid`` is the frame to use (with masked pixels for ``pixels_masked``),
    or ``None`` when the frame is ``rejected``. ``jumps`` is how many pixels
    moved more than ``max_step``.
    """

    verdict: Verdict
    grid: RainGrid | None
    jumps: int

    @property
    def usable(self) -> bool:
        return self.grid is not None


__all__ = ["BBox", "FrameCheck", "RainGrid", "Verdict"]
