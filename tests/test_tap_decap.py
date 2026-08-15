"""Tap then decap insertion after placement (no filler cells)."""

from __future__ import annotations

from pathlib import Path

from pnr_tool.algorithms.floorplan import estimate_die_area
from pnr_tool.algorithms.placement import ForceDirectedPlacement
from pnr_tool.algorithms.power_plan import plan_power
from pnr_tool.algorithms.tap_decap import insert_decaps, insert_taps
from pnr_tool.config import load_config
from pnr_tool.design.graph import infer_drivers
from pnr_tool.io.verilog_parser import elaborate, parse_verilog_file
from pnr_tool.pdk.loader import ensure_cells, load_library_and_tech
from pnr_tool.pipeline.run import run_pipeline

FIXTURES = Path(__file__).parent / "fixtures"


def _load_design(pdk_cache, netlist: Path):
    library, tech = load_library_and_tech(pdk_cache)
    mods = parse_verilog_file(netlist)
    design = elaborate(mods, library_cell_names=set(library.get("cells", {})))
    used = {info["cell_type"] for info in design.cells.values()}
    ensure_cells(library, used, tech)
    design.library = library
    design.tech = tech
    infer_drivers(design)
    return design


def test_tap_then_decap_no_fillers(pdk_cache):
    design = _load_design(pdk_cache, FIXTURES / "golden_three_cell.v")
    base = next(iter(design.cells.values()))
    for i in range(60):
        design.cells[f"c{i}"] = {
            "cell_type": base["cell_type"],
            "pins": dict(base.get("pins", {})),
        }
    cfg = load_config()
    estimate_die_area(design, cfg)
    plan_power(design, cfg)
    design.instances = ForceDirectedPlacement().execute(design, cfg)

    tap_summary = insert_taps(design, cfg)
    assert tap_summary["enabled"]
    assert tap_summary["taps"] >= 2
    taps = [n for n, i in design.instances.items() if i.get("physical") == "tap"]
    rows = {round(float(design.instances[n]["y"]), 3) for n in taps}
    assert len(rows) >= 2

    decap_summary = insert_decaps(design, cfg)
    assert decap_summary["enabled"]
    assert decap_summary["decaps"] > 0
    fills = [n for n, i in design.instances.items() if i.get("physical") == "fill"]
    assert fills == []
    fill_types = [
        info.get("cell_type", "")
        for info in design.cells.values()
        if "fill_" in str(info.get("cell_type", ""))
    ]
    assert fill_types == []


def test_pipeline_writes_tap_and_decap_stages(pdk_cache, tmp_path):
    result = run_pipeline(
        netlist=FIXTURES / "golden_seq.v",
        out_dir=tmp_path / "tap",
        clock_period_ns=10.0,
        fetch_if_missing=True,
        layout_images=False,
    )
    design = result["design"]
    assert design.meta.get("tap", {}).get("taps", 0) >= 1
    assert design.meta.get("decap", {}).get("decaps", 0) >= 0
    assert not any(i.get("physical") == "fill" for i in design.instances.values())
    assert not (tmp_path / "tap" / "layout_view_tap.json").exists()
    assert not (tmp_path / "tap" / "layout_decap.png").exists()
    assert result["report"]["timing_s"].get("tap", 0) >= 0
    assert result["report"]["timing_s"].get("decap", 0) >= 0
