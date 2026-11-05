from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from dpc_radar import DpcRadarError
from dpc_radar import bulletins as b

_NONE = "Assenza di fenomeni significativi prevedibili / NESSUNA ALLERTA"


def _feature(name: str | None, square: tuple[float, float], **levels: str) -> dict[str, Any]:
    x, y = square
    ring = [[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1], [x, y]]
    props = {
        "Rappresentata nella mappa": levels.get("map", _NONE),
        "Per rischio idrogeologico": levels.get("hydro", _NONE),
        "Per rischio idraulico": levels.get("river", _NONE),
        "Per rischio temporali": levels.get("storm", _NONE),
        "Comuni": ["Melito di Porto Salvo", "Bagaladi"],
    }
    if name is not None:
        props["Nome zona"] = name
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": props,
    }


TODAY = {
    "features": [
        _feature(
            "Calabria-Versante tirrenico meridionale",
            (15.0, 37.5),
            map="Moderata per rischio idrogeologico / ALLERTA ARANCIONE",
            hydro="Moderata per rischio idrogeologico / ALLERTA ARANCIONE",
            storm="Ordinaria per rischio temporali / ALLERTA GIALLA",
        ),
        _feature("Bacini Tordino Vomano", (13.0, 42.0)),
        _feature(None, (10.0, 40.0)),  # no name: skipped
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1, 1]},
            "properties": {"Nome zona": "a point"},
        },  # not a polygon: skipped
    ]
}
TOMORROW = {"features": [_feature("Bacini Tordino Vomano", (13.0, 42.0), map="ALLERTA ROSSA")]}
NAME = "20261010_1417_today.json"


def test_level_from_the_text() -> None:
    assert b.level("Ordinaria per rischio temporali / ALLERTA GIALLA") == 1
    assert b.level("moderata / allerta arancione") == 2
    assert b.level("Elevata per rischio idraulico / ALLERTA ROSSA") == 3
    assert b.level(_NONE) == 0 and b.level(None) == 0


def test_issued_from_the_file_name() -> None:
    when = b.issued(NAME)
    assert when is not None and (when.hour, when.minute) == (14, 17)
    offset = when.utcoffset()
    assert offset is not None and offset.total_seconds() == 7200  # CEST
    assert b.issued("20261010_1417_tomorrow.json") is None


def test_zones_from_geojson() -> None:
    zones = b.zones_from_geojson(TODAY, date(2026, 10, 10))
    assert [z.name for z in zones] == [
        "Calabria-Versante tirrenico meridionale",
        "Bacini Tordino Vomano",
    ]
    calabria = zones[0]
    assert (
        calabria.level,
        calabria.hydrogeological,
        calabria.hydraulic,
        calabria.thunderstorms,
    ) == (2, 2, 0, 1)
    assert calabria.colour == "orange" and calabria.municipalities[0] == "Melito di Porto Salvo"
    assert calabria.geometry.geom_type == "MultiPolygon"


def test_bulletin_zone_at_and_alerts() -> None:
    day = date(2026, 10, 10)
    bulletin = b.Bulletin(
        name="20261010_1417",
        issued=b.issued(NAME),  # type: ignore[arg-type]
        zones=b.zones_from_geojson(TODAY, day) + b.zones_from_geojson(TOMORROW, date(2026, 10, 11)),
    )
    assert bulletin.days() == [day, date(2026, 10, 11)]
    melito = bulletin.zone_at(15.78, 37.92)
    assert melito is not None and melito.level == 2
    tomorrow = bulletin.zone_at(13.5, 42.5, date(2026, 10, 11))
    assert tomorrow is not None and tomorrow.colour == "red"
    assert bulletin.zone_at(0.0, 0.0) is None
    assert [z.name for z in bulletin.alerts()] == ["Calabria-Versante tirrenico meridionale"]
    assert bulletin.alerts(at_least=3) == []


def test_latest_name_from_a_listing() -> None:
    listing = {
        "tree": [
            {"path": p} for p in (NAME, "20261011_1430_today.json", "20261011_1430_style.json")
        ]
    }
    assert b.latest_name_from(listing) == "20261011_1430_today.json"
    assert b.latest_name_from({"tree": []}) is None


def github(layout_ok: bool = True) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/git/trees/master"):
            files = "files" if layout_ok else "elsewhere"
            return httpx.Response(200, json={"tree": [{"path": files, "sha": "f1"}]})
        if url.endswith("/git/trees/f1"):
            return httpx.Response(200, json={"tree": [{"path": "geojson", "sha": "g1"}]})
        if url.endswith("/git/trees/g1"):
            return httpx.Response(200, json={"tree": [{"path": NAME}]})
        if url.endswith("_today.json"):
            return httpx.Response(200, json=TODAY)
        if url.endswith("_tomorrow.json"):
            return httpx.Response(200, json=TOMORROW)
        return httpx.Response(404)

    return httpx.MockTransport(handle)


async def test_latest_bulletin() -> None:
    async with httpx.AsyncClient(transport=github()) as client:
        bulletin = await b.latest(client)
    assert bulletin.name == "20261010_1417" and len(bulletin.zones) == 3


async def test_errors() -> None:
    async with httpx.AsyncClient(transport=github(layout_ok=False)) as client:
        with pytest.raises(DpcRadarError, match="layout"):
            await b.latest_name(client)
    with pytest.raises(DpcRadarError, match="file name"):
        await b.fetch("readme.md")

    def empty(request: httpx.Request) -> httpx.Response:
        if "trees/g1" in str(request.url):
            return httpx.Response(200, json={"tree": []})
        return github().handle_request(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(empty)) as client:
        with pytest.raises(DpcRadarError, match="no bulletin"):
            await b.latest(client)


async def test_opens_and_closes_its_own_client(monkeypatch: pytest.MonkeyPatch) -> None:
    real = httpx.AsyncClient

    def patched(**kwargs: Any) -> httpx.AsyncClient:
        return real(transport=github(), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    assert await b.latest_name() == NAME
    assert len((await b.fetch(NAME)).zones) == 3
