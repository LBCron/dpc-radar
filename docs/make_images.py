"""Draw the README images in docs/images from real DPC radar frames.

    uv run --with httpx --with rasterio --with matplotlib \
        python docs/make_images.py [DATA_DIR] [YYYY-MM-DD]

The day shown is 10 October 2026, when a storm hit the Reggio Calabria area
(176 mm at Melito di Porto Salvo, an event Limen was checked against). The
script asks the open DPC radar API for:

* one «Merging» CUM24 frame a day (00:00 UTC) for the last 150 days, for the
  season chart (the average over Italy's land only: radar clutter over the sea
  must not make a dry day look wet);
* the 24 hourly SRT1 frames of the day, for the radar-only map and the hourly
  chart of one cell (sampled with the median of the 3x3 pixels around it);
* four SRI frames of the day, for the storm strip.

Frames are kept in DATA_DIR (default ``.cache/dpc-radar``), so a second run
reads them from disk. A frame older than about five months is gone from the
API: run it again with a later day. The numbers quoted in the README are
printed at the end.

Country borders: Natural Earth 1:50m (public domain), downloaded once.
Radar data: © Dipartimento della Protezione Civile, CC BY-SA 4.0.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import BoundaryNorm, ListedColormap
from rasterio.features import rasterize
from rasterio.warp import Resampling, reproject
from rasterio.warp import transform as warp_points

from dpc_radar import CUM24, SRI, SRT1, Product, is_expired, parse_download, products

DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".cache/dpc-radar")
DAY = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 10, 10)
OUT = Path(__file__).parent / "images"
OUT.mkdir(parents=True, exist_ok=True)
SEASON_DAYS = 150
BORDERS_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_50m_admin_0_countries.geojson"
)

#: Where the story happens: Melito di Porto Salvo, and the box of the zoom.
PLACE = ("Melito di Porto Salvo", 15.78, 37.92)
ZOOM = (15.25, 17.25, 37.75, 39.45)
ITALY = (6.0, 19.0, 36.3, 47.3)

# Palette: the sequential blue ramp (steps 100 → 700) for rain, light chrome.
BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SURFACE, LAND, BORDER = "#fcfcfb", "#ecebe6", "#a9a8a2"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#7a7974"
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"
CREDIT = "Data © Dipartimento della Protezione Civile, CC BY-SA 4.0 · borders: Natural Earth"
RAIN_MM = [1, 5, 10, 25, 50, 100, 150]
RAIN_MMH = [0.5, 1, 2, 5, 10, 20, 50]
plt.rcParams.update({"font.family": "sans-serif", "font.size": 11, "text.color": INK})


# --- downloads -------------------------------------------------------------------


async def frame(
    client: httpx.AsyncClient, limit: asyncio.Semaphore, p: Product, at: datetime
) -> Path | None:
    """The GeoTIFF of one frame, from disk or from the API; None if the DPC lost it."""
    path = DATA / p.name / f"{p.name}_{at:%Y%m%d%H%M}.tif"
    absent = path.with_suffix(".absent")
    if path.exists():
        return path
    if absent.exists():
        return None
    async with limit:
        for attempt in range(4):  # the API sometimes drops a connection
            try:
                r = await client.post(
                    products.download_url(),
                    json=products.download_body(p, at),
                    headers=products.HEADERS,
                )
                if r.status_code != 200:
                    if is_expired(r.status_code, r.text):
                        absent.parent.mkdir(parents=True, exist_ok=True)
                        absent.touch()
                        return None
                    r.raise_for_status()
                tif = await client.get(parse_download(r.json()))
                tif.raise_for_status()
                break
            except httpx.TransportError:
                if attempt == 3:
                    raise
                await asyncio.sleep(2 * (attempt + 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tif.content)
    return path


async def frames(wanted: list[tuple[Product, datetime]]) -> list[Path | None]:
    limit = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=60) as client:
        return list(await asyncio.gather(*(frame(client, limit, p, t) for p, t in wanted)))


def read(path: Path) -> tuple[np.ndarray, Any, Any]:
    with rasterio.open(path) as src:
        a = src.read(1).astype("float64")
        a[a < 0] = np.nan  # -9999: no data
        return a, src.transform, src.crs


def borders() -> tuple[list[np.ndarray], dict[str, Any]]:
    """Country rings around Italy, and Italy's own geometry."""
    path = DATA / "ne_50m_admin_0_countries.geojson"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(httpx.get(BORDERS_URL, timeout=120, follow_redirects=True).content)
    rings: list[np.ndarray] = []
    italy: dict[str, Any] = {}
    for feature in json.loads(path.read_text())["features"]:
        geom = feature["geometry"]
        if feature["properties"]["ADMIN"] == "Italy":
            italy = geom
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            ring = np.asarray(poly[0])
            if ring[:, 0].max() > 3 and ring[:, 0].min() < 22 and ring[:, 1].max() > 34:
                rings.append(ring)
    return rings, italy


