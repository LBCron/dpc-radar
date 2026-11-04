from __future__ import annotations

import dpc_radar


def test_version_is_exposed() -> None:
    assert dpc_radar.__version__
