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

### Documentation

- README rewritten for a wider audience: what the DPC radar is, the products,
  a quick start, the traps, with maps and charts of the 10 October 2026 storm
  in Reggio Calabria drawn from real frames by `docs/make_images.py`.
- Documentation site (MkDocs Material) with the API reference, published to
  GitHub Pages by the `docs` workflow.
- Roadmap with an analysis of where AI and machine learning can improve the
  library, and through it Limen.
