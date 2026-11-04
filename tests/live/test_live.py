"""The same behaviour on the real DPC API. Not run by default:

uv run pytest -m live
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dpc_radar import ABSENT, CUM3, SRI, RadarClient, RainGrid

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