def to_lonlat(a: np.ndarray, transform: Any, crs: Any, dst_transform: Any, shape) -> np.ndarray:
    dst = np.full(shape, np.nan)
    reproject(
        a,
        dst,
        src_transform=transform,
        src_crs=crs,
        dst_transform=dst_transform,
        dst_crs="EPSG:4326",
        resampling=Resampling.bilinear,
        src_nodata=np.nan,
        dst_nodata=np.nan,
    )
    return dst


def sample(a: np.ndarray, transform: Any, crs: Any, lon: float, lat: float) -> float:
    """The median of the 3x3 pixels around a point: one hot pixel is not rain."""
    xs, ys = warp_points("EPSG:4326", crs, [lon], [lat])
    col, row = ~transform * (xs[0], ys[0])
    r, c = int(row), int(col)
    return float(np.nanmedian(a[r - 1 : r + 2, c - 1 : c + 2]))


# --- maps --------------------------------------------------------------------------


def map_figure(ncols: int, panel_w: float, extents: list[tuple[float, ...]], top: float = 1.0):
    """A figure with ncols map panels sized to their extent, room above and below."""
    heights = [
        (e[3] - e[2]) / math.cos(math.radians((e[2] + e[3]) / 2)) / (e[1] - e[0]) * panel_w
        for e in extents
    ]
    ph = max(heights)
    gap, side, bottom = 0.35, 0.15, 0.95
    fw = 2 * side + ncols * panel_w + (ncols - 1) * gap
    fh = top + ph + bottom
    fig = plt.figure(figsize=(fw, fh), facecolor=SURFACE)
    axes = []
    for i, h in enumerate(heights):
        x = side + i * (panel_w + gap)
        axes.append(fig.add_axes((x / fw, (bottom + ph - h) / fh, panel_w / fw, h / fh)))
    return fig, axes


def base_axes(ax: plt.Axes, rings: list[np.ndarray], extent: tuple[float, ...]) -> None:
    ax.set_facecolor(SURFACE)
    for ring in rings:
        ax.fill(ring[:, 0], ring[:, 1], color=LAND, lw=0, zorder=0)
        ax.plot(ring[:, 0], ring[:, 1], color=BORDER, lw=0.5, zorder=3)
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_aspect(1 / math.cos(math.radians((extent[2] + extent[3]) / 2)))
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)


def rain_layer(ax: plt.Axes, a: np.ndarray, extent: tuple[float, ...], bounds: list[float]):
    cmap = ListedColormap(BLUE)
    cmap.set_under((0, 0, 0, 0))
    cmap.set_bad((0, 0, 0, 0))
    norm = BoundaryNorm(bounds, cmap.N, extend="max")
    im = ax.imshow(a, extent=extent, cmap=cmap, norm=norm, interpolation="nearest", zorder=2)
    keep_aspect(ax)
    return im


def keep_aspect(ax: plt.Axes) -> None:
    """imshow resets the aspect to 1: a degree of longitude is shorter than one of latitude."""
    s, n = ax.get_ylim()
    ax.set_aspect(1 / math.cos(math.radians((s + n) / 2)))


