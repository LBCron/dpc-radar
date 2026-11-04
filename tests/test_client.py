from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from typing import Any

import httpx
import numpy as np
import pytest

from dpc_radar import ABSENT, CUM24, Absent, DpcRadarError, RadarClient
from tests.conftest import T0, grid, tiff

_LINK = "https://s3-prod-dpc-radar.s3.eu-south-1.amazonaws.com/CUM24.tif?sig=x"
_EXPIRED = (
    '{"error":"S3 error: An error occurred (403) when calling the HeadObject operation: Forbidden"}'
)
_T0_MS = int(T0.timestamp() * 1000)
_FRAME = tiff(np.full((20, 20), 12.0))


def api(
    *,
    last: dict[str, Any] | None = None,
    link_status: int = 200,
    link_body: str = "",
    fail_first: int = 0,
) -> tuple[httpx.MockTransport, Counter[str]]:
    """A fake DPC API; counts the calls by path."""
    calls: Counter[str] = Counter()
    if last is None:
        last = {
            "total": 1,
            "lastProducts": [{"productType": "CUM24", "time": _T0_MS, "period": "PT30M"}],
        }

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls[path] += 1
        if calls[path] <= fail_first:
            return httpx.Response(503)
        if path == "/findLastProductByType":
            assert request.headers["origin"] == "https://radar.protezionecivile.gov.it"
            return httpx.Response(200, json=last)
        if path == "/downloadProduct":
            body = json.loads(request.content)
            assert body["productDate"] == _T0_MS
            if link_status != 200:
                return httpx.Response(link_status, text=link_body)
            return httpx.Response(200, json={"url": _LINK})
        if path == "/CUM24.tif":
            return httpx.Response(200, content=_FRAME)
        return httpx.Response(404)

    return httpx.MockTransport(handle), calls


async def no_sleep(_: float) -> None:
    return None


def client(transport: httpx.MockTransport, **kwargs: Any) -> RadarClient:
    return RadarClient(httpx.AsyncClient(transport=transport), sleep=no_sleep, **kwargs)


async def test_latest_downloads_the_newest_frame() -> None:
    transport, calls = api()
    async with client(transport) as radar:
        frame = await radar.latest("cum24")
    assert frame.product is CUM24 and frame.time == T0
    assert frame.sample([(15.1, 37.9)]) == [12.0]
    assert calls == {"/findLastProductByType": 1, "/downloadProduct": 1, "/CUM24.tif": 1}


async def test_latest_reuses_the_frame_already_held() -> None:
    transport, calls = api()
    held = grid(np.zeros((20, 20)))
    async with client(transport) as radar:
        assert await radar.latest(CUM24, cached=held) is held
        other = grid(np.zeros((20, 20)), name="CUM3")
        assert (await radar.latest(CUM24, cached=other)).product is CUM24
    assert calls["/findLastProductByType"] == 2 and calls["/downloadProduct"] == 1


async def test_latest_when_the_api_lists_nothing() -> None:
    transport, _ = api(last={"total": 0, "lastProducts": []})
    async with client(transport) as radar:
        assert await radar.last_product("CUM24") is None
        with pytest.raises(DpcRadarError, match="no frame"):
            await radar.latest("CUM24")


async def test_a_frame_that_is_gone_is_absent_and_not_retried() -> None:
    transport, calls = api(link_status=500, link_body=_EXPIRED)
    async with client(transport, retries=3) as radar:
        assert await radar.at("CUM24", T0) is ABSENT
        assert await radar.download("CUM24", T0) is ABSENT
    assert calls["/downloadProduct"] == 2  # once per call, never retried


async def test_other_errors_raise() -> None:
    transport, _ = api(link_status=400, link_body='{"error":"bad request"}')
    async with client(transport) as radar:
        with pytest.raises(httpx.HTTPStatusError):
            await radar.at("CUM24", T0)


async def test_retries_5xx_then_succeeds() -> None:
    transport, calls = api(fail_first=2)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    radar = RadarClient(httpx.AsyncClient(transport=transport), sleep=sleep, backoff_s=1)
    frame = await radar.at("CUM24", T0)
    assert not isinstance(frame, Absent)
    assert calls["/downloadProduct"] == 3 and waits[:2] == [1, 2]


async def test_gives_up_after_the_retries() -> None:
    transport, calls = api(fail_first=10)
    async with client(transport, retries=2) as radar:
        with pytest.raises(httpx.HTTPStatusError):
            await radar.last_product("CUM24")
    assert calls["/findLastProductByType"] == 3


def _flaky(failures: int) -> tuple[httpx.MockTransport, Callable[[], int]]:
    seen = 0
    transport, _ = api()

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen += 1
        if seen <= failures:
            raise httpx.ConnectError("reset", request=request)
        return transport.handle_request(request)

    return httpx.MockTransport(handle), lambda: seen


async def test_retries_network_errors() -> None:
    transport, seen = _flaky(1)
    async with client(transport) as radar:
        assert await radar.last_product("CUM24") is not None
    assert seen() == 2


async def test_network_errors_raise_after_the_retries() -> None:
    transport, _ = _flaky(10)
    async with client(transport, retries=1) as radar:
        with pytest.raises(httpx.ConnectError):
            await radar.last_product("CUM24")


async def test_send_hook_takes_every_request() -> None:
    transport, _ = api()
    http = httpx.AsyncClient(transport=transport)
    seen: list[str] = []

    async def send(method: str, url: str, **kwargs: Any) -> httpx.Response:
        seen.append(method)
        return await http.request(method, url, **kwargs)

    async with RadarClient(send=send) as radar:
        frame = await radar.latest("CUM24")
    assert frame.time == T0 and seen == ["GET", "POST", "GET"]
    await http.aclose()


async def test_closes_only_the_client_it_opened() -> None:
    transport, _ = api()
    mine = httpx.AsyncClient(transport=transport)
    async with RadarClient(mine):
        pass
    assert not mine.is_closed
    own = RadarClient()
    await own.aclose()
    assert own._client is not None and own._client.is_closed
    await mine.aclose()


def test_absent_is_one_falsy_value() -> None:
    assert Absent() is ABSENT
    assert not ABSENT
    assert repr(ABSENT) == "ABSENT"
