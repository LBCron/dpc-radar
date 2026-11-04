# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `archive`: `archive()` downloads the CUM3 and SRT1 frames an archive on disk
  lacks (oldest first, frames that are gone written to `absent.txt`, idempotent;
  reads Limen's layout and `assenti.txt`); `MergingArchive.hourly` gives the
  rain of every hour at lon/lat points, SRT1 rescaled so that each block adds
  up to its CUM3; `split_block`.
- `history` (extra `history`, `numcodecs`): `SriHistory` reads the IT-DPC-SRI
  Zarr store (2010-2025) with no zarr or xarray; `hourly` with the same
  signature as `MergingArchive.hourly`.

### Changed

- `RainGrid.data` is float32, as published: half the memory, and exactly the
  values Limen reads.

### Fixed

- Compared with Limen's reader of IT-DPC-SRI, the first hour of a window now
  counts the frame issued at its start, and no hour after the window is
  returned.

## [0.1.0] - 2026-11-04

### Added

- `products`: the DPC radar products (SRI, SRT1, CUM3/6/12/24), the requests of
  `findLastProductByType` and `downloadProduct`, the parsers of their answers,
  and `is_expired` for a frame the DPC no longer keeps.
- `DpcRadarError`.
- `grid`: `RainGrid`, one frame as an array (NaN where there is no data), from
  its GeoTIFF; `sample` (median of the 3×3 pixels), `peak`, `crop`, and
  `check(previous, max_step=)`, the frame-level `jump-sign` rule: the frame
  that jumps up is rejected, the one after a corrupted frame kept whole.
  `discard_inconsistent` kept for comparison with Limen.
- `client`: `RadarClient` with an injectable `httpx.AsyncClient`, retries
  with backoff on network errors and 429/5xx, a `send=` hook; `latest`
  (with `cached=`), `at`, `download`, `last_product`; `ABSENT` for a frame the
  DPC no longer keeps, never retried.
- `Product.period`: 5 minutes for SRI and SRT1, 30 for the CUM products.
- Core dependencies: `httpx`, `numpy`, `rasterio`.
- `benchmarks/`: known cases of the DPC radar data (Melito and Lipari on
  10 October 2026, the corrupted :30 CUM24 series, calm days, two suspect
  artefacts), a runner that scores cleaning methods on them, and Limen's
  current rule as a baseline (#2). Checked in CI.
- `benchmarks`: `jump-sign`, a frame-level check (#6): the frame that jumps up
  more than 150 mm in 30 minutes is rejected whole, the one after it kept.
  17/18 confirmed checks (Limen today 8/18); on 96 whole-frame pairs it
  rejects the 44 corrupted :30 frames and no :00 frame.
  `benchmarks.season` runs a method on every pair of whole frames.

### Documentation

- README rewritten for a wider audience: what the DPC radar is, the products,
  a quick start, the traps, with maps and charts of the 10 October 2026 storm
  in Reggio Calabria drawn from real frames by `docs/make_images.py`.
- Documentation site (MkDocs Material) with the API reference, published to
  GitHub Pages by the `docs` workflow.
- Roadmap with an analysis of where AI and machine learning can improve the
  library, and through it Limen.
