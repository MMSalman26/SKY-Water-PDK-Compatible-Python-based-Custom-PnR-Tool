"""DRC fidelity: width-aware shorts/spacing, T-junctions, vias, CIF, OBS."""

from __future__ import annotations

from pathlib import Path

from pnr_tool.checkers.drc import collect_drc_geometry, inflate_segment, run_drc
from pnr_tool.config import load_config
from pnr_tool.design.object import DesignObject
from pnr_tool.pdk.lef_parser import parse_lef_text
from pnr_tool.report.layout_cif import write_cif


def test_width_aware_spacing_ignores_far_gcell_tracks():
    design = DesignObject(name="w", die_area=(0, 0, 20, 20))
    design.tech = {
        "layers": {"met2": {"width_um": 0.14, "min_spacing_um": 0.14}},
        "mfg_grid_um": 0.005,
    }
    design.library = {"cells": {}}
    # 2 um gcell tracks: edge gap ~1.86 um >> 0.14
    design.routing = {
        "a": [{"layer": "met2", "x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 0.0}],
        "b": [{"layer": "met2", "x1": 0.0, "y1": 2.0, "x2": 10.0, "y2": 2.0}],
    }
    report = run_drc(design, load_config())
    assert report["counts_by_type"].get("spacing", 0) == 0
    assert report["counts_by_type"].get("short", 0) == 0


def test_width_aware_coincident_still_short():
    design = DesignObject(name="s", die_area=(0, 0, 20, 20))
    design.tech = {"layers": {"met2": {"width_um": 0.14, "min_spacing_um": 0.14}}}
    design.library = {"cells": {}}
    design.routing = {
        "a": [{"layer": "met2", "x1": 0.0, "y1": 0.0, "x2": 8.0, "y2": 0.0}],
        "b": [{"layer": "met2", "x1": 0.0, "y1": 0.0, "x2": 8.0, "y2": 0.0}],
    }
    report = run_drc(design, load_config())
    assert report["counts_by_type"].get("short", 0) >= 1
    shorts = [v for v in report["violations"] if v["type"] == "short"]
    assert shorts and shorts[0].get("rule") == "met2.short"


def test_tjunction_is_not_an_open():
    design = DesignObject(name="t", die_area=(0, 0, 40, 40))
    design.tech = {"layers": {"met2": {"width_um": 0.14, "min_spacing_um": 0.14}}}
    design.instances = {
        "drv": {"x": 0.0, "y": 4.0, "orientation": "N", "is_fixed": False, "width": 1.0, "height": 2.0},
        "snk": {"x": 20.0, "y": 0.0, "orientation": "N", "is_fixed": False, "width": 1.0, "height": 2.0},
    }
    design.cells = {
        "drv": {"cell_type": "buf", "pins": {"Y": "n0"}},
        "snk": {"cell_type": "inv", "pins": {"A": "n0"}},
    }
    design.library = {"cells": {"buf": {"pins": {}}, "inv": {"pins": {}}}}
    design.nets = {"n0": {"pins": [("drv", "Y"), ("snk", "A")], "driver": ("drv", "Y")}}
    # Trunk along y=5 from x=0..20; stub down from (20,5) to sink at ~center (20.5, 1)
    design.routing = {
        "n0": [
            {"layer": "met2", "x1": 0.5, "y1": 5.0, "x2": 20.5, "y2": 5.0},
            {"layer": "met2", "x1": 20.5, "y1": 5.0, "x2": 20.5, "y2": 1.0},
        ]
    }
    cfg = load_config()
    cfg["drc"]["pin_enclosure"] = False
    report = run_drc(design, cfg)
    opens = [v for v in report["violations"] if v["type"] == "open"]
    assert not opens, opens


def test_via_inferred_at_layer_change():
    design = DesignObject(name="v", die_area=(0, 0, 20, 20))
    design.tech = {
        "layers": {
            "met1": {"width_um": 0.14, "min_spacing_um": 0.14, "via_size_um": 0.15, "enclosure_um": 0.0},
            "met2": {"width_um": 0.14, "min_spacing_um": 0.14, "via_size_um": 0.15, "enclosure_um": 0.0},
        },
        "via_size_um": 0.14,
        "enclosure_um": 0.0,
    }
    design.library = {"cells": {}}
    design.routing = {
        "n0": [
            {"layer": "met1", "x1": 0.0, "y1": 2.0, "x2": 4.0, "y2": 2.0},
            {"layer": "met2", "x1": 4.0, "y1": 2.0, "x2": 4.0, "y2": 8.0},
        ]
    }
    geom = collect_drc_geometry(design, {})
    assert geom["vias"], "layer change should insert a via cut"
    assert geom["vias"][0].kind == "via"


def test_lef_obs_parsed():
    text = """
MACRO sky130_fd_sc_hd__inv_2
  SIZE 1.38 BY 2.72 ;
  PIN A
    DIRECTION INPUT ;
    PORT
      LAYER li1 ;
        RECT 0.1 0.2 0.3 0.4 ;
    END
  END A
  OBS
    LAYER met1 ;
      RECT 0.0 0.0 1.38 2.72 ;
  END
END sky130_fd_sc_hd__inv_2
"""
    cells = parse_lef_text(text)
    cell = cells["sky130_fd_sc_hd__inv_2"]
    assert cell["obs"]
    assert cell["obs"][0]["layer"] == "met1"


def test_cif_write(tmp_path):
    design = DesignObject(name="cif", die_area=(0, 0, 10, 10))
    design.tech = {"layers": {"met2": {"width_um": 0.14, "min_spacing_um": 0.14}}}
    design.library = {"cells": {}}
    design.routing = {"n0": [{"layer": "met2", "x1": 0.0, "y1": 1.0, "x2": 5.0, "y2": 1.0}]}
    path = write_cif(design, tmp_path / "t.cif")
    text = Path(path).read_text(encoding="utf-8")
    assert "CIF" in text
    assert "MET2" in text
    assert "B " in text


def test_inflate_uses_width():
    bbox = inflate_segment({"x1": 0, "y1": 1, "x2": 10, "y2": 1}, 0.14)
    assert abs((bbox[3] - bbox[1]) - 0.14) < 1e-9


def test_compare_drc_skips_without_gold(tmp_path, monkeypatch):
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "compare_drc.py"
    spec = importlib.util.spec_from_file_location("compare_drc", script)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    monkeypatch.setattr(harness.shutil, "which", lambda _n: None)
    design = DesignObject(name="cmp", die_area=(0, 0, 10, 10))
    design.tech = {"layers": {"met2": {"width_um": 0.14, "min_spacing_um": 0.14}}}
    design.library = {"cells": {}}
    design.routing = {"n0": [{"layer": "met2", "x1": 0.0, "y1": 0.0, "x2": 4.0, "y2": 0.0}]}
    result = harness.compare(design, tmp_path / "out", load_config())
    assert result["gold"]["skipped"] is True
    assert (tmp_path / "out" / "cmp.cif").exists()
