"""The one exception this package raises."""

from __future__ import annotations


class DpcRadarError(Exception):
    """An answer of the DPC radar API that this package cannot read, or a bad argument."""


__all__ = ["DpcRadarError"]
