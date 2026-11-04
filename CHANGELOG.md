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
