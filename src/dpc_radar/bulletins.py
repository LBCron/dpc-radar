"""The DPC criticality bulletin: the official hydro-geological alert, as data.

Every day by 16:00 the Civil Protection Department publishes the national
criticality bulletin (*bollettino di criticità e allerta*) in its GitHub
repository `pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica
<https://github.com/pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica>`_
(CC BY 4.0): one GeoJSON for today and one for tomorrow, with the ~190 alert
zones of Italy. It is already structured: no language model reads it, it is
imported.

Each zone carries four levels, written in words in the file ("Ordinaria per
rischio idrogeologico / ALLERTA GIALLA"): the one on the map, and the ones for
hydro-geological risk (landslides), hydraulic risk (rivers) and thunderstorms.
They become numbers: 0 none, 1 yellow, 2 orange, 3 red.

This is the **official** alert. Whatever a model computes from the radar is
not, and should be shown next to it, never instead of it.

Needs ``shapely`` for the zone shapes: ``pip install "dpc-radar[bulletins]"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

import httpx

from dpc_radar.errors import DpcRadarError

REPO: Final = "pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica"
API: Final = f"https://api.github.com/repos/{REPO}"
RAW: Final = f"https://raw.githubusercontent.com/{REPO}/master/files/geojson"
ROME: Final = ZoneInfo("Europe/Rome")
#: ``20261104_1456_today.json``: issued on 4 November 2026 at 14:56, Italian time.
_NAME = re.compile(r"^(\d{8})_(\d{4})_today\.json$")
#: The colour is written in the text of each level.
_LEVELS = (("ROSSA", 3), ("ARANCIONE", 2), ("GIALLA", 1))
#: Level number → colour, for display.
COLOURS: Final = {0: "none", 1: "yellow", 2: "orange", 3: "red"}


def _shapely() -> Any:
    try:
        import shapely.geometry
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise DpcRadarError(
            'reading bulletin zones needs shapely: pip install "dpc-radar[bulletins]"'
        ) from exc
    return shapely.geometry


def level(text: str | None) -> int:
    """«Ordinaria per rischio temporali / ALLERTA GIALLA» → 1; no alert → 0."""
    upper = (text or "").upper()
    return next((n for colour, n in _LEVELS if f"ALLERTA {colour}" in upper), 0)


def issued(name: str) -> datetime | None:
    """When a bulletin was issued, from its file name (Italian time); ``None`` if not one."""
    m = _NAME.match(name)
    if m is None:
        return None
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M").replace(tzinfo=ROME)


@dataclass(frozen=True, slots=True)
class Zone:
    """One alert zone on one day."""

    valid: date
    name: str
    #: The level shown on the map: the highest of the three below.
    level: int
    hydrogeological: int
    hydraulic: int
    thunderstorms: int
    municipalities: tuple[str, ...]
    geometry: Any  # shapely MultiPolygon, lon/lat

    @property
    def colour(self) -> str:
        return COLOURS[self.level]


def zones_from_geojson(data: dict[str, Any], valid: date) -> list[Zone]:
    """The zones of one bulletin file (today or tomorrow). Features with no name are skipped."""
    geometry = _shapely()
    out: list[Zone] = []
    for feature in data.get("features") or []:
        p = feature.get("properties") or {}
        shape = geometry.shape(feature["geometry"])
        if isinstance(shape, geometry.Polygon):
            shape = geometry.MultiPolygon([shape])
        if not isinstance(shape, geometry.MultiPolygon) or not p.get("Nome zona"):
            continue
        out.append(
            Zone(
                valid=valid,
                name=str(p["Nome zona"]),
                level=level(p.get("Rappresentata nella mappa")),
                hydrogeological=level(p.get("Per rischio idrogeologico")),
                hydraulic=level(p.get("Per rischio idraulico")),
                thunderstorms=level(p.get("Per rischio temporali")),
                municipalities=tuple(str(m) for m in p.get("Comuni") or ()),
                geometry=shape,
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class Bulletin:
    """One bulletin: its name, when it was issued, and its zones for today and tomorrow."""

    name: str
    issued: datetime
    zones: list[Zone]

    def days(self) -> list[date]:
        return sorted({z.valid for z in self.zones})

    def zone_at(self, lon: float, lat: float, day: date | None = None) -> Zone | None:
        """The zone that contains a point on ``day`` (the first day if omitted)."""
        point = _shapely().Point(lon, lat)
        day = day or self.issued.date()
        for zone in self.zones:
            if zone.valid == day and zone.geometry.covers(point):
                return zone
        return None

    def alerts(self, day: date | None = None, *, at_least: int = 1) -> list[Zone]:
        """The zones at ``at_least`` (yellow by default) on ``day``, highest first."""
        day = day or self.issued.date()
        found = [z for z in self.zones if z.valid == day and z.level >= at_least]
        return sorted(found, key=lambda z: (-z.level, z.name))


def latest_name_from(listing: dict[str, Any]) -> str | None:
    """The newest bulletin name in a GitHub tree listing of ``files/geojson``."""
    names = sorted(t["path"] for t in listing.get("tree", []) if _NAME.match(t["path"]))
    return names[-1] if names else None


async def _json(client: httpx.AsyncClient, url: str) -> Any:
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


async def latest_name(client: httpx.AsyncClient | None = None) -> str | None:
    """The file name of the newest bulletin (``…_today.json``), or ``None``.

    Three small calls to the repository tree instead of one recursive call: the
    archive holds thousands of files. Unauthenticated, GitHub allows 60 calls an
    hour; pass a client with a token header for more.
    """
    own = client is None
    http = client or httpx.AsyncClient(follow_redirects=True, timeout=30)
    try:
        root = await _json(http, f"{API}/git/trees/master")
        files = next(t["sha"] for t in root["tree"] if t["path"] == "files")
        sub = await _json(http, f"{API}/git/trees/{files}")
        geojson = next(t["sha"] for t in sub["tree"] if t["path"] == "geojson")
        return latest_name_from(await _json(http, f"{API}/git/trees/{geojson}"))
    except (KeyError, StopIteration) as exc:
        raise DpcRadarError(f"unexpected layout of {REPO}: {exc!r}") from exc
    finally:
        if own:
            await http.aclose()


async def fetch(name: str, client: httpx.AsyncClient | None = None) -> Bulletin:
    """Today and tomorrow of the bulletin ``name`` (``YYYYMMDD_hhmm_today.json``)."""
    when = issued(name)
    if when is None:
        raise DpcRadarError(f"not a bulletin file name: {name!r}")
    stem = name.removesuffix("_today.json")
    own = client is None
    http = client or httpx.AsyncClient(follow_redirects=True, timeout=30)
    try:
        today = await _json(http, f"{RAW}/{stem}_today.json")
        tomorrow = await _json(http, f"{RAW}/{stem}_tomorrow.json")
    finally:
        if own:
            await http.aclose()
    day = when.date()
    zones = zones_from_geojson(today, day) + zones_from_geojson(tomorrow, day + timedelta(days=1))
    return Bulletin(name=stem, issued=when, zones=zones)


async def latest(client: httpx.AsyncClient | None = None) -> Bulletin:
    """The newest bulletin."""
    name = await latest_name(client)
    if name is None:
        raise DpcRadarError(f"no bulletin found in {REPO}")
    return await fetch(name, client)


__all__ = [
    "COLOURS",
    "REPO",
    "Bulletin",
    "Zone",
    "fetch",
    "issued",
    "latest",
    "latest_name",
    "latest_name_from",
    "level",
    "zones_from_geojson",
]
