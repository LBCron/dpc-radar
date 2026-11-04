"""The same behaviour on the real DPC API. Not run by default:

uv run pytest -m live
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dpc_radar import ABSENT, CUM3, SRI, RadarClient, RainGrid, bulletins
from dpc_radar.archive import MergingArchive, archive
from dpc_radar.bulletins import COLOURS

pytestmark = pytest.mark.live


async def test_latest_sri_is_a_national_frame() -> None:
    async with RadarClient() as radar:
        frame = await radar.latest("SRI")
    assert frame.product is SRI
    assert frame.data.shape == (1400, 1200)  # 1 km, Transverse Mercator
    assert (datetime.now(UTC) - frame.time).total_seconds() < 6 * 3600


async def test_cum3_at_a_time_and_its_check() -> None:
    async with RadarClient() as radar:
        last = await radar.last_product("CUM3")
        assert last is not None
        frame = await radar.at(CUM3, last.time)
        previous = await radar.at(CUM3, last.time - last.period)
    assert isinstance(frame, RainGrid) and isinstance(previous, RainGrid)
    assert frame.data.shape == (1233, 1341)  # 0.01°, EPSG:4326
    assert frame.check(previous, max_step=150).usable


async def test_a_frame_older_than_the_retention_is_absent() -> None:
    async with RadarClient(retries=0) as radar:
        assert await radar.at("CUM3", datetime(2026, 1, 1, tzinfo=UTC)) is ABSENT


async def test_archive_three_hours_and_read_them(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    start = (now - timedelta(hours=8)).replace(minute=0, second=0, microsecond=0)
    start -= timedelta(hours=start.hour % 3)  # a CUM3 block boundary
    end = start + timedelta(hours=3)
    async with RadarClient() as radar:
        report = await archive(tmp_path, radar, start=start, end=end, limit=20)
    assert report.downloaded == 2 + 4 and report.failed == 0
    rain = MergingArchive(tmp_path).hourly([(12.5, 42.0)], start, end)
    assert sorted(rain) == [start + timedelta(hours=h) for h in (1, 2, 3)]


async def test_latest_bulletin_covers_italy() -> None:
    bulletin = await bulletins.latest()
    assert len(bulletin.days()) == 2
    assert len([z for z in bulletin.zones if z.valid == bulletin.days()[0]]) > 150
    rome = bulletin.zone_at(12.50, 41.90)
    assert rome is not None and rome.level in COLOURS
