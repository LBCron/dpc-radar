# dpc-radar

Async Python client for the Italian Civil Protection (DPC) open radar data: SRI, Merging (CUM), history and criticality bulletins

[![ci](https://github.com/agent-engineering-studio/dpc-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/agent-engineering-studio/dpc-radar/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dpc-radar)](https://pypi.org/project/dpc-radar/)


> **Work in progress.** This package is being extracted from
> [Limen](https://github.com/agent-engineering-studio/limen), where the code
> already runs in production. For now only the pure product definitions and the
> API request/answer helpers are here. The client, the rain grid with its 3x3
> median, the frame check, the «Merging» archive, the IT-DPC-SRI history and
> the criticality bulletins follow. Nothing is on PyPI yet.

## What is already here

```python
from dpc_radar import CUM3, parse_last_product, products

url, params = products.last_product_url(CUM3)  # GET, with products.HEADERS
latest = parse_last_product(answer_json)  # time + period of the newest frame
```

| Product | Window | Source | Grid | New frame |
|---|---|---|---|---|
| `SRI` | intensity (mm/h) | radar only | 1 km | every 5 min |
| `SRT1` | 1 h (mm) | radar only | 1 km | every 5 min |
| `CUM3`, `CUM6`, `CUM12`, `CUM24` | 3–24 h (mm) | «Merging»: radar + rain gauges | 0.01°, EPSG:4326 | every 30 min |

There is no `CUM1`: the API answers it with an empty list. A frame the DPC no
longer keeps (about five months back) answers **500** with S3's `403 Forbidden`
in the body, not 404: `is_expired(status, body)` tells it apart from a failure
worth retrying.

## Data licence

The DPC radar data are open data under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/): credit the
*Dipartimento della Protezione Civile* and share derived data under the same
licence.

## Install

```bash
pip install dpc-radar
```

## Development

```bash
uv sync
make check
```

## License

[Apache-2.0](./LICENSE).
