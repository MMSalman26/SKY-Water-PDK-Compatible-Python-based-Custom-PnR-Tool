"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from pnr_tool.config import load_config
from pnr_tool.pdk.fetch import fetch_pdk, pdk_ready
from pnr_tool.pdk.loader import load_library_and_tech


@pytest.fixture(scope="session")
def pdk_cache():
    cfg = load_config()
    cache = Path(cfg["pdk"]["cache_dir"])
    if not pdk_ready(cache):
        fetch_pdk(cache)
    assert pdk_ready(cache), "PDK fetch did not produce a usable cache"
    return cache


@pytest.fixture(scope="session")
def library_tech(pdk_cache):
    return load_library_and_tech(pdk_cache)
