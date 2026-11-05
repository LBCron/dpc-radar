from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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


_LINK = "https://s3.test/CUM3.tif?X-Amz-Signature=secret"


def _hook(fail: Callable[[], httpx.Response]) -> RadarClient:
    async def send(*_: Any, **__: Any) -> httpx.Response:
        return fail()

    return RadarClient(send=send)


@pytest.mark.parametrize(
    "answer",
    [
        lambda: httpx.Response(200, text="<html>maintenance</html>"),
        lambda: httpx.Response(200, json=["not", "an", "object"]),
    ],
)
async def test_a_page_that_is_not_json_is_one_failed_frame(
    tmp_path: Path, answer: Callable[[], httpx.Response], caplog: pytest.LogCaptureFixture
) -> None:
    t = datetime(2026, 11, 1, 0, tzinfo=UTC)
    report = await archive(tmp_path, _hook(answer), start=t, end=t, limit=10, now=NOW)
    assert report.failed == 2 and report.downloaded == 0
    assert "CUM3 202611010000 failed: DpcRadarError" in caplog.text


@pytest.mark.parametrize("error", [TimeoutError("slow"), PermissionError("no"), OSError("net")])
async def test_a_hook_that_raises_oserror_is_one_failed_frame(
    tmp_path: Path, error: OSError
) -> None:
    def fail() -> httpx.Response:
        raise error

    t = datetime(2026, 11, 1, 0, tzinfo=UTC)
    report = await archive(tmp_path, _hook(fail), start=t, end=t, limit=10, now=NOW)
    assert report.failed == 2


async def test_another_exception_from_a_hook_ends_the_run(tmp_path: Path) -> None:
    def fail() -> httpx.Response:
        raise ZeroDivisionError

    t = datetime(2026, 11, 1, 0, tzinfo=UTC)
    with pytest.raises(ZeroDivisionError):
        await archive(tmp_path, _hook(fail), start=t, end=t, limit=10, now=NOW)


async def test_the_log_says_why_and_never_holds_the_signed_link(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/downloadProduct":
            return httpx.Response(200, json={"url": _LINK})
        return httpx.Response(403, text="denied")

    radar = RadarClient(httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    t = datetime(2026, 11, 1, 0, tzinfo=UTC)
    report = await archive(tmp_path, radar, start=t, end=t, limit=10, now=NOW)
    assert report.failed == 2
    assert "HTTP 403 from s3.test" in caplog.text
    assert "secret" not in caplog.text


async def test_the_log_says_a_young_frame_is_not_published_yet(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    t = NOW.replace(hour=0)
    radar, _ = fake_dpc(gone={t})
    await archive(tmp_path, radar, start=t, end=t, limit=10, now=NOW)
    assert "is not published yet" in caplog.text


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


def test_read_touches_the_cache_only_under_the_lock(tmp_path: Path) -> None:
    """Without the lock: KeyError, or a segfault in popitem, under heavy thread switching."""

    class Guarded(OrderedDict[Any, Any]):
        def __contains__(self, key: object) -> bool:
            assert store._lock.locked()
            return super().__contains__(key)

        def __setitem__(self, key: Any, value: Any) -> None:
            assert store._lock.locked()
            super().__setitem__(key, value)

        def move_to_end(self, key: Any, last: bool = True) -> None:
            assert store._lock.locked()
            super().move_to_end(key, last)

        def popitem(self, last: bool = True) -> tuple[Any, Any]:
            assert store._lock.locked()
            return super().popitem(last)

    t = datetime(2026, 10, 10, 3, tzinfo=UTC)
    later = t + timedelta(hours=3)
    _put(tmp_path, "CUM3", t, 1.0)
    _put(tmp_path, "CUM3", later, 2.0)
    store = MergingArchive(tmp_path, cache_frames=1)
    store._cache = Guarded()
    assert store.read("CUM3", t) is store.read("CUM3", t)  # miss, then hit
    assert store.read("CUM3", later) is not None  # evicts
    assert len(store._cache) == 1


def test_one_archive_shared_between_threads(tmp_path: Path) -> None:
    moments = [datetime(2026, 10, 10, tzinfo=UTC) + timedelta(hours=3 * i) for i in range(4)]
    for t in moments:
        _put(tmp_path, "CUM3", t, 1.0)
    store = MergingArchive(tmp_path, cache_frames=1)
    errors: list[Exception] = []

    def work() -> None:
        try:
            for i in range(40):
                assert store.read("CUM3", moments[i % 4]) is not None
        except Exception as exc:  # pragma: no cover - only if the lock is broken
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


@pytest.mark.parametrize("step", ["stat", "read"])
def test_an_os_error_is_one_unreadable_frame(
    tmp_path: Path, step: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    t = datetime(2026, 10, 10, 3, tzinfo=UTC)
    _put(tmp_path, "CUM3", t, 1.0)

    def denied(*_: Any, **__: Any) -> Any:
        raise PermissionError("another uid wrote it")

    monkeypatch.setattr(Path, "stat" if step == "stat" else "read_bytes", denied)
    with caplog.at_level(logging.WARNING, logger="dpc_radar.archive"):
        assert MergingArchive(tmp_path).read("CUM3", t) is None
    assert "not readable: PermissionError" in caplog.text


def test_a_frame_that_is_not_a_tiff_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    t = datetime(2026, 10, 10, 3, tzinfo=UTC)
    bad = path(tmp_path, "CUM3", t)
    bad.parent.mkdir(parents=True)
    bad.write_text("<html>error page</html>")
    assert MergingArchive(tmp_path).read("CUM3", t) is None
    assert "not readable: DpcRadarError" in caplog.text


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
