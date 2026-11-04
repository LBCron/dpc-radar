from __future__ import annotations

import math

import numpy as np
import pytest

from dpc_radar import CUM24, SRI, DpcRadarError, RainGrid
from tests.conftest import METRES, T0, TMERC, grid, tiff


def test_from_geotiff_reads_values_and_no_data(rain: np.ndarray) -> None:
    g = grid(rain)
    assert g.product is CUM24
    assert g.time == T0
    assert g.data.shape == (20, 20)
    assert g.data[10, 10] == 10.0
    assert math.isnan(g.data[0, 19])


def test_from_geotiff_refuses_what_is_not_a_tiff() -> None:
    with pytest.raises(DpcRadarError, match="GeoTIFF"):
        RainGrid.from_geotiff(b"<html>error</html>", "CUM24", T0)


def test_sample_takes_the_median_of_3x3(rain: np.ndarray) -> None:
    g = grid(rain)
    # Pixel (5, 5) is centred at 15.055°E 37.945°N: the 7,348 mm pixel alone.
    assert g.sample([(15.055, 37.945)]) == [10.0]
    assert g.sample([]) == []


def test_sample_outside_or_without_data_is_none(rain: np.ndarray) -> None:
    g = grid(rain)
    outside = (14.0, 37.0)
    corner = (15.195, 37.995)  # top-right, inside the -9999 block
    assert g.sample([outside, corner]) == [None, None]


def test_sample_converts_lon_lat_to_the_grid_projection() -> None:
    data = np.zeros((20, 20))
    data[9:12, 9:12] = 42.0  # around the centre of the projection, 42°N 12.5°E
    g = RainGrid.from_geotiff(tiff(data, METRES, TMERC), "SRI", T0)
    assert g.product is SRI
    assert g.sample([(12.5, 42.0)]) == [42.0]


def test_peak_in_a_box(rain: np.ndarray) -> None:
    g = grid(rain)
    assert g.peak((15.0, 37.8, 15.2, 38.0), threshold=10) == (
        7348.0,
        391,
    )  # 400 pixels, 9 without data
    assert g.peak((15.0, 37.8, 15.2, 38.0), threshold=100) == (7348.0, 1)


def test_peak_outside_or_without_data_is_zero(rain: np.ndarray) -> None:
    g = grid(rain)
    assert g.peak((10.0, 30.0, 11.0, 31.0), threshold=1) == (0.0, 0)
    assert g.peak((15.175, 37.975, 15.195, 37.995), threshold=1) == (0.0, 0)


def test_crop_keeps_the_georeference(rain: np.ndarray) -> None:
    g = grid(rain)
    c = g.crop((15.055, 37.905, 15.095, 37.945))  # edges on pixel centres
    assert c.data.shape == (5, 5)
    assert c.sample([(15.075, 37.925)]) == g.sample([(15.075, 37.925)])
    assert c.time == g.time and c.product == g.product


def test_crop_outside_the_frame_fails(rain: np.ndarray) -> None:
    with pytest.raises(DpcRadarError, match="outside"):
        grid(rain).crop((10.0, 30.0, 11.0, 31.0))


# --- check: which frame to trust ----------------------------------------------------


def _pair(change: float, pixels: int) -> tuple[RainGrid, RainGrid]:
    before = np.full((20, 20), 20.0)
    now = before.copy()
    now.flat[:pixels] += change
    return grid(now), grid(before)


def test_check_consistent_frames() -> None:
    now, before = _pair(change=30, pixels=50)  # 30 mm in half an hour: possible
    result = now.check(before, max_step=150)
    assert result.verdict == "consistent"
    assert result.grid is now and result.usable and result.jumps == 0


def test_check_masks_a_few_pixels() -> None:
    now, before = _pair(change=900, pixels=3)
    result = now.check(before, max_step=150)
    assert result.verdict == "pixels_masked" and result.jumps == 3
    assert result.grid is not None
    assert int(np.isnan(result.grid.data).sum()) == 3


def test_check_rejects_the_frame_that_jumps_up() -> None:
    now, before = _pair(change=5000, pixels=40)  # the corrupted :30 frame
    result = now.check(before, max_step=150)
    assert result.verdict == "rejected" and result.jumps == 40
    assert result.grid is None and not result.usable


def test_check_keeps_the_frame_after_a_corrupted_one() -> None:
    corrupted, clean = _pair(change=5000, pixels=40)
    result = clean.check(corrupted, max_step=150)  # clean comes after corrupted
    assert result.verdict == "previous_wrong" and result.jumps == 40
    assert result.grid is clean


def test_check_different_grids_are_not_comparable(rain: np.ndarray) -> None:
    g = grid(rain)
    smaller = g.crop((15.055, 37.905, 15.095, 37.945))
    result = g.check(smaller, max_step=150)
    assert result.verdict == "not_comparable" and result.grid is g


def test_discard_inconsistent_masks_both_ways() -> None:
    corrupted, clean = _pair(change=5000, pixels=40)
    out, n = clean.discard_inconsistent(corrupted, max_step=150)
    assert n == 40 and int(np.isnan(out.data).sum()) == 40  # good pixels lost
    same, zero = clean.discard_inconsistent(clean, max_step=150)
    assert zero == 0 and same is clean
    other, none = clean.discard_inconsistent(clean.crop((15.0, 37.9, 15.05, 38.0)), max_step=1)
    assert none == 0 and other is clean