def colorbar(fig, im, label: str, bounds: list[float]) -> None:
    fw, fh = fig.get_size_inches()
    cax = fig.add_axes(((fw - 5) / 2 / fw, 0.5 / fh, 5 / fw, 0.14 / fh))
    cb = fig.colorbar(im, cax=cax, orientation="horizontal")
    cb.set_label(label, color=INK2)
    cb.set_ticks(bounds)
    cb.set_ticklabels([f"{b:g}" for b in bounds])
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=INK2, length=0)


def titles(fig, title: str, subtitle: str) -> None:
    fh = fig.get_size_inches()[1]
    fig.text(
        0.15 / fig.get_size_inches()[0],
        1 - 0.38 / fh,
        title,
        fontsize=16,
        fontweight="bold",
        color=INK,
    )
    fig.text(0.15 / fig.get_size_inches()[0], 1 - 0.66 / fh, subtitle, fontsize=11, color=INK2)
    fig.text(
        1 - 0.15 / fig.get_size_inches()[0],
        0.06 / fh,
        CREDIT,
        fontsize=7.5,
        color=MUTED,
        ha="right",
    )


def mark(ax: plt.Axes, label: str, lon: float, lat: float, right: bool = True) -> None:
    ax.plot(lon, lat, "o", ms=8, mfc="none", mec=INK, mew=1.5, zorder=5)
    ax.annotate(
        label,
        (lon, lat),
        xytext=(10 if right else -10, -14),
        textcoords="offset points",
        ha="left" if right else "right",
        fontsize=10,
        color=INK,
        zorder=6,
    )


def zoom_box(ax: plt.Axes) -> None:
    w, e, s, n = ZOOM
    ax.plot([w, e, e, w, w], [s, s, n, n, s], color=INK, lw=1, zorder=4)


def map_rain(cum, extent, rings, out: Path) -> None:
    fig, (a1, a2) = map_figure(2, 5.0, [ITALY, ZOOM])
    for ax, ext in ((a1, ITALY), (a2, ZOOM)):
        base_axes(ax, rings, ext)
        im = rain_layer(ax, cum, extent, RAIN_MM)
    zoom_box(a1)
    name, lon, lat = PLACE
    mark(a2, name, lon, lat)
    titles(
        fig,
        f"Rain over Italy on {DAY:%-d %B %Y}",
        "DPC «Merging» CUM24: the 24 hours of rain, radar + rain gauges, 0.01° grid. "
        "Right: the Reggio Calabria area.",
    )
    colorbar(fig, im, "rain in the 24 hours (mm)", RAIN_MM)
    fig.savefig(out, dpi=110, facecolor=SURFACE)
    plt.close(fig)


def map_compare(radar, cum, extent, rings, out: Path) -> None:
    fig, axes = map_figure(2, 5.0, [ZOOM, ZOOM])
    for ax, a, label in zip(
        axes, (radar, cum), ("Radar only: 24 × SRT1", "«Merging»: CUM24"), strict=True
    ):
        base_axes(ax, rings, ZOOM)
        im = rain_layer(ax, a, extent, RAIN_MM)
        ax.text(
            0.02,
            0.97,
            label,
            transform=ax.transAxes,
            fontsize=12.5,
            fontweight="bold",
            va="top",
            color=INK,
            zorder=7,
            bbox={"fc": SURFACE, "ec": "none", "alpha": 0.85, "pad": 3},
        )
        mark(ax, PLACE[0], PLACE[1], PLACE[2])
    titles(
        fig,
        f"The same storm, two products · {DAY:%-d %B %Y}",
        "Left: the radar alone, hour by hour. Right: the radar corrected with the rain gauges.",
    )
    colorbar(fig, im, "rain in the 24 hours (mm)", RAIN_MM)
    fig.savefig(out, dpi=110, facecolor=SURFACE)
    plt.close(fig)


