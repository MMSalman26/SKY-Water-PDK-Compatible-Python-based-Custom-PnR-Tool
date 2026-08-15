"""Plugin loader + dummy placement through run_pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pnr_tool.algorithms.base import PlacementAlgorithm
from pnr_tool.algorithms.loader import PluginLoadError, load_placement, load_routing, load_clock_opt
from pnr_tool.algorithms.placement import ForceDirectedPlacement
from pnr_tool.pipeline.run import run_pipeline

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"

# Ensure tests.plugins is importable as a top-level package path
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_load_builtin_aliases():
    placer, pid = load_placement("default")
    assert isinstance(placer, ForceDirectedPlacement)
    assert pid == "default"
    _, cid = load_clock_opt("htree")
    assert cid == "htree"
    _, rid = load_routing("global")
    assert rid == "global"


def test_load_module_class_spec():
    placer, pid = load_placement("tests.plugins.dummy_placement:DummyPlacement")
    assert isinstance(placer, PlacementAlgorithm)
    assert pid == "tests.plugins.dummy_placement:DummyPlacement"


def test_load_bad_type_raises():
    with pytest.raises(PluginLoadError):
        load_placement("tests.plugins.dummy_placement:MissingClass")
    with pytest.raises(PluginLoadError):
        load_placement("not_a_real_module:Foo")


def test_dummy_plugin_through_pipeline(pdk_cache, tmp_path):
    result = run_pipeline(
        netlist=FIXTURES / "golden_three_cell.v",
        out_dir=tmp_path / "dummy_place",
        clock_period_ns=10.0,
        fetch_if_missing=True,
        layout_images=False,
        placement_algo="tests.plugins.dummy_placement:DummyPlacement",
    )
    assert result["algorithms"]["placement"] == "tests.plugins.dummy_placement:DummyPlacement"
    assert result["report"]["qor_schema"] == 2
    assert result["report"]["algorithms"]["placement"].endswith("DummyPlacement")
    assert result["design"].meta.get("completed_stage") == "routing"
    assert (tmp_path / "dummy_place" / "layout_view.json").exists()
