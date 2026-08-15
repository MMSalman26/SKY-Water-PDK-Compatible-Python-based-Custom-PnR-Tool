"""Build networkx graphs and run netlist sanity checks."""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

import networkx as nx

from .object import DesignObject


class NetlistSanityError(ValueError):
    pass


def build_timing_graph(design: DesignObject) -> nx.DiGraph:
    """Pin-level directed graph for STA (driver pin -> sink pin)."""
    g = nx.DiGraph()
    for inst, info in design.cells.items():
        cell_type = info["cell_type"]
        lib_cell = design.library.get("cells", {}).get(cell_type, {})
        g.add_node(inst, cell_type=cell_type, is_sequential=lib_cell.get("is_sequential", False))

    for net, ninfo in design.nets.items():
        driver = ninfo.get("driver")
        if driver is None:
            continue
        d_inst, d_pin = driver
        for s_inst, s_pin in ninfo.get("pins", []):
            if (s_inst, s_pin) == (d_inst, d_pin):
                continue
            # Skip sequential Q->D combinational edges through flops for loop detect;
            # for STA we still connect net edges; sequential breaks handled in STA.
            g.add_edge(d_inst, s_inst, net=net, driver_pin=d_pin, sink_pin=s_pin)
    design.graph = g
    return g


def sanity_check(design: DesignObject) -> List[str]:
    """Return warnings; raise on hard errors (loops that break topo, multi-driven)."""
    warnings: List[str] = []
    multi: List[str] = []
    floating: List[str] = []

    for net, ninfo in design.nets.items():
        drivers = ninfo.get("drivers", [])
        if len(drivers) > 1:
            multi.append(net)
        pins = ninfo.get("pins", [])
        if not pins and net not in design.ports:
            floating.append(net)
        # Undriven internal nets (no driver, has sinks)
        if ninfo.get("driver") is None and pins and net not in design.ports:
            # Allow if it's a primary input port name
            if design.ports.get(net, {}).get("direction") != "input":
                floating.append(net)

    if multi:
        raise NetlistSanityError(f"Multiply-driven nets: {multi[:20]}")

    # Combinational loops: build graph skipping sequential arcs (Q not driven by D in same cell)
    comb = nx.DiGraph()
    seq_cells = set()
    for inst, info in design.cells.items():
        cell_type = info["cell_type"]
        lib_cell = design.library.get("cells", {}).get(cell_type, {})
        if lib_cell.get("is_sequential"):
            seq_cells.add(inst)
        comb.add_node(inst)

    for net, ninfo in design.nets.items():
        driver = ninfo.get("driver")
        if driver is None:
            continue
        d_inst, d_pin = driver
        if str(d_inst).startswith("PORT:"):
            # Primary inputs feed sinks but are not combinational cells
            for s_inst, s_pin in ninfo.get("pins", []):
                if (s_inst, s_pin) == (d_inst, d_pin):
                    continue
                if str(s_inst).startswith("PORT:"):
                    continue
                if s_inst in seq_cells and s_pin.upper() in (
                    "D",
                    "CLK",
                    "CK",
                    "SET",
                    "RESET",
                    "RESET_B",
                    "SET_B",
                ):
                    continue
                if s_inst in design.cells:
                    comb.add_node(s_inst)
            continue
        # Outputs of flops break combinational loops
        cell_type = design.cells.get(d_inst, {}).get("cell_type")
        if cell_type is None:
            continue
        lib_cell = design.library.get("cells", {}).get(cell_type, {})
        if lib_cell.get("is_sequential") and d_pin.upper() in ("Q", "QN", "CLK", "CK"):
            # Still connect from flop Q to sinks — that's fine for loops FROM flop.
            pass
        for s_inst, s_pin in ninfo.get("pins", []):
            if (s_inst, s_pin) == (d_inst, d_pin):
                continue
            if str(s_inst).startswith("PORT:"):
                continue
            # Do not enter flop through data/clock as continuing comb path from inside flop
            if s_inst in seq_cells and s_pin.upper() in (
                "D",
                "CLK",
                "CK",
                "SET",
                "RESET",
                "RESET_B",
                "SET_B",
            ):
                continue
            if d_inst in design.cells and s_inst in design.cells:
                comb.add_edge(d_inst, s_inst, net=net)

    # Cheap acyclicity test first; enumerating cycles can blow up combinatorially.
    if not nx.is_directed_acyclic_graph(comb):
        if len(design.cells) > 500:
            raise NetlistSanityError("Combinational loop detected in netlist graph")
        cycles = list(nx.simple_cycles(comb))
        raise NetlistSanityError(f"Combinational loops detected: {cycles[:5]}")

    if floating:
        warnings.append(f"Possibly floating/undriven nets: {floating[:20]}")
    return warnings


_POWER_PINS = frozenset({"VGND", "VPWR", "VNB", "VPB", "VDD", "VSS", "VPWRA", "VGNDA"})


def infer_drivers(design: DesignObject) -> None:
    """Populate net driver fields from library pin directions."""
    for net, ninfo in design.nets.items():
        # Power rails are multi-fanout supplies, not logic drivers
        if net.upper() in _POWER_PINS:
            ninfo["drivers"] = []
            ninfo["driver"] = None
            continue
        drivers = []
        for inst, pin in ninfo.get("pins", []):
            if pin.upper() in _POWER_PINS:
                continue
            if inst.startswith("PORT:"):
                port_name = inst.split(":", 1)[1]
                direction = design.ports.get(port_name, {}).get("direction", "input")
                if direction == "input":
                    drivers.append((inst, pin))
                continue
            if inst not in design.cells:
                continue
            cell_type = design.cells[inst]["cell_type"]
            lib_cell = design.library.get("cells", {}).get(cell_type, {})
            pin_info = lib_cell.get("pins", {}).get(pin, {})
            use = str(pin_info.get("use", "SIGNAL")).upper()
            if use in ("POWER", "GROUND"):
                continue
            pin_dir = pin_info.get("direction", "input")
            if pin_dir in ("output", "inout"):
                drivers.append((inst, pin))
        ninfo["drivers"] = drivers
        ninfo["driver"] = drivers[0] if len(drivers) == 1 else (drivers[0] if drivers else None)