def map_storm(storm: dict[datetime, np.ndarray], extent, rings, out: Path) -> None:
    fig, axes = map_figure(len(storm), 3.4, [ITALY] * len(storm), top=0.85)
    for ax, (t, a) in zip(axes, storm.items(), strict=True):
        base_axes(ax, rings, ITALY)
        im = rain_layer(ax, a, extent, RAIN_MMH)
        ax.text(
            0.03,
            0.97,
            f"{t:%H:%M} UTC",
            transform=ax.transAxes,
            fontsize=12,
            fontweight="bold",
            va="top",
            color=INK,
            zorder=7,
            bbox={"fc": SURFACE, "ec": "none", "alpha": 0.85, "pad": 3},
        )
    titles(
        fig,
        f"Rain intensity, every few hours · {DAY:%-d %B %Y}",
        "DPC SRI: what the radar sees right now, in mm/h, a new frame every 5 minutes.",
    )
    colorbar(fig, im, "rain intensity (mm/h)", RAIN_MMH)
    fig.savefig(out, dpi=100, facecolor=SURFACE)
    plt.close(fig)


def banner(cum, extent, rings, out: Path) -> None:
    """A wide strip: the day's rain on a dark map, with the title."""
    bg, text, sub = "#0f1720", "#f0efec", "#c3c2b7"
    fig = plt.figure(figsize=(12, 3.4), facecolor=bg)
    ax = fig.add_axes((0.55, 0.0, 0.45, 1.0))
    ax.set_facecolor(bg)
    for ring in rings:
        ax.plot(ring[:, 0], ring[:, 1], color="#3d536a", lw=0.6, zorder=3)
    cmap = ListedColormap(
        ["#104281", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"]
    )
    cmap.set_under((0, 0, 0, 0))
    cmap.set_bad((0, 0, 0, 0))
    norm = BoundaryNorm(RAIN_MM, cmap.N, extend="max")
    ax.imshow(cum, extent=extent, cmap=cmap, norm=norm, interpolation="nearest", zorder=2)
    ax.set_xlim(7.0, 19.5)
    ax.set_ylim(36.4, 46.0)
    keep_aspect(ax)
    ax.axis("off")
    fig.text(0.04, 0.62, "dpc-radar", color=text, fontsize=40, fontweight="bold")
    fig.text(0.04, 0.44, "Rain over Italy from the Civil Protection radar,", color=sub, fontsize=15)
    fig.text(0.04, 0.33, "in a few lines of Python.", color=sub, fontsize=15)
    fig.text(
        0.04,
        0.12,
        "SRI · SRT1 · CUM3–24 «Merging»  ·  open data, CC BY-SA 4.0",
        color="#86b6ef",
        fontsize=11,
    )
    fig.savefig(out, dpi=110, facecolor=bg)
    plt.close(fig)


# --- SVG charts (light and dark) -----------------------------------------------------

SVG_STYLE = f"""<style>
text{{font-family:{FONT};fill:#0b0b0b}}
.bg{{fill:#fcfcfb}} .t2{{fill:#52514e}} .mu{{fill:#7a7974}}
.grid{{stroke:#e6e5e0;stroke-width:1}} .axis{{stroke:#7a7974;stroke-width:1}}
.radar{{fill:#2a78d6}} .merge{{fill:#eb6834}} .hl{{fill:#0d366b}} .bar{{fill:#6da7ec}}
@media (prefers-color-scheme: dark){{
text{{fill:#f0efec}} .bg{{fill:#1a1a19}} .t2{{fill:#c3c2b7}} .mu{{fill:#9a9993}}
.grid{{stroke:#2e2e2c}} .axis{{stroke:#7a7974}}
.radar{{fill:#3987e5}} .merge{{fill:#d95926}} .hl{{fill:#cde2fb}} .bar{{fill:#3987e5}}
}}
</style>"""


def svg_open(w: int, h: int, label: str, title: str, subtitle: str, left: int) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" '
        f'height="{h}" role="img" aria-label="{label}">',
        SVG_STYLE,
        f'<rect class="bg" width="{w}" height="{h}" rx="8"/>',
        f'<text x="{left}" y="30" font-size="18" font-weight="700">{title}</text>',
        f'<text class="t2" x="{left}" y="52" font-size="13">{subtitle}</text>',
    ]


