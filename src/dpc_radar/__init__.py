"""dpc-radar: the Italian Civil Protection (DPC) open radar data, from Python."""

from importlib.metadata import version

from dpc_radar.client import ABSENT, Absent, RadarClient
from dpc_radar.errors import DpcRadarError
from dpc_radar.grid import FrameCheck, RainGrid
from dpc_radar.products import (
    API_BASE,
    CUM3,
    CUM6,
    CUM12,
    CUM24,
    PRODUCTS,
    SRI,
    SRT1,
    LastProduct,
    Product,
    is_expired,
    parse_download,
    parse_last_product,
    product,
)

__version__ = version("dpc-radar")

__all__ = [
    "ABSENT",
    "API_BASE",
    "CUM3",
    "CUM6",
    "CUM12",
    "CUM24",
    "PRODUCTS",
    "SRI",
    "SRT1",
    "Absent",
    "DpcRadarError",
    "FrameCheck",
    "LastProduct",
    "Product",
    "RadarClient",
    "RainGrid",
    "__version__",
    "is_expired",
    "parse_download",
    "parse_last_product",
    "product",
]
