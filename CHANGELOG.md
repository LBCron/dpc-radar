# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `products`: the DPC radar products (SRI, SRT1, CUM3/6/12/24), the requests of
  `findLastProductByType` and `downloadProduct`, the parsers of their answers,
  and `is_expired` for a frame the DPC no longer keeps.
- `DpcRadarError`.
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