def y_grid(parts, left, right_x, top, ph, ymax, step, unit=""):
    for v in range(0, ymax + 1, step):
        y = top + ph - v / ymax * ph
        parts.append(f'<line class="grid" x1="{left}" x2="{right_x}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(
            f'<text class="mu" x="{left - 8}" y="{y + 4:.1f}" font-size="11" '
            f'text-anchor="end">{v}{unit}</text>'
        )


def nice_max(v: float) -> tuple[int, int]:
    step = next(s for s in (1, 2, 5, 10, 20, 25, 50, 100) if v / s <= 6)
    return max(math.ceil(v / step), 1) * step, step


def season_svg(daily: list[tuple[date, float]], out: Path) -> None:
    w, h, left, right, top, bottom = 960, 340, 56, 20, 72, 40
    pw, ph = w - left - right, h - top - bottom
    ymax, step = nice_max(max(v for _, v in daily))
    n = len(daily)
    pitch = pw / n
    bw = max(pitch - 2, 1)
    parts = svg_open(
        w,
        h,
        f"Average daily rain over Italy's land, last {n} days",
        f"{n} days of rain over Italy, one bar a day",
        "Average of the CUM24 «Merging» frame over Italy's land (mm). "
        f"The highlighted bar is {DAY:%-d %B}.",
        left,
    )
    y_grid(parts, left, w - right, top, ph, ymax, step)
    for i, (d, v) in enumerate(daily):
        x = left + i * pitch + 1
        bh = v / ymax * ph
        cls = "hl" if d == DAY else "bar"
        if bh > 0:
            parts.append(
                f'<rect class="{cls}" x="{x:.1f}" y="{top + ph - bh:.1f}" '
                f'width="{bw:.1f}" height="{bh:.1f}" rx="1">'
                f"<title>{d:%-d %b %Y}: {v:.1f} mm</title></rect>"
            )
        if d.day == 1:
            parts.append(
                f'<text class="mu" x="{x:.1f}" y="{h - bottom + 18}" font-size="11">{d:%b}</text>'
            )
    parts.append(
        f'<line class="axis" x1="{left}" x2="{w - right}" y1="{top + ph}" y2="{top + ph}"/>'
    )
    wettest = max(daily, key=lambda d: d[1])
    for d, v in {wettest, *[x for x in daily if x[0] == DAY]}:
        i = daily.index((d, v))
        x = left + i * pitch + pitch / 2
        y = top + ph - v / ymax * ph - 6
        anchor = "end" if i > n * 0.6 else "start"
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="12" font-weight="700" '
            f'text-anchor="{anchor}">{d:%-d %b}: {v:.1f} mm</text>'
        )
    parts.append("</svg>")
    out.write_text("\n".join(parts) + "\n")


def cell_svg(hourly: list[float | None], cum24: float, out: Path) -> None:
    """The radar's hour-by-hour rain at one cell, and the day's total."""
    w, h, left, right, top, bottom = 960, 340, 56, 20, 72, 44
    pw, ph = w - left - right, h - top - bottom
    vals = [v or 0.0 for v in hourly]
    ymax, step = nice_max(max(vals))
    pitch = pw / 24
    total = sum(vals)
    name = PLACE[0]
    parts = svg_open(
        w,
        h,
        f"Hourly radar rain at {name} on {DAY}",
        f"{name}, {DAY:%-d %B %Y}: the storm, hour by hour",
        f"Radar only (SRT1), 3×3 median around the town, mm per hour. Day total: radar "
        f"{total:.0f} mm, «Merging» {cum24:.0f} mm.",
        left,
    )
    y_grid(parts, left, w - right, top, ph, ymax, step)
    for i, v in enumerate(hourly):
        x = left + i * pitch + 2
        if v is None:
            parts.append(
                f'<text class="mu" x="{x + pitch / 2 - 2:.1f}" y="{top + ph - 4}" '
                'font-size="10" text-anchor="middle">n/a</text>'
            )
            continue
        bh = v / ymax * ph
        if bh > 0:
            parts.append(
                f'<rect class="radar" x="{x:.1f}" y="{top + ph - bh:.1f}" '
                f'width="{pitch - 4:.1f}" height="{bh:.1f}" rx="2">'
                f"<title>{i:02d}:00–{i + 1:02d}:00 UTC: {v:.1f} mm</title></rect>"
            )
        if i % 3 == 0:
            parts.append(
                f'<text class="mu" x="{x:.1f}" y="{h - bottom + 18}" '
                f'font-size="11">{i:02d}:00</text>'
            )
    peak = max(range(24), key=lambda i: vals[i])
    x = left + peak * pitch + pitch / 2
    y = top + ph - vals[peak] / ymax * ph - 6
    parts.append(
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="12" font-weight="700" '
        f'text-anchor="middle">{vals[peak]:.0f} mm</text>'
    )
    parts.append(
        f'<line class="axis" x1="{left}" x2="{w - right}" y1="{top + ph}" y2="{top + ph}"/>'
    )
    parts.append(
        f'<text class="mu" x="{w - right}" y="{h - 8}" font-size="11" '
        'text-anchor="end">hour of the day (UTC) · mm per hour</text>'
    )
    parts.append("</svg>")
    out.write_text("\n".join(parts) + "\n")


