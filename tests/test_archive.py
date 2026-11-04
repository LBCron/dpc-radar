from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest

from dpc_radar import RadarClient
from dpc_radar.archive import MergingArchive, archive, path, split_block, times
from tests.conftest import LONLAT, METRES, TMERC, tiff

NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)
_EXPIRED = (
    '{"error":"S3 error: An error occurred (403) when calling the HeadObject operation: Forbidden"}'
)


def test_path_and_times() -> None:
    t = datetime(2026, 10, 10, 6, tzinfo=UTC)
    assert path(Path("a"), "CUM3", t) == Path("a/CUM3/2026/10/10/CUM3_202610100600.tif")
    start, end = datetime(2026, 10, 10, 1, 30, tzinfo=UTC), datetime(2026, 10, 10, 9, tzinfo=UTC)
    assert [x.hour for x in times("CUM3", start, end)] == [3, 6, 9]
    assert [x.hour for x in times("SRT1", start, end)] == [2, 3, 4, 5, 6, 7, 8, 9]
    on_the_hour = datetime(2026, 10, 10, 3, tzinfo=UTC)
    assert times("CUM3", on_the_hour, on_the_hour) == [on_the_hour]


def fake_dpc(
    gone: set[datetime] | None = None, broken: set[datetime] | None = None
) -> tuple[RadarClient, list[datetime]]:
    """A DPC API that knows every frame, except the ``gone`` and the ``broken`` ones."""
    asked: list[datetime] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/downloadProduct":
            body = json.loads(request.content)
            t = datetime.fromtimestamp(body["productDate"] / 1000, tz=UTC)
            asked.append(t)
            if gone and t in gone:
                return httpx.Response(500, text=_EXPIRED)
            if broken and t in broken:
                return httpx.Response(400, text="bad request")
            return httpx.Response(200, json={"url": f"https://s3.test/{body['productType']}.tif"})
        return httpx.Response(200, content=tiff(np.ones((5, 5))))

    async def no_sleep(_: float) -> None:
        return None

    client = RadarClient(httpx.AsyncClient(transport=httpx.MockTransport(handle)), sleep=no_sleep)
    return client, asked


async def test_archive_downloads_what_is_missing_oldest_first(tmp_path: Path) -> None:
    radar, asked = fake_dpc()
    start, end = datetime(2026, 11, 1, 0, tzinfo=UTC), datetime(2026, 11, 1, 3, tzinfo=UTC)
    report = await archive(tmp_path, radar, start=start, end=end, limit=100, now=NOW)
    assert report.downloaded == 2 + 4  # CUM3 at 00 and 03, SRT1 at 00..03
    assert report.bytes > 0 and asked[0] == start
    assert path(tmp_path, "SRT1", end).exists()
    assert not list(tmp_path.rglob("*.part"))
    again = await archive(tmp_path, radar, start=start, end=end, limit=100, now=NOW)
    assert again.present == 6 and again.requests == 0


async def test_archive_newest_first_and_limit(tmp_path: Path) -> None:
    radar, asked = fake_dpc()
    start, end = datetime(2026, 11, 1, 0, tzinfo=UTC), datetime(2026, 11, 1, 9, tzinfo=UTC)
    report = await archive(
        tmp_path, radar, start=start, end=end, limit=2, newest_first=True, now=NOW
    )
    assert report.requests == 2 and asked == [end, end - timedelta(hours=3)]


async def test_archive_writes_down_frames_that_are_gone(tmp_path: Path) -> None:
    old = datetime(2026, 5, 1, 0, tzinfo=UTC)
    young = NOW - timedelta(days=1)
    young = young.replace(hour=0)
    radar, _ = fake_dpc(gone={old, young})
    report = await archive(tmp_path, radar, start=old, end=old, limit=10, now=NOW)
    assert report.lost == 2  # CUM3 and SRT1 at the same time
    assert (tmp_path / "CUM3" / "absent.txt").read_text() == "202605010000\n"
    young_report = await archive(tmp_path, radar, start=young, end=young, limit=10, now=NOW)
    assert young_report.failed == 2 and young_report.lost == 0  # may not be published yet
    again = await archive(tmp_path, radar, start=old, end=old, limit=10, now=NOW)
    assert again.present == 2 and again.requests == 0


