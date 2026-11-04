"""A local archive of the «Merging», and hourly rain corrected with the gauges.

The DPC API keeps about five months of frames, then they are gone. The
radar-only history (IT-DPC-SRI, :mod:`dpc_radar.history`) under-estimates: in
Apulia in September 2016 it read between 10 % and 80 % of the rain gauges. The
«Merging» is the correction with the gauges, and nobody archives it publicly:
whatever is not saved in time is lost, one month at a time.

Two products are kept, because the shortest «Merging» is three hours long:

* ``CUM3`` at 00, 03, ..., 21 UTC: three-hour blocks that do not overlap,
  radar and gauges, 0.01° in EPSG:4326;
* ``SRT1`` at every full hour: the radar-only rain of that hour, 1 km.

The rain of one hour is SRT1 rescaled so that the three hours of a block add up
exactly to the CUM3: the «Merging» says *how much*, the radar says *when*. If
the radar saw nothing in a block (or missed an hour of it) and the gauges did,
the block is split in equal parts; without CUM3, SRT1 stays as it is; without
either, the hour is not measured (``None``), which is not the same as dry.

Layout: ``<folder>/<PRODUCT>/<YYYY>/<MM>/<DD>/<PRODUCT>_<YYYYMMDDhhmm>.tif``,
plus ``<folder>/<PRODUCT>/absent.txt`` with the frames the DPC no longer keeps.
It is the layout of Limen's archive, so an existing one can be read as is
(its ``assenti.txt`` is read too).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import httpx

from dpc_radar.client import Absent, RadarClient
from dpc_radar.errors import DpcRadarError
from dpc_radar.grid import RainGrid

#: Product → hours between two archived frames.
STEPS: Final = {"CUM3": 3, "SRT1": 1}
#: Below this sum over a block the radar "saw nothing" there (mm).
_RADAR_DRY_MM = 0.1
#: Before this age a 403 is a frame not published yet, not one that is gone.
ABSENT_AFTER: Final = timedelta(days=7)
_ABSENT_FILES = ("absent.txt", "assenti.txt")  # the second: Limen's archive


def path(folder: Path, product: str, time: datetime) -> Path:
    """Where a frame lives in the archive."""
    return folder / product / f"{time:%Y/%m/%d}" / f"{product}_{time:%Y%m%d%H%M}.tif"


def times(product: str, start: datetime, end: datetime) -> list[datetime]:
    """The archived times of ``product`` in ``[start, end]``, oldest first."""
    step = STEPS[product]
    t = start.replace(minute=0, second=0, microsecond=0)
    if t < start:
        t += timedelta(hours=1)
    t += timedelta(hours=(-t.hour) % step)
    out = []
    while t <= end:
        out.append(t)
        t += timedelta(hours=step)
    return out


def _absent(folder: Path, product: str) -> set[str]:
    known: set[str] = set()
    for name in _ABSENT_FILES:
        file = folder / product / name
        if file.exists():
            known.update(file.read_text().split())
    return known


@dataclass
class ArchiveReport:
    """What one :func:`archive` run did."""

    downloaded: int = 0
    present: int = 0
    lost: int = 0
    failed: int = 0
    bytes: int = 0

    @property
    def requests(self) -> int:
        return self.downloaded + self.lost + self.failed


async def archive(
    folder: Path,
    radar: RadarClient,
    *,
    start: datetime,
    end: datetime,
    limit: int,
    newest_first: bool = False,
    now: datetime | None = None,
) -> ArchiveReport:
    """Download the CUM3 and SRT1 frames of ``[start, end]`` that the archive lacks.

    Oldest first, because the oldest is the one the DPC will lose first;
    ``newest_first`` for a run that must keep up with the last hours before
    the ``limit`` is spent on old frames. Idempotent: a frame on disk is not
    downloaded again, and one the DPC no longer keeps is written to
    ``absent.txt`` and not asked again. A frame younger than
    :data:`ABSENT_AFTER` that the API calls gone is not written down: it may
    just not be published yet. Stops after ``limit`` requests. A frame that
    fails for any other reason is counted and retried on the next run.
    """
    now = now or datetime.now(UTC)
    report = ArchiveReport()
    for product in STEPS:
        absent_file = folder / product / _ABSENT_FILES[0]
        known = _absent(folder, product)
        order = times(product, start, end)
        for t in reversed(order) if newest_first else order:
            if report.requests >= limit:
                return report
            dest = path(folder, product, t)
            key = f"{t:%Y%m%d%H%M}"
            if dest.exists() or key in known:
                report.present += 1
                continue
            try:
                data = await radar.download(product, t)
            except (httpx.HTTPError, DpcRadarError):
                report.failed += 1
                continue
            if isinstance(data, Absent):
                if now - t > ABSENT_AFTER:
                    absent_file.parent.mkdir(parents=True, exist_ok=True)
                    with absent_file.open("a") as f:
                        f.write(key + "\n")
                    report.lost += 1
                else:
                    report.failed += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.rename(dest)
            report.downloaded += 1
            report.bytes += len(data)
    return report


class MergingArchive:
    """Hourly rain corrected with the gauges, read from an archive on disk.

    Frames are kept in memory, ``cache_frames`` at most (an SRT1 frame is
    about 6.7 MB as float32): the same frames are read for every region of a
    run.
    """

    def __init__(self, folder: Path, *, cache_frames: int = 80) -> None:
        self.folder = folder
        self._cache: OrderedDict[tuple[Path, int], RainGrid] = OrderedDict()
        self._cache_frames = cache_frames

    def read(self, product: str, time: datetime) -> RainGrid | None:
        """One archived frame, or ``None`` if it is not there or not readable."""
        file = path(self.folder, product, time)
        try:
            key = (file, file.stat().st_mtime_ns)  # a file written again is read again
        except FileNotFoundError:
            return None
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        try:
            grid = RainGrid.from_geotiff(file.read_bytes(), product, time)
        except DpcRadarError:  # e.g. an error page saved as a frame
            return None
        self._cache[key] = grid
        if len(self._cache) > self._cache_frames:
            self._cache.popitem(last=False)
        return grid

    def _sample(
        self, product: str, time: datetime, points: list[tuple[float, float]]
    ) -> list[float | None]:
        grid = self.read(product, time)
        return grid.sample(points) if grid is not None else [None] * len(points)

    def hourly(
        self, points: list[tuple[float, float]], start: datetime, end: datetime
    ) -> dict[datetime, list[float | None]]:
        """Rain of every hour ``(h-1, h]`` in ``(start, end]``, per lon/lat point (mm)."""
        out: dict[datetime, list[float | None]] = {}
        first = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        for block_end in times("CUM3", first, end + timedelta(hours=2)):
            hours = [block_end - timedelta(hours=k) for k in (2, 1, 0)]
            merging = self._sample("CUM3", block_end, points)
            radar = [self._sample("SRT1", h, points) for h in hours]
            for j, h in enumerate(hours):
                if start < h <= end:
                    out[h] = [
                        split_block(merging[k], [r[k] for r in radar], j)
                        for k in range(len(points))
                    ]
        return out


def split_block(merging: float | None, radar: list[float | None], j: int) -> float | None:
    """The share of hour ``j`` of a block in its «Merging» total.

    ``radar`` holds the SRT1 rain of each hour of the block. The «Merging» says
    how much, the radar says when; if the radar saw nothing, or missed an hour,
    the total is split in equal parts.
    """
    if merging is None:
        return radar[j]
    measured = [r for r in radar if r is not None]
    total = sum(max(r, 0.0) for r in measured)
    if total < _RADAR_DRY_MM or len(measured) < len(radar):
        return merging / len(radar)
    r = radar[j]
    assert r is not None  # every hour was measured
    return merging * max(r, 0.0) / total


__all__ = [
    "ABSENT_AFTER",
    "STEPS",
    "ArchiveReport",
    "MergingArchive",
    "archive",
    "path",
    "split_block",
    "times",
]
