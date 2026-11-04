from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dpc_radar import products as p
from dpc_radar.errors import DpcRadarError

# Answers recorded from the live API on 2026-11-04.
_LAST_SRI = {
    "total": 1,
    "lastProducts": [{"productType": "SRI", "time": 1793789400000, "period": "PT5M"}],
}
_LAST_CUM3 = {
    "total": 1,
    "lastProducts": [{"productType": "CUM3", "time": 1793788200000, "period": "PT30M"}],
}
_LAST_CUM1 = {"total": 0, "lastProducts": []}
_EXPIRED = (
    '{"error":"S3 error: An error occurred (403) when calling the HeadObject operation: Forbidden"}'
)


def test_products_by_name_any_case() -> None:
    assert p.product("cum3") is p.CUM3
    assert p.product(p.SRI) is p.SRI
    assert p.CUM24.hours == 24 and p.CUM24.merging
    assert p.SRI.hours is None and not p.SRI.merging


def test_unknown_product_names_the_known_ones() -> None:
    with pytest.raises(DpcRadarError, match=r"CUM1.*known: SRI"):
        p.product("CUM1")


def test_last_product_request() -> None:
    url, params = p.last_product_url("cum6")
    assert url == "https://radar-api.protezionecivile.it/findLastProductByType"
    assert params == {"type": "CUM6"}
    assert (
        p.last_product_url(p.SRI, base="http://mirror")[0] == "http://mirror/findLastProductByType"
    )


def test_download_request_uses_epoch_milliseconds() -> None:
    at = datetime(2026, 11, 4, 10, 30, tzinfo=UTC)
    assert p.download_url() == "https://radar-api.protezionecivile.it/downloadProduct"
    assert p.download_body("CUM3", at) == {"productType": "CUM3", "productDate": 1793788200000}


def test_epoch_round_trip_and_naive_datetimes_refused() -> None:
    at = datetime(2026, 11, 4, 10, 50, tzinfo=UTC)
    assert p.from_epoch_ms(p.to_epoch_ms(at)) == at
    assert p.from_epoch_ms("1793789400000") == at
    with pytest.raises(DpcRadarError, match="naive"):
        p.to_epoch_ms(datetime(2026, 11, 4, 10, 50))


@pytest.mark.parametrize(
    ("text", "expected"),
    [("PT5M", timedelta(minutes=5)), ("PT30M", timedelta(minutes=30)), ("PT1H", timedelta(hours=1)),
     ("PT1H30M", timedelta(minutes=90))],
)  # fmt: skip
def test_parse_period(text: str, expected: timedelta) -> None:
    assert p.parse_period(text) == expected


@pytest.mark.parametrize("text", ["PT", "P1D", "5M", ""])
def test_parse_period_refuses_the_rest(text: str) -> None:
    with pytest.raises(DpcRadarError):
        p.parse_period(text)


def test_parse_last_product() -> None:
    sri = p.parse_last_product(_LAST_SRI)
    assert sri == p.LastProduct(
        product=p.SRI, time=datetime(2026, 11, 4, 10, 50, tzinfo=UTC), period=timedelta(minutes=5)
    )
    cum = p.parse_last_product(_LAST_CUM3)
    assert cum is not None and cum.period == timedelta(minutes=30)


def test_parse_last_product_empty_list_is_none() -> None:
    assert p.parse_last_product(_LAST_CUM1) is None


@pytest.mark.parametrize(
    "payload",
    [{}, {"lastProducts": "x"}, {"lastProducts": [{"productType": "SRI"}]},
     {"lastProducts": [{"productType": "SRI", "time": "soon", "period": "PT5M"}]}],
)  # fmt: skip
def test_parse_last_product_refuses_malformed_answers(payload: dict[str, object]) -> None:
    with pytest.raises(DpcRadarError):
        p.parse_last_product(payload)


def test_parse_download() -> None:
    link = "https://s3-prod-dpc-radar.s3.eu-south-1.amazonaws.com/rete_terra/x.tif?sig=1"
    assert p.parse_download({"bucket": "b", "key": "k", "url": link}) == link
    with pytest.raises(DpcRadarError):
        p.parse_download({"error": "boom"})


def test_expired_frame_is_a_500_with_s3_403_in_the_body() -> None:
    assert p.is_expired(500, _EXPIRED)
    assert not p.is_expired(500, "Internal Server Error")
    assert not p.is_expired(200, _EXPIRED)
