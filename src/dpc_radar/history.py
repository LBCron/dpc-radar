"""Fifteen years of radar rain: the IT-DPC-SRI archive (2010-2025), read from disk.

IT-DPC-SRI (Fondazione Bruno Kessler on DPC data, CC BY-SA 4.0,
`doi:10.5281/zenodo.18637608 <https://doi.org/10.5281/zenodo.18637608>`_) is the
national SRI mosaic, rain intensity in mm/h from the radar alone, as one
Zarr v2 store: one chunk per time step, 1400 x 1200 pixels of 1 km in a
Transverse Mercator projection centred on 42°N 12.5°E, a frame every 15 minutes
until 2014, every 10 until 2020, every 5 after. It is published as a single
50 GB ``.zarr.tar.zst``: unpack it once, then point :class:`SriHistory` at the
``.zarr`` folder.

It is read here without zarr or xarray: the Blosc codec (extra ``history``,
``pip install "dpc-radar[history]"``) is enough to decompress a frame.

It is the history the API does not give (it keeps only the last months), and it
is radar only: it under-estimates heavy rain, so compare it with the «Merging»
of :mod:`dpc_radar.archive` where both exist.

An hour covered for less than half by frames is ``None``, not measured: a gap
in the archive is not a dry hour.
"""

from __future__ import annotations

import json
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import numpy as np
from rasterio.warp import transform as warp_points

from dpc_radar.errors import DpcRadarError

#: The origin of the store's time axis ("minutes since 2010-01-01").
EPOCH: Final = datetime(2010, 1, 1, tzinfo=UTC)
#: The longest step of the archive: a wider interval is a gap, and the frame
#: before the gap does not cover it.
_MAX_STEP_MIN = 15


def _blosc() -> Any:
    try:
        from numcodecs import Blosc
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise DpcRadarError(
            'reading IT-DPC-SRI needs the Blosc codec: pip install "dpc-radar[history]"'
        ) from exc
    return Blosc()


class SriHistory:
    """Hourly rain at lon/lat points from the IT-DPC-SRI Zarr store on disk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._codec = _blosc()
        try:
            rr = json.loads((path / "RR" / ".zarray").read_text())
            crs = json.loads((path / "crs" / ".zattrs").read_text())
        except FileNotFoundError as exc:
            raise DpcRadarError(f"{path} is not the IT-DPC-SRI .zarr folder") from exc
        self.shape: tuple[int, int] = (int(rr["shape"][1]), int(rr["shape"][2]))
        x0, dx, _, y0, _, dy = (float(v) for v in crs["GeoTransform"].split())
        self._x0, self._dx, self._y0, self._dy = x0, dx, y0, dy
        self._crs_wkt: str = crs["crs_wkt"]
        self._minutes = np.frombuffer(self._decode(path / "time" / "0"), dtype="<i8")

    @property
    def first(self) -> datetime:
        return EPOCH + timedelta(minutes=int(self._minutes[0]))

    @property
    def last(self) -> datetime:
        return EPOCH + timedelta(minutes=int(self._minutes[-1]))

    def __len__(self) -> int:
        return len(self._minutes)

    def _decode(self, file: Path) -> bytes:
        return bytes(self._codec.decode(file.read_bytes()))

    def pixel(self, points: list[tuple[float, float]]) -> list[tuple[int, int] | None]:
        """(row, column) of each lon/lat point; ``None`` outside the grid."""
        xs, ys = warp_points(
            "EPSG:4326", self._crs_wkt, [p[0] for p in points], [p[1] for p in points]
        )
        out: list[tuple[int, int] | None] = []
        for x, y in zip(xs, ys, strict=True):
            r = int((y - self._y0) // self._dy)
            c = int((x - self._x0) // self._dx)
            inside = 0 <= r < self.shape[0] and 0 <= c < self.shape[1]
            out.append((r, c) if inside else None)
        return out

    def frame(self, i: int) -> np.ndarray | None:
        """Frame ``i`` of the store in mm/h (NaN for no data); ``None`` if its chunk is missing."""
        file = self.path / "RR" / f"{i}.0.0"
        if not file.exists():
            return None
        data = np.frombuffer(self._decode(file), dtype="<f4").reshape(self.shape)
        return np.where(data >= 0, data, np.nan)

    def time(self, i: int) -> datetime:
        """The time of frame ``i``."""
        return EPOCH + timedelta(minutes=int(self._minutes[i]))

    def _windows(self, pixels: list[tuple[int, int] | None]) -> tuple[np.ndarray, np.ndarray]:
        """Indices of the 3x3 pixels around each point, for one vectorised read.

        At the edges the index folds back onto the edge; a point outside the grid
        reads pixel (0, 0), and the caller drops it.
        """
        h, w = self.shape
        rows = np.zeros((len(pixels), 9), dtype=np.intp)
        cols = np.zeros((len(pixels), 9), dtype=np.intp)
        dr, dc = np.meshgrid([-1, 0, 1], [-1, 0, 1], indexing="ij")
        for k, rc in enumerate(pixels):
            if rc is None:
                continue
            r, c = rc
            rows[k] = np.clip(r + dr.ravel(), 0, h - 1)
            cols[k] = np.clip(c + dc.ravel(), 0, w - 1)
        return rows, cols

    def hourly(
        self, points: list[tuple[float, float]], start: datetime, end: datetime
    ) -> dict[datetime, list[float | None]]:
        """Rain of every hour ``(h-1, h]`` in ``(start, end]``, per lon/lat point (mm).

        The mean of the intensities of the hour's frames, each weighted by the
        time it covers until the next one (15 minutes at most), read as the
        median of the 3x3 pixels around the point. The same signature as
        :meth:`dpc_radar.archive.MergingArchive.hourly`: the two are
        interchangeable.
        """
        pixels = self.pixel(points)
        outside = np.array([rc is None for rc in pixels])
        rows, cols = self._windows(pixels)
        a = int((start - EPOCH).total_seconds() // 60)
        b = int((end - EPOCH).total_seconds() // 60)
        # A frame at `m` is the rain from `m` to the next frame: the hours in
        # (start, end] are made of the frames issued in [start, end).
        lo = int(np.searchsorted(self._minutes, a, side="left"))
        hi = int(np.searchsorted(self._minutes, b, side="left"))
        sums: dict[int, np.ndarray] = {}
        covered: dict[int, np.ndarray] = {}
        for i in range(lo, hi):
            m = int(self._minutes[i])
            after = int(self._minutes[i + 1]) if i + 1 < len(self._minutes) else m + 5
            weight = min(after - m, _MAX_STEP_MIN)
            f = self.frame(i)
            if f is None:
                continue
            windows = f[rows, cols].astype(np.float64)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # an all-NaN window
                values = np.nanmedian(windows, axis=1)
            values[outside] = np.nan
            # A frame counts from `m` to the next one: it falls in the hour closing after it.
            hour = m // 60 + 1
            ok = np.isfinite(values)
            sums.setdefault(hour, np.zeros(len(points)))[ok] += values[ok] * weight
            covered.setdefault(hour, np.zeros(len(points)))[ok] += weight
        out: dict[datetime, list[float | None]] = {}
        for hour, total in sorted(sums.items()):
            cov = covered[hour]
            out[EPOCH + timedelta(hours=hour)] = [
                float(s / c) if c >= 30 else None for s, c in zip(total, cov, strict=True)
            ]
        return out


__all__ = ["EPOCH", "SriHistory"]
