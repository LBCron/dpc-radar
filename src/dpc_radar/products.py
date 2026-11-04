"""The DPC radar products, and the requests and answers of the open radar API.

Pure: nothing here does I/O. A client sends the requests built here and hands
the answers back to the parsers.

The API (``radar-api.protezionecivile.it``) has two calls that matter:

* ``GET /findLastProductByType?type=<product>`` gives the time of the latest
  frame and its period;
* ``POST /downloadProduct`` with ``{"productType", "productDate"}`` gives a
  short-lived S3 link to the GeoTIFF of one frame.

A frame the DPC no longer keeps (about five months back) does not answer 404:
it answers **500** with S3's ``403 Forbidden`` in the body. :func:`is_expired`
tells the two failures apart, because an expired frame must not be retried.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from dpc_radar.errors import DpcRadarError

API_BASE: Final = "https://radar-api.protezionecivile.it"

#: Sent on every request. The API answered without it in November 2026, but the
#: DPC's own viewer sends it, and an API that starts checking it again would
#: otherwise fail every call.
HEADERS: Final = {"origin": "https://radar.protezionecivile.gov.it"}

#: Pixels with no data in every product.
NODATA: Final = -9999.0


@dataclass(frozen=True, slots=True)
class Product:
    """One DPC radar product."""

    name: str
    #: Accumulation window: ``None`` for an intensity (SRI, mm/h).
    hours: int | None
    #: ``True`` for the «Merging» of radar and rain gauges, ``False`` for radar only.
    merging: bool
    description: str

    @property
    def period(self) -> timedelta:
        """How often a new frame comes out: 30 minutes for the «Merging», 5 for radar only.

        The same as the ``period`` the API returns (``PT30M``, ``PT5M``): the
        frame before one issued at ``t`` is the one issued at ``t - period``.
        """
        return timedelta(minutes=30 if self.merging else 5)


#: Surface Rainfall Intensity: radar only, mm/h, a frame every 5 minutes.
SRI: Final = Product("SRI", None, False, "rain intensity, radar only (mm/h)")
#: Surface Rainfall Total over the last hour: radar only, mm, every 5 minutes.
SRT1: Final = Product("SRT1", 1, False, "rain over the last hour, radar only (mm)")
CUM3: Final = Product("CUM3", 3, True, "rain over 3 hours, radar + rain gauges (mm)")
CUM6: Final = Product("CUM6", 6, True, "rain over 6 hours, radar + rain gauges (mm)")
CUM12: Final = Product("CUM12", 12, True, "rain over 12 hours, radar + rain gauges (mm)")
CUM24: Final = Product("CUM24", 24, True, "rain over 24 hours, radar + rain gauges (mm)")

#: Every product this package knows, by name. There is no ``CUM1``: the API
#: answers it with an empty list.
PRODUCTS: Final = {p.name: p for p in (SRI, SRT1, CUM3, CUM6, CUM12, CUM24)}


def product(name: str | Product) -> Product:
    """The :class:`Product` called ``name``."""
    if isinstance(name, Product):
        return name
    try:
        return PRODUCTS[name.upper()]
    except KeyError:
        known = ", ".join(PRODUCTS)
        raise DpcRadarError(f"unknown DPC radar product {name!r}; known: {known}") from None


@dataclass(frozen=True, slots=True)
class LastProduct:
    """What ``findLastProductByType`` says about the latest frame."""

    product: Product
    time: datetime
    period: timedelta


def last_product_url(name: str | Product, *, base: str = API_BASE) -> tuple[str, dict[str, str]]:
    """URL and query parameters of the latest-frame request."""
    return f"{base}/findLastProductByType", {"type": product(name).name}


def download_url(base: str = API_BASE) -> str:
    """URL of the download-link request (a ``POST``)."""
    return f"{base}/downloadProduct"


def download_body(name: str | Product, at: datetime) -> dict[str, Any]:
    """JSON body of the download-link request for the frame issued ``at``."""
    return {"productType": product(name).name, "productDate": to_epoch_ms(at)}


def to_epoch_ms(at: datetime) -> int:
    """``at`` as the API's milliseconds since the epoch; ``at`` must be aware."""
    if at.tzinfo is None:
        raise DpcRadarError("naive datetime: pass an aware one (UTC)")
    return round(at.timestamp() * 1000)


def from_epoch_ms(ms: int | float | str) -> datetime:
    """The API's milliseconds since the epoch as an aware UTC datetime."""
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)


_PERIOD = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?$")


def parse_period(text: str) -> timedelta:
    """An ISO 8601 period of hours and minutes (``PT5M``, ``PT30M``, ``PT1H``)."""
    m = _PERIOD.match(text)
    if m is None or not any(m.groups()):
        raise DpcRadarError(f"unexpected period {text!r}")
    return timedelta(hours=int(m[1] or 0), minutes=int(m[2] or 0))


def parse_last_product(payload: dict[str, Any]) -> LastProduct | None:
    """The latest frame, or ``None`` when the API lists none."""
    rows = payload.get("lastProducts")
    if not isinstance(rows, list):
        raise DpcRadarError(f"no 'lastProducts' in the answer: {payload!r}")
    if not rows:
        return None
    row = rows[0]
    try:
        return LastProduct(
            product=product(str(row["productType"])),
            time=from_epoch_ms(row["time"]),
            period=parse_period(str(row["period"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DpcRadarError(f"unexpected latest-frame answer: {row!r}") from exc


def parse_download(payload: dict[str, Any]) -> str:
    """The S3 link of a frame, from the download-link answer."""
    url = payload.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise DpcRadarError(f"no download link in the answer: {payload!r}")
    return url


def is_expired(status: int, body: str) -> bool:
    """``True`` when the API says the frame is no longer kept.

    That is a 500 carrying S3's 403 in the body, not a 404. Any other failure
    is worth retrying; this one never is.
    """
    return status >= 400 and ("Forbidden" in body or "(403)" in body)


__all__ = [
    "API_BASE",
    "CUM3",
    "CUM6",
    "CUM12",
    "CUM24",
    "HEADERS",
    "NODATA",
    "PRODUCTS",
    "SRI",
    "SRT1",
    "LastProduct",
    "Product",
    "download_body",
    "download_url",
    "from_epoch_ms",
    "is_expired",
    "last_product_url",
    "parse_download",
    "parse_last_product",
    "parse_period",
    "product",
    "to_epoch_ms",
]
