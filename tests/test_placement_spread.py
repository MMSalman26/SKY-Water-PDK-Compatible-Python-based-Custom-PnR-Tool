"""RePlAce-style rectangular placement fill + random alias regression tests."""

from __future__ import annotations

from pathlib import Path

from pnr_tool.algorithms.floorplan import estimate_die_area
from pnr_tool.algorithms.loader import load_placement
from pnr_tool.algorithms.placement import ForceDirectedPlacement
from pnr_tool.algorithms.power_plan import plan_power
from pnr_tool.algorithms.random_place import RandomPlacement
from pnr_tool.config import load_config
from pnr_tool.design.graph import infer_drivers
from pnr_tool.io.verilog_parser import elaborate, parse_verilog_file
from pnr_tool.pdk.loader import ensure_cells, load_library_and_tech

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


def _bbox_coverage(instances, die_area):
    minx, miny, maxx, maxy = die_area
    die_w = max(maxx - minx, 1e-9)
    die_h = max(maxy - miny, 1e-9)
    xs = [inst["x"] + 0.5 * inst.get("width", 0.0) for inst in instances.values()]
    ys = [inst["y"] + 0.5 * inst.get("height", 0.0) for inst in instances.values()]
    span_x = (max(xs) - min(xs)) / die_w
    span_y = (max(ys) - min(ys)) / die_h
    return span_x, span_y


def _quadrant_counts(instances, die_area):
    minx, miny, maxx, maxy = die_area
    midx = 0.5 * (minx + maxx)
    midy = 0.5 * (miny + maxy)
    counts = [0, 0, 0, 0]
    for inst in instances.values():
        cx = float(inst["x"]) + 0.5 * float(inst.get("width", 0.0))
        cy = float(inst["y"]) + 0.5 * float(inst.get("height", 0.0))
        qi = (0 if cx < midx else 1) + (0 if cy < midy else 2)
        counts[qi] += 1
    return counts


def test_load_random_alias():
    placer, pid = load_placement("random")
    assert isinstance(placer, RandomPlacement)
    assert pid == "random"


def test_power_before_place_zero_instances(pdk_cache):
    design = _load_design(pdk_cache, FIXTURES / "golden_three_cell.v")
    cfg = load_config()
    estimate_die_area(design, cfg)
    grid = plan_power(design, cfg)
    assert design.instances == {}
    assert grid.get("segments")
    assert design.power_grid.get("segments")


def test_random_placement_spans_die(pdk_cache):
    design = _load_design(pdk_cache, FIXTURES / "golden_three_cell.v")
    base = next(iter(design.cells.values()))
    for i in range(80):
        design.cells[f"c{i}"] = {
            "cell_type": base["cell_type"],
            "pins": dict(base.get("pins", {})),
        }
    cfg = load_config()
    estimate_die_area(design, cfg)
    plan_power(design, cfg)
    cfg.setdefault("placement", {})["seed"] = 7
    instances = RandomPlacement().execute(design, cfg)
    span_x, span_y = _bbox_coverage(instances, design.die_area)
    assert span_x >= 0.70
    assert span_y >= 0.70


def test_replace_style_placer_fills_rectangle(pdk_cache):
    design = _load_design(pdk_cache, FIXTURES / "golden_three_cell.v")
    base = next(iter(design.cells.values()))
    for i in range(120):
        design.cells[f"g{i}"] = {
            "cell_type": base["cell_type"],
            "pins": dict(base.get("pins", {})),
        }
        if i > 0:
            net = f"n{i}"
            design.nets[net] = {
                "pins": [(f"g{i-1}", "Y"), (f"g{i}", "A")],
                "drivers": [],
                "driver": None,
            }
            design.cells[f"g{i-1}"]["pins"]["Y"] = net
            design.cells[f"g{i}"]["pins"]["A"] = net
    cfg = load_config()
    estimate_die_area(design, cfg)
    plan_power(design, cfg)
    place = cfg.setdefault("placement", {})
    place["iterations"] = 50
    instances = ForceDirectedPlacement().execute(design, cfg)
    assert design.power_grid.get("segments")  # PDN preserved through place
    span_x, span_y = _bbox_coverage(instances, design.die_area)
    assert span_x >= 0.85
    assert span_y >= 0.85
    q = _quadrant_counts(instances, design.die_area)
    assert all(c > 0 for c in q), f"empty quadrant(s): {q}"
