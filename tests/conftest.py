"""Synthetic DPC frames, written as real GeoTIFFs in memory."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from affine import Affine
from rasterio.io import MemoryFile

from dpc_radar import RainGrid

#: The CUM grid: 0.01° in EPSG:4326. A 20x20 piece whose top-left is 15°E 38°N.
LONLAT = Affine(0.01, 0, 15.0, 0, -0.01, 38.0)
#: The SRI/SRT1 grid: 1 km, Transverse Mercator centred on 42°N 12.5°E, as the DPC.
TMERC = "+proj=tmerc +lat_0=42 +lon_0=12.5 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m"
METRES = Affine(1000, 0, -10_000, 0, -1000, 10_000)
T0 = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


def tiff(data: np.ndarray, transform: Affine = LONLAT, crs: str = "EPSG:4326") -> bytes:
    """``data`` (with -9999 for no data) as the bytes of a float32 GeoTIFF."""
    profile = {
        "driver": "GTiff",
        "width": data.shape[1],
        "height": data.shape[0],
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
        "nodata": -9999.0,
    }
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(data.astype("float32"), 1)
        return bytes(mem.read())


def grid(data: np.ndarray, name: str = "CUM24", time: datetime = T0) -> RainGrid:
    return RainGrid.from_geotiff(tiff(data), name, time)


@pytest.fixture
def rain() -> np.ndarray:
    """20x20 of 10 mm, with one wrong pixel (7,348 mm) and a no-data corner."""
    data = np.full((20, 20), 10.0)
    data[5, 5] = 7348.0
    data[0:3, 17:20] = -9999.0
    return data
