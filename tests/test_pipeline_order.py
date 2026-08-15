"""OpenLane-order pipeline: power → place → tap → decap → cts → route."""

from __future__ import annotations

from pathlib import Path

from pnr_tool.pipeline.run import run_pipeline

FIXTURES = Path(__file__).parent / "fixtures"


def test_power_place_tap_decap_stage_order(pdk_cache, tmp_path):
    result = run_pipeline(
        netlist=FIXTURES / "golden_seq.v",
        out_dir=tmp_path / "order",
        clock_period_ns=10.0,
        fetch_if_missing=True,
        layout_images=False,
    )
    design = result["design"]
    assert design.meta.get("completed_stage") == "routing"
    assert design.power_grid.get("segments")
    assert design.instances
    assert design.routing
    assert result["report"]["timing_s"]["power_plan"] >= 0.0
    assert result["report"]["timing_s"]["placement"] >= 0.0
    assert result["report"]["timing_s"]["tap"] >= 0.0
    assert result["report"]["timing_s"]["decap"] >= 0.0
    assert (tmp_path / "order" / "layout_view_power.json").exists()
    assert (tmp_path / "order" / "layout_view_placement.json").exists()
    assert (tmp_path / "order" / "layout_view_cts.json").exists()
    assert not (tmp_path / "order" / "layout_view_tap.json").exists()
    assert not (tmp_path / "order" / "layout_view_decap.json").exists()
    assert not (tmp_path / "order" / "layout_tap.png").exists()
    assert not (tmp_path / "order" / "layout_decap.png").exists()
    assert not any(i.get("physical") == "fill" for i in design.instances.values())


def test_cts_clusters_many_sinks(pdk_cache, tmp_path):
    result = run_pipeline(
        netlist=FIXTURES / "golden_seq.v",
        out_dir=tmp_path / "cts",
        clock_period_ns=10.0,
        fetch_if_missing=True,
        layout_images=False,
    )
    tree = result["design"].clock_tree
    assert "new_buffers" in tree
    assert "clock_nets" in tree


def test_cts_tree_depth_with_low_fanout(pdk_cache, tmp_path):
    cfg_path = tmp_path / "cts.yaml"
    cfg_path.write_text(
        "cts:\n  max_fanout: 4\n  max_levels: 10\n",
        encoding="utf-8",
    )
    result = run_pipeline(
        netlist=FIXTURES / "golden_many_ff.v",
        out_dir=tmp_path / "deep",
        clock_period_ns=10.0,
        config_path=cfg_path,
        fetch_if_missing=True,
        layout_images=False,
    )
    tree = result["design"].clock_tree
    assert len(tree.get("new_buffers", {})) > 1
    depths = [
        int(info.get("tree_depth", 0))
        for info in (tree.get("clock_nets") or {}).values()
    ]
    assert depths and max(depths) > 1
