from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from numcodecs import Blosc
from rasterio.crs import CRS

from dpc_radar import DpcRadarError
from dpc_radar.history import EPOCH, SriHistory
from tests.conftest import TMERC

DATA = Path(__file__).parent / "data"
SHAPE = (1400, 1200)


def store(folder: Path, minutes: list[int], frames: dict[int, np.ndarray]) -> Path:
    """A minimal IT-DPC-SRI Zarr v2 store: the files SriHistory reads."""
    codec = Blosc(cname="zstd")
    root = folder / "italian-radar-dpc-sri.zarr"
    for name in ("RR", "time", "crs"):
        (root / name).mkdir(parents=True)
    (root / "RR" / ".zarray").write_text(json.dumps({"shape": [len(minutes), *SHAPE]}))
    (root / "crs" / ".zattrs").write_text(
        json.dumps(
            {
                "GeoTransform": "-600000.0 1000.0 0.0 650000.0 0.0 -1000.0",
                "crs_wkt": CRS.from_proj4(TMERC).to_wkt(),
            }
        )
    )
    (root / "time" / "0").write_bytes(codec.encode(np.array(minutes, dtype="<i8").tobytes()))
    for i, frame in frames.items():
        (root / "RR" / f"{i}.0.0").write_bytes(codec.encode(frame.astype("<f4").tobytes()))
    return root


def _frame(mmh: float) -> np.ndarray:
    data = np.full(SHAPE, mmh, dtype="f4")
    data[:, :100] = np.nan  # no coverage on the western edge
    return data


MELITO = (15.78, 37.92)
WEST = (4.6, 45.0)  # inside the grid, in the no-coverage strip
FAR = (0.0, 60.0)  # outside the grid


def test_hourly_weights_each_frame_by_the_time_it_covers(tmp_path: Path) -> None:
    hour = 8 * 60  # 2010-01-01 08:00
    # 07:00, 07:15, 07:30, 07:45 at 4, 8, 8, 4 mm/h: one hour, 6 mm
    minutes = [hour - 60, hour - 45, hour - 30, hour - 15, hour]
    frames = {0: _frame(4), 1: _frame(8), 2: _frame(8), 3: _frame(4), 4: _frame(0)}
    sri = SriHistory(store(tmp_path, minutes, frames))
    assert len(sri) == 5 and sri.first == EPOCH + timedelta(hours=7)
    assert sri.last == EPOCH + timedelta(hours=8) and sri.time(1).minute == 15
    rain = sri.hourly([MELITO, WEST, FAR], EPOCH + timedelta(hours=7), EPOCH + timedelta(hours=8))
    assert rain == {EPOCH + timedelta(hours=8): [6.0, None, None]}


def test_an_hour_covered_less_than_half_is_not_measured(tmp_path: Path) -> None:
    hour = 8 * 60
    minutes = [hour - 60, hour - 45, hour - 30, hour - 15]
    frames = {0: _frame(4), 3: _frame(4)}  # two of four chunks missing: 30 min... minus one
    sri = SriHistory(store(tmp_path, minutes, {0: frames[0]}))
    rain = sri.hourly([MELITO], EPOCH + timedelta(hours=7), EPOCH + timedelta(hours=8))
    assert rain == {EPOCH + timedelta(hours=8): [None]}  # 15 minutes covered
    sri = SriHistory(store(tmp_path / "b", minutes, {0: _frame(4), 1: _frame(4)}))
    rain = sri.hourly([MELITO], EPOCH + timedelta(hours=7), EPOCH + timedelta(hours=8))
    assert rain == {EPOCH + timedelta(hours=8): [4.0]}  # 30 minutes: enough


def test_a_gap_counts_15_minutes_at_most(tmp_path: Path) -> None:
    sri = SriHistory(store(tmp_path, [7 * 60, 9 * 60], {0: _frame(10), 1: _frame(10)}))
    assert sri.frame(5) is None
    rain = sri.hourly([MELITO], EPOCH + timedelta(hours=7), EPOCH + timedelta(hours=10))
    # 07:00 covers 15 minutes only: not measured. The last frame covers 5 minutes.
    assert rain == {EPOCH + timedelta(hours=8): [None], EPOCH + timedelta(hours=10): [None]}


def test_a_real_chunk_decodes(tmp_path: Path) -> None:
    root = store(tmp_path, [0], {})
    shutil.copy(DATA / "it-dpc-sri-RR-166809.0.0", root / "RR" / "0.0.0")
    frame = SriHistory(root).frame(0)
    assert frame is not None and frame.shape == SHAPE
    assert int(np.isfinite(frame).sum()) == 809_736
    assert float(np.nanmax(frame)) == pytest.approx(10.109375)


def test_not_a_store(tmp_path: Path) -> None:
    with pytest.raises(DpcRadarError, match="IT-DPC-SRI"):
        SriHistory(tmp_path)
