"""An async client for the DPC radar API: the latest frame, or the frame at a time.

Give it your ``httpx.AsyncClient`` and timeouts, proxies and pooling stay
yours; give it none and it opens (and closes) its own. Network errors and
429/5xx are retried ``retries`` times with exponential backoff, except the one
answer that must never be retried: a frame the DPC no longer keeps (a 500 with
S3's 403 in the body), which comes back as :data:`ABSENT`.

To route every request through your own HTTP layer (a shared retry policy, a
rate limiter, a cache), pass ``send``: an ``async (method, url, **kwargs) ->
httpx.Response``. The client then uses it for every call and does no retrying
of its own. The hook must *return* error responses rather than raise on them,
or the client cannot tell a frame that is gone from a failure.

The client keeps no state between calls: no cache, no logging. Holding on to
the last frame, and deciding what a failure means, is the caller's job.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from types import TracebackType
from typing import Any, Final, final

import httpx

from dpc_radar.errors import DpcRadarError
from dpc_radar.grid import RainGrid
from dpc_radar.products import (
    API_BASE,
    HEADERS,
    LastProduct,
    Product,
    download_body,
    download_url,
    is_expired,
    last_product_url,
    parse_download,
    parse_last_product,
    product,
)

Send = Callable[..., Awaitable[httpx.Response]]
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


@final
class Absent:
    """A frame the DPC no longer keeps (older than about five months)."""

    _instance: Absent | None = None

    def __new__(cls) -> Absent:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "ABSENT"

    def __bool__(self) -> bool:
        return False


#: The answer for a frame that is gone for good: never worth a retry.
ABSENT: Final = Absent()


class RadarClient:
    """The open DPC radar API (``radar-api.protezionecivile.it``)."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        send: Send | None = None,
        retries: int = 3,
        backoff_s: float = 2.0,
        timeout_s: float = 60.0,
        base: str = API_BASE,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._own = client is None and send is None
        self._client = client or (
            None if send is not None else httpx.AsyncClient(follow_redirects=True)
        )
        self._send_hook = send
        self._retries = retries
        self._backoff_s = backoff_s
        self._timeout_s = timeout_s
        self._base = base.rstrip("/")
        self._sleep = sleep

    async def __aenter__(self) -> RadarClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client if this instance opened it."""
        if self._own and self._client is not None:
            await self._client.aclose()

    async def last_product(self, name: str | Product) -> LastProduct | None:
        """Time and period of the newest frame; ``None`` if the API lists none."""
        url, params = last_product_url(name, base=self._base)
        response = await self._send("GET", url, params=params, headers=HEADERS)
        response.raise_for_status()
        return parse_last_product(response.json())

    async def latest(self, name: str | Product, *, cached: RainGrid | None = None) -> RainGrid:
        """The newest frame of a product.

        Pass the frame you already hold as ``cached``: if it is still the
        newest, it comes back as is and nothing is downloaded (a CUM frame is
        about 2 MB and changes every 30 minutes).
        """
        last = await self.last_product(name)
        if last is None:
            raise DpcRadarError(f"the API lists no frame of {product(name).name}")
        if cached is not None and cached.product == last.product and cached.time == last.time:
            return cached
        grid = await self.at(last.product, last.time)
        if isinstance(grid, Absent):  # pragma: no cover - the newest frame is never gone
            raise DpcRadarError(f"the newest {last.product.name} frame is not available")
        return grid

    async def at(self, name: str | Product, time: datetime) -> RainGrid | Absent:
        """The frame of a product issued at ``time`` (aware), or :data:`ABSENT`."""
        raw = await self.download(name, time)
        if isinstance(raw, Absent):
            return raw
        return RainGrid.from_geotiff(raw, name, time)

    async def download(self, name: str | Product, time: datetime) -> bytes | Absent:
        """The GeoTIFF of a frame as published, or :data:`ABSENT` (to archive it as is)."""
        response = await self._send(
            "POST",
            download_url(self._base),
            json=download_body(name, time),
            headers=HEADERS,
        )
        if response.status_code >= 400:
            if is_expired(response.status_code, response.text):
                return ABSENT
            response.raise_for_status()
        link = parse_download(response.json())
        tiff = await self._send("GET", link, timeout=self._timeout_s)
        tiff.raise_for_status()
        return tiff.content

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._send_hook is not None:
            return await self._send_hook(method, url, **kwargs)
        assert self._client is not None
        for attempt in range(self._retries + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt == self._retries:
                    raise
            else:
                if response.status_code not in _RETRY_STATUS:
                    return response
                if is_expired(response.status_code, response.text):
                    return response  # gone for good: retrying cannot help
                if attempt == self._retries:
                    return response
            await self._sleep(self._backoff_s * 2**attempt)
        raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["ABSENT", "Absent", "RadarClient", "Send"]