async def test_archive_reads_limens_absent_list_and_counts_failures(tmp_path: Path) -> None:
    t = datetime(2026, 11, 1, 0, tzinfo=UTC)
    (tmp_path / "CUM3").mkdir()
    (tmp_path / "CUM3" / "assenti.txt").write_text("202611010000\n")
    radar, asked = fake_dpc(broken={t})
    report = await archive(tmp_path, radar, start=t, end=t, limit=10, now=NOW)
    assert report.present == 1 and report.failed == 1 and asked == [t]


# --- reading the archive ---------------------------------------------------------------


def _put(folder: Path, product: str, t: datetime, value: float) -> None:
    dest = path(folder, product, t)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if product == "CUM3":
        dest.write_bytes(tiff(np.full((20, 20), value), LONLAT))
    else:  # SRT1 on the 1 km grid, around 42°N 12.5°E
        dest.write_bytes(tiff(np.full((20, 20), value), METRES, TMERC))


def test_read_caches_and_tolerates_bad_files(tmp_path: Path) -> None:
    t = datetime(2026, 10, 10, 3, tzinfo=UTC)
    store = MergingArchive(tmp_path, cache_frames=1)
    assert store.read("CUM3", t) is None
    _put(tmp_path, "CUM3", t, 9.0)
    first = store.read("CUM3", t)
    assert first is not None and store.read("CUM3", t) is first
    _put(tmp_path, "CUM3", t + timedelta(hours=3), 1.0)
    assert store.read("CUM3", t + timedelta(hours=3)) is not None  # pushes the first out
    bad = path(tmp_path, "SRT1", t)
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('{"error": "an error page saved as a frame"}')
    assert store.read("SRT1", t) is None


def test_hourly_splits_the_merging_like_the_radar(tmp_path: Path) -> None:
    block = datetime(2026, 10, 10, 6, tzinfo=UTC)  # the CUM3 of 03:00-06:00
    _put(tmp_path, "CUM3", block, 30.0)
    for hour, mm in ((4, 1.0), (5, 2.0), (6, 3.0)):
        _put(tmp_path, "SRT1", block.replace(hour=hour), mm)
    point = (15.1, 37.9)  # inside the CUM3 piece, outside the SRT1 one
    radar_point = (12.5, 42.0)  # inside the SRT1 piece, outside the CUM3 one
    store = MergingArchive(tmp_path)
    rain = store.hourly([point, radar_point], block - timedelta(hours=3), block)
    assert sorted(rain) == [block.replace(hour=h) for h in (4, 5, 6)]
    assert [rain[block.replace(hour=h)][0] for h in (4, 5, 6)] == [10.0, 10.0, 10.0]
    assert [rain[block.replace(hour=h)][1] for h in (4, 5, 6)] == [1.0, 2.0, 3.0]


def test_hourly_with_both_products_on_the_same_point(tmp_path: Path) -> None:
    """The same point covered by both: the radar's hours, scaled to the gauges' total."""
    block = datetime(2026, 10, 10, 6, tzinfo=UTC)
    dest = path(tmp_path, "CUM3", block)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(tiff(np.full((20, 20), 60.0), METRES, TMERC))
    for hour, mm in ((4, 1.0), (5, 2.0), (6, 3.0)):
        _put(tmp_path, "SRT1", block.replace(hour=hour), mm)
    rain = MergingArchive(tmp_path).hourly([(12.5, 42.0)], block - timedelta(hours=1), block)
    assert rain == {block.replace(hour=6): [30.0]}  # 60 x 3/6


@pytest.mark.parametrize(
    ("merging", "radar", "j", "expected"),
    [
        (30.0, [1.0, 2.0, 3.0], 2, 15.0),  # the radar says when
        (30.0, [0.0, 0.0, 0.0], 0, 10.0),  # the radar saw nothing: equal parts
        (30.0, [1.0, None, 3.0], 0, 10.0),  # the radar missed an hour: equal parts
        (None, [1.0, 2.0, 3.0], 1, 2.0),  # no «Merging»: the radar
        (None, [1.0, None, 3.0], 1, None),  # neither: not measured
        (30.0, [-1.0, 2.0, 2.0], 0, 0.0),  # a negative radar value counts as zero
    ],
)
def test_split_block(
    merging: float | None, radar: list[float | None], j: int, expected: float | None
) -> None:
    assert split_block(merging, radar, j) == expected
