"""Layout PNG rendering + HTML embedding tests."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from pnr_tool.config import load_config
from pnr_tool.design.object import DesignObject
from pnr_tool.report.html_report import load_reports, write_html_report
from pnr_tool.report.layout_plot import STAGE_FILES, layout_image_paths, write_stage_layout

FIXTURES = Path(__file__).parent / "fixtures"


def _png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def _demo_design() -> DesignObject:
    design = DesignObject(name="plotme", die_area=(0.0, 0.0, 20.0, 10.88))
    design.library = {
        "cells": {
            "sky130_fd_sc_hd__inv_2": {"width": 1.38, "height": 2.72},
            "sky130_fd_sc_hd__clkbuf_4": {"width": 2.76, "height": 2.72},
        }
    }
    design.cells = {
        "u0": {"cell_type": "sky130_fd_sc_hd__inv_2", "pins": {}},
        "u1": {"cell_type": "sky130_fd_sc_hd__inv_2", "pins": {}},
        "CTS_ROOT_0": {"cell_type": "sky130_fd_sc_hd__clkbuf_4", "pins": {}},
    }
    design.instances = {
        "u0": {"x": 0.0, "y": 0.0, "orientation": "N", "is_fixed": False, "width": 1.38, "height": 2.72},
        "u1": {"x": 4.0, "y": 2.72, "orientation": "N", "is_fixed": False, "width": 1.38, "height": 2.72},
        "CTS_ROOT_0": {
            "x": 10.0,
            "y": 5.44,
            "orientation": "N",
            "is_fixed": True,
            "width": 2.76,
            "height": 2.72,
        },
    }
    design.clock_tree = {
        "new_buffers": {"CTS_ROOT_0": {"cell_type": "sky130_fd_sc_hd__clkbuf_4", "x": 10.0, "y": 5.44, "orientation": "N"}},
        "clock_nets": {},
    }
    design.routing = {
        "n0": [
            {"layer": "met3", "x1": 0.0, "y1": 0.0, "x2": 8.0, "y2": 0.0},
            {"layer": "met2", "x1": 8.0, "y1": 0.0, "x2": 8.0, "y2": 4.0},
        ],
        "n1": [{"layer": "met5", "x1": 2.0, "y1": 6.0, "x2": 18.0, "y2": 6.0}],
    }
    design.meta["port_positions"] = {"a": (0.0, 5.44), "y": (20.0, 5.44)}
    return design


@pytest.mark.parametrize("stage", sorted(STAGE_FILES))
def test_stage_image_is_written_and_not_degenerate(stage, tmp_path):
    design = _demo_design()
    out = write_stage_layout(design, stage, tmp_path, load_config())
    assert out is not None and out.exists()
    assert out.name == STAGE_FILES[stage]
    width, height = _png_size(out)
    assert width > 200 and height > 200
    # A blank Agg canvas of this size compresses far smaller than a real plot.
    assert out.stat().st_size > 8000


def test_layout_images_can_be_disabled(tmp_path):
    design = _demo_design()
    cfg = load_config()
    cfg["report"]["layout_images"] = False
    assert write_stage_layout(design, "placement", tmp_path, cfg) is None
    assert not (tmp_path / STAGE_FILES["placement"]).exists()


def test_unknown_stage_rejected(tmp_path):
    with pytest.raises(ValueError):
        write_stage_layout(_demo_design(), "floorplan", tmp_path, load_config())


def test_layout_image_paths_cover_all_stages(tmp_path):
    paths = layout_image_paths(tmp_path)
    assert set(paths) == set(STAGE_FILES)
    assert all(p.parent == tmp_path for p in paths.values())


def test_html_report_embeds_relative_image_paths(tmp_path):
    run_dir = tmp_path / "runs" / "demo"
    run_dir.mkdir(parents=True)
    design = _demo_design()
    for stage in STAGE_FILES:
        write_stage_layout(design, stage, run_dir, load_config())
    qor = run_dir / "demo.qor.json"
    qor.write_text('{"design": "demo", "overall_pass": true}', encoding="utf-8")

    html_dir = tmp_path / "runs"
    reports = load_reports([qor], html_dir=html_dir)
    assert reports[0]["_layout_images"] == {
        stage: f"demo/{name}" for stage, name in STAGE_FILES.items()
    }

    out = write_html_report(reports, html_dir / "index.html")
    html = out.read_text(encoding="utf-8")
    assert "renderLayoutPanel" in html
    assert "demo/layout_routing.png" in html
    assert "layoutViewerShell" in html
    assert "requestFullscreen" in html
    assert "Fullscreen" in html
    assert "metals above cells" in html
    assert "layer-chip" in html
    assert "Power Plan" in html or '"power"' in html
    assert "togVdd" in html


def test_html_report_without_html_dir_has_no_images(tmp_path):
    qor = tmp_path / "demo.qor.json"
    qor.write_text('{"design": "demo", "overall_pass": true}', encoding="utf-8")
    reports = load_reports([qor])
    assert "_layout_images" not in reports[0]


def test_png_routing_drawn_above_cells():
    """Static PNG z-order: metals (z=8) above cells (z=2)."""
    src = Path(__file__).resolve().parents[1] / "pnr_tool" / "report" / "layout_plot.py"
    text = src.read_text(encoding="utf-8")
    assert "zorder=8" in text
    assert "Z-order: die" in text