def products_svg(out: Path) -> None:
    """The window of rain each product adds up, before the frame time."""
    w, h, left, right, top = 960, 404, 110, 300, 82
    pw = w - left - right
    rows = [
        ("SRI", None, "radar", "intensity right now · mm/h · 1 km · every 5 min"),
        ("SRT1", 1, "radar", "last hour · mm · 1 km · every 5 min"),
        ("CUM3", 3, "merge", "last 3 h · mm · 0.01° · every 30 min"),
        ("CUM6", 6, "merge", "last 6 h · every 30 min"),
        ("CUM12", 12, "merge", "last 12 h · every 30 min"),
        ("CUM24", 24, "merge", "last 24 h · every 30 min"),
    ]
    rh = 40

    def x_of(hours_before: float) -> float:
        return left + pw - hours_before / 24 * pw

    parts = svg_open(
        w,
        h,
        "The time window of each DPC radar product",
        "Six products, one question: how much rain, over which window?",
        "Each frame adds up the rain of a window that ends at the frame time.",
        24,
    )
    parts.append(
        '<rect class="radar" x="24" y="62" width="12" height="12" rx="2"/>'
        '<text class="t2" x="42" y="73" font-size="13">radar only</text>'
        '<rect class="merge" x="130" y="62" width="12" height="12" rx="2"/>'
        '<text class="t2" x="148" y="73" font-size="13">«Merging»: radar + rain gauges</text>'
    )
    top += 14
    bottom_y = top + len(rows) * rh
    for hb in (24, 18, 12, 6, 3, 0):
        x = x_of(hb)
        parts.append(
            f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top - 6}" y2="{bottom_y}"/>'
        )
        label = "frame time" if hb == 0 else f"−{hb} h"
        parts.append(
            f'<text class="mu" x="{x:.1f}" y="{bottom_y + 18}" font-size="11" '
            f'text-anchor="middle">{label}</text>'
        )
    for i, (name, hours, cls, note) in enumerate(rows):
        y = top + i * rh
        parts.append(
            f'<text x="{left - 14}" y="{y + 22}" font-size="14" font-weight="700" '
            f'text-anchor="end" font-family="ui-monospace,Menlo,monospace">{name}</text>'
        )
        if hours is None:
            parts.append(f'<circle class="{cls}" cx="{x_of(0):.1f}" cy="{y + 17}" r="6"/>')
        else:
            x0 = x_of(hours)
            parts.append(
                f'<rect class="{cls}" x="{x0:.1f}" y="{y + 9}" '
                f'width="{x_of(0) - x0:.1f}" height="16" rx="4"/>'
            )
        parts.append(
            f'<text class="t2" x="{x_of(0) + 16:.1f}" y="{y + 22}" font-size="12">{note}</text>'
        )
    parts.append(
        f'<text class="mu" x="24" y="{h - 14}" font-size="12">No CUM1: the API answers '
        "it with an empty list. Frames are kept for about five months, then they are "
        "gone.</text>"
    )
    parts.append("</svg>")
    out.write_text("\n".join(parts) + "\n")


# --- main ------------------------------------------------------------------------------


def midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def main() -> None:
    products_svg(OUT / "products.svg")
    rings, italy = borders()

    # The season: one CUM24 a day, averaged over Italy's land.
    today = midnight(datetime.now(UTC).date())
    ends = [today - timedelta(days=k) for k in range(SEASON_DAYS, 0, -1)]
    season = asyncio.run(frames([(CUM24, t) for t in ends]))
    land = None
    daily: list[tuple[date, float]] = []
    for t, p in zip(ends, season, strict=True):
        if p is None:
            continue
        a, tr, _ = read(p)
        if land is None:
            land = rasterize([(italy, 1)], out_shape=a.shape, transform=tr).astype(bool)
        daily.append(((t - timedelta(days=1)).date(), float(np.nanmean(a[land]))))
    season_svg(daily, OUT / "season.svg")

    # The day.
    start = midnight(DAY)
    hours = [start + timedelta(hours=h) for h in range(1, 25)]
    storm_times = [start + timedelta(hours=h) for h in (6, 10, 14, 18)]
    got = asyncio.run(
        frames(
            [(CUM24, start + timedelta(days=1))]
            + [(SRT1, t) for t in hours]
            + [(SRI, t) for t in storm_times]
        )
    )
    cum_path, srt1_paths, sri_paths = got[0], got[1:25], got[25:]
    if cum_path is None:
        sys.exit(f"{DAY}: the DPC no longer keeps this day; pass a later one")
    cum, transform, cum_crs = read(cum_path)
    extent = (
        transform.c,
        transform.c + cum.shape[1] * transform.a,
        transform.f + cum.shape[0] * transform.e,
        transform.f,
    )

    map_rain(cum, extent, rings, OUT / "rain-24h.png")
    banner(cum, extent, rings, OUT / "banner.png")

    radar = np.zeros(cum.shape)
    hourly: list[float | None] = []
    for p in srt1_paths:
        if p is None:
            hourly.append(None)
            continue
        a, tr, crs = read(p)
        hourly.append(sample(a, tr, crs, PLACE[1], PLACE[2]))
        radar = radar + np.nan_to_num(to_lonlat(a, tr, crs, transform, cum.shape))
    radar[~np.isfinite(cum)] = np.nan
    map_compare(radar, cum, extent, rings, OUT / "radar-vs-merging.png")
    cum_cell = sample(cum, transform, cum_crs, PLACE[1], PLACE[2])
    cell_svg(hourly, cum_cell, OUT / "cell.svg")

    storm = {}
    for t, p in zip(storm_times, sri_paths, strict=True):
        if p is not None:
            a, tr, crs = read(p)
            storm[t] = to_lonlat(a, tr, crs, transform, cum.shape)
    map_storm(storm, extent, rings, OUT / "storm.png")

    w, e, s, n = ZOOM
    rows = slice(int((transform.f - n) / -transform.e), int((transform.f - s) / -transform.e))
    cols = slice(int((w - transform.c) / transform.a), int((e - transform.c) / transform.a))
    box_c, box_r = cum[rows, cols], radar[rows, cols]
    ok = np.isfinite(box_c) & np.isfinite(box_r) & (box_c >= 1)
    print(
        json.dumps(
            {
                "day": str(DAY),
                "season": [str(daily[0][0]), str(daily[-1][0]), len(daily)],
                "wettest_land_day": max(daily, key=lambda d: d[1]),
                "day_land_mean_mm": next((v for d, v in daily if d == DAY), None),
                "zoom_peak_cum24_mm": round(float(np.nanmax(box_c)), 1),
                "zoom_peak_radar_mm": round(float(np.nanmax(box_r)), 1),
                "zoom_radar_share": round(float(box_r[ok].sum() / box_c[ok].sum()), 3),
                "cell_cum24_mm": round(cum_cell, 1),
                "cell_radar_mm": round(sum(v or 0 for v in hourly), 1),
                "cell_peak_hour_mm": round(max(v or 0 for v in hourly), 1),
                "srt1_frames": sum(p is not None for p in srt1_paths),
                "sri_frames": len(storm),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
