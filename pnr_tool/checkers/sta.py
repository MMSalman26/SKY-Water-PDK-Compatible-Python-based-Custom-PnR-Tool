"""OpenSTA-inspired STA: dual-corner NLDM, tree Elmore/D2M, CRPR, SDC subset."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx

from pnr_tool.checkers.sdc import (
    is_false_path,
    load_sdc,
    multicycle_offset,
    port_input_delay_ns,
    port_output_delay_ns,
)
from pnr_tool.design.object import DesignObject
from pnr_tool.pdk.lib_parser import (
    async_constraint_pins,
    lookup_cell_delay,
    lookup_setup_hold,
    related_pins_for_output,
)

PinKey = Tuple[str, str]
Point = Tuple[float, float]


def _pin_cap(design: DesignObject, inst: str, pin: str) -> float:
    if inst.startswith("PORT:"):
        return 0.005
    info = design.cells.get(inst)
    if info is None:
        return 0.002
    lib = design.library.get("cells", {}).get(info["cell_type"], {})
    return float(lib.get("pins", {}).get(pin, {}).get("capacitance", 0.002) or 0.002)


def _pin_xy(design: DesignObject, inst: str, pin: str) -> Optional[Point]:
    from pnr_tool.algorithms.pins import instance_pin_xy

    xy = instance_pin_xy(design, inst, pin)
    if xy is not None:
        return (float(xy[0]), float(xy[1]))
    if str(inst).startswith("PORT:"):
        pos = (design.meta.get("port_positions") or {}).get(str(inst).split(":", 1)[1])
        if pos is not None:
            return (float(pos[0]), float(pos[1]))
        return None
    inst_d = design.instances.get(inst)
    if inst_d is None:
        return None
    return (
        float(inst_d["x"]) + 0.5 * float(inst_d.get("width", 0.0)),
        float(inst_d["y"]) + 0.5 * float(inst_d.get("height", 0.0)),
    )


def _seg_rc(seg: dict, tech: dict, default_layer: str = "met2") -> Tuple[float, float, str]:
    """Width-aware R/C for a routed segment. Returns (R_ohm, C_F, layer)."""
    layers = tech.get("layers", {})
    width_ref = float(tech.get("width_ref_um", 0.14))
    length = abs(float(seg.get("x2", 0)) - float(seg.get("x1", 0))) + abs(
        float(seg.get("y2", 0)) - float(seg.get("y1", 0))
    )
    layer = str(seg.get("layer", default_layer))
    info = layers.get(layer, layers.get(default_layer, {}))
    width = float(seg.get("width_um") or info.get("width_um") or info.get("pitch_um") or width_ref)
    r_per = float(info.get("r_per_um", 0.125)) * (width_ref / max(width, 1e-9))
    c_per = float(info.get("c_per_um", 0.14e-15)) * (max(width, 1e-9) / max(width_ref, 1e-9))
    return r_per * length, c_per * length, layer


def _d2m_delay(elmore_ns: float) -> float:
    """D2M two-pole delay vs first-moment Elmore (Kahng/Pillage ~1.047×)."""
    return float(elmore_ns) * 1.047


def _ceff_pf(c_pin_pf: float, c_wire_pf: float, r_ohm: float, slew_ns: float) -> float:
    """Resistive-shielding Ceff for NLDM load (pin + shielded wire)."""
    c_wire_pf = max(0.0, float(c_wire_pf))
    c_pin_pf = max(0.0, float(c_pin_pf))
    if r_ohm <= 0.0 or c_wire_pf <= 0.0 or slew_ns <= 0.0:
        return c_pin_pf + c_wire_pf
    tau_ns = r_ohm * (c_wire_pf * 1e-12) * 1e9
    x = float(slew_ns) / max(tau_ns, 1e-9)
    x = min(x, 40.0)
    shield = 1.0 if x < 1e-6 else (1.0 - math.exp(-x)) / x
    shield = max(0.35, min(1.0, shield))
    return c_pin_pf + c_wire_pf * shield


def _rc_scales(config: Dict[str, Any]) -> Tuple[float, float]:
    sta = config.get("sta", {}) or {}
    return float(sta.get("rc_min_scale", 0.72)), float(sta.get("rc_max_scale", 1.08))


def _lib_cells(design: DesignObject, which: str) -> Dict[str, Any]:
    corners = design.library.get("corners") or {}
    if which in corners and corners[which]:
        return corners[which]
    return design.library.get("cells", {})


def _ss_delay_scale(design: DesignObject) -> float:
    if design.library.get("corners", {}).get("ss"):
        return 1.0
    return 1.35


def _topo_order(g: nx.DiGraph, cell_names: List[str]) -> Tuple[List[str], int]:
    """Topological order; break combinational SCCs instead of random order."""
    broken = 0
    work = g.copy()
    try:
        return list(nx.topological_sort(work)), 0
    except nx.NetworkXUnfeasible:
        pass
    for scc in list(nx.strongly_connected_components(work)):
        if len(scc) < 2:
            continue
        nodes = list(scc)
        # Drop one back-edge from the last node in the component
        u = nodes[-1]
        for v in list(work.successors(u)):
            if v in scc:
                work.remove_edge(u, v)
                broken += 1
                break
    try:
        return list(nx.topological_sort(work)), broken
    except nx.NetworkXUnfeasible:
        return list(cell_names), broken


def _apply_wire_model(delay_ns: float, wire_model: str) -> float:
    if wire_model == "d2m":
        return _d2m_delay(delay_ns)
    return delay_ns


def _lumped_net_electricals(
    design: DesignObject,
) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]:
    """Legacy whole-net π-Elmore (fallback / lumped_elmore mode)."""
    layers = design.tech.get("layers", {})
    default_layer = "met2"
    loads: Dict[str, float] = {}
    elmore: Dict[str, float] = {}
    wire_slew: Dict[str, float] = {}
    instances = design.instances

    for net, ninfo in design.nets.items():
        r_total = 0.0
        c_wire_f = 0.0
        length = 0.0
        prev_layer = None
        for seg in design.routing.get(net, ()):
            dx = abs(float(seg["x2"]) - float(seg["x1"]))
            dy = abs(float(seg["y2"]) - float(seg["y1"]))
            length += dx + dy
            r_seg, c_seg, layer = _seg_rc(seg, design.tech, default_layer)
            r_total += r_seg
            c_wire_f += c_seg
            if prev_layer is not None and layer != prev_layer:
                via = float(layers.get(layer, {}).get("via_r_ohm", 5.0))
                r_total += via
            prev_layer = layer

        driver = ninfo.get("driver")
        dkey = tuple(driver) if driver else None
        pins = ninfo.get("pins", [])
        pin_c_pf = 0.0
        for inst, pin in pins:
            if dkey is not None and (inst, pin) == dkey:
                continue
            pin_c_pf += _pin_cap(design, inst, pin)

        if length <= 0 and r_total <= 0:
            placed = [p for p in pins if p[0] in instances]
            if len(placed) >= 2:
                xs = [float(instances[p[0]]["x"]) for p in placed]
                ys = [float(instances[p[0]]["y"]) for p in placed]
                eff_len = (max(xs) - min(xs)) + (max(ys) - min(ys))
                tech_l = layers.get(default_layer, {})
                r_per = float(tech_l.get("r_per_um", 0.125))
                c_per = float(tech_l.get("c_per_um", 0.14e-15))
                r_total = r_per * eff_len
                c_wire_f = c_per * eff_len

        c_pin_f = pin_c_pf * 1e-12
        loads[net] = pin_c_pf + c_wire_f * 1e12
        elmore[net] = 0.69 * r_total * (c_wire_f * 0.5 + c_pin_f) * 1e9
        wire_slew[net] = 0.69 * r_total * (c_wire_f + c_pin_f) * 1e9 * 0.5
    return loads, elmore, wire_slew


def _tree_net_electricals(
    design: DesignObject,
) -> Tuple[
    Dict[str, float],
    Dict[str, float],
    Dict[str, float],
    Dict[str, Dict[PinKey, float]],
    Dict[str, Dict[PinKey, float]],
]:
    """Per-net load + per-sink tree Elmore / slew from routed segment graph."""
    layers = design.tech.get("layers", {})
    default_layer = "met2"
    loads: Dict[str, float] = {}
    elmore_net: Dict[str, float] = {}
    wire_slew_net: Dict[str, float] = {}
    sink_delay: Dict[str, Dict[PinKey, float]] = {}
    sink_slew: Dict[str, Dict[PinKey, float]] = {}

    lumped_l, lumped_e, lumped_s = _lumped_net_electricals(design)

    for net, ninfo in design.nets.items():
        loads[net] = lumped_l.get(net, 0.0)
        segs = list(design.routing.get(net, ()) or [])
        driver = ninfo.get("driver")
        pins = list(ninfo.get("pins", []) or [])
        if not segs or not driver:
            elmore_net[net] = lumped_e.get(net, 0.0)
            wire_slew_net[net] = lumped_s.get(net, 0.0)
            sink_delay[net] = {}
            sink_slew[net] = {}
            continue

        # Segment graph: nodes = snapped endpoints; edges with R, C
        snap = 0.5
        adj: Dict[Point, List[Tuple[Point, float, float]]] = defaultdict(list)

        def snap_pt(x: float, y: float) -> Point:
            return (round(x / snap) * snap, round(y / snap) * snap)

        for seg in segs:
            a = snap_pt(float(seg["x1"]), float(seg["y1"]))
            b = snap_pt(float(seg["x2"]), float(seg["y2"]))
            length = abs(a[0] - b[0]) + abs(a[1] - b[1])
            if length < 1e-12:
                continue
            r, c, _layer = _seg_rc(seg, design.tech, default_layer)
            adj[a].append((b, r, c))
            adj[b].append((a, r, c))

        d_xy = _pin_xy(design, driver[0], driver[1])
        if d_xy is None or not adj:
            elmore_net[net] = lumped_e.get(net, 0.0)
            wire_slew_net[net] = lumped_s.get(net, 0.0)
            sink_delay[net] = {}
            sink_slew[net] = {}
            continue

        root = snap_pt(d_xy[0], d_xy[1])
        # Attach root to nearest graph node
        if root not in adj:
            best = min(adj.keys(), key=lambda p: abs(p[0] - root[0]) + abs(p[1] - root[1]))
            dist = abs(best[0] - root[0]) + abs(best[1] - root[1])
            tech_l = layers.get(default_layer, {})
            r = float(tech_l.get("r_per_um", 0.125)) * max(dist, snap)
            c = float(tech_l.get("c_per_um", 0.14e-15)) * max(dist, snap)
            adj[root].append((best, r, c))
            adj[best].append((root, r, c))

        # BFS spanning tree from root; accumulate R_path and C_subtree later
        parent: Dict[Point, Optional[Point]] = {root: None}
        edge_rc: Dict[Tuple[Point, Point], Tuple[float, float]] = {}
        q: deque[Point] = deque([root])
        while q:
            u = q.popleft()
            for v, r, c in adj.get(u, []):
                if v in parent:
                    continue
                parent[v] = u
                edge_rc[(u, v)] = (r, c)
                q.append(v)

        # Subtree wire C at each node (bottom-up)
        children: Dict[Point, List[Point]] = defaultdict(list)
        for v, p in parent.items():
            if p is not None:
                children[p].append(v)

        # Pin capacitances attached to nearest tree nodes
        pin_c_at: Dict[Point, float] = defaultdict(float)
        pin_at_node: Dict[PinKey, Point] = {}
        for inst, pin in pins:
            if (inst, pin) == (driver[0], driver[1]):
                continue
            xy = _pin_xy(design, inst, pin)
            if xy is None or not parent:
                continue
            node = min(
                parent.keys(),
                key=lambda p: abs(p[0] - xy[0]) + abs(p[1] - xy[1]),
            )
            pin_at_node[(inst, pin)] = node
            pin_c_at[node] += _pin_cap(design, inst, pin) * 1e-12

        # Post-order for subtree C
        order: List[Point] = []
        stack = [root]
        seen = {root}
        while stack:
            u = stack.pop()
            order.append(u)
            for v in children.get(u, []):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        order.reverse()
        sub_c: Dict[Point, float] = {}
        for u in order:
            c_here = pin_c_at.get(u, 0.0)
            for v in children.get(u, []):
                r, c_edge = edge_rc.get((u, v), (0.0, 0.0))
                c_here += c_edge + sub_c.get(v, 0.0)
            sub_c[u] = c_here

        # Elmore to each node: sum R_edge * C_downstream along path
        delay_to: Dict[Point, float] = {root: 0.0}
        slew_to: Dict[Point, float] = {root: 0.0}
        pre: deque[Point] = deque([root])
        while pre:
            u = pre.popleft()
            for v in children.get(u, []):
                r, c_edge = edge_rc.get((u, v), (0.0, 0.0))
                c_down = c_edge * 0.5 + sub_c.get(v, 0.0)
                d = delay_to[u] + 0.69 * r * c_down * 1e9
                s = slew_to[u] + 0.69 * r * (c_edge + sub_c.get(v, 0.0)) * 1e9 * 0.5
                delay_to[v] = d
                slew_to[v] = s
                pre.append(v)

        sd: Dict[PinKey, float] = {}
        ss: Dict[PinKey, float] = {}
        for pk, node in pin_at_node.items():
            sd[pk] = float(delay_to.get(node, lumped_e.get(net, 0.0)))
            ss[pk] = float(slew_to.get(node, lumped_s.get(net, 0.0)))
        sink_delay[net] = sd
        sink_slew[net] = ss
        # Net-level summary = max sink delay (for port / legacy paths)
        if sd:
            elmore_net[net] = max(sd.values())
            wire_slew_net[net] = max(ss.values()) if ss else lumped_s.get(net, 0.0)
        else:
            elmore_net[net] = lumped_e.get(net, 0.0)
            wire_slew_net[net] = lumped_s.get(net, 0.0)

    return loads, elmore_net, wire_slew_net, sink_delay, sink_slew


def _driver_pin_nets(design: DesignObject) -> Dict[PinKey, str]:
    out: Dict[PinKey, str] = {}
    for net, ninfo in design.nets.items():
        driver = ninfo.get("driver")
        if driver:
            out.setdefault((driver[0], driver[1]), net)
    return out


def _default_input_pins(cell: dict) -> List[str]:
    return [
        p
        for p, info in cell.get("pins", {}).items()
        if info.get("direction") == "input" and not info.get("is_clock")
    ]


def _default_output_pins(cell: dict) -> List[str]:
    return [p for p, info in cell.get("pins", {}).items() if info.get("direction") == "output"]


def _clock_pin(cell: dict) -> Optional[str]:
    for p, info in cell.get("pins", {}).items():
        if info.get("is_clock"):
            return p
    if "CLK" in cell.get("pins", {}):
        return "CLK"
    return None


def _timing_sense(cell: dict, related: str, out_pin: str) -> str:
    """Return positive_unate / negative_unate / non_unate (default positive)."""
    pins = cell.get("pins", {})
    pinfo = pins.get(out_pin, {})
    for t in pinfo.get("timing_arcs", []) or []:
        if t.get("related_pin") == related:
            return str(t.get("timing_sense") or "positive_unate")
    for t in pinfo.get("timing", []) or []:
        if t.get("related_pin") == related:
            return str(t.get("timing_sense") or "positive_unate")
    for arc in cell.get("timing_arcs", []) or []:
        if arc.get("related_pin") == related and (
            arc.get("output_pin") == out_pin or arc.get("pin") == out_pin
        ):
            return str(arc.get("timing_sense") or "positive_unate")
    return "positive_unate"


def _cts_timing(design: DesignObject, config: Dict[str, Any]) -> Dict[str, Any]:
    """CTS launch latency, slew, and root→sink buffer path (for CRPR)."""
    lat = _cts_latency_map(design, config)
    paths: Dict[str, List[str]] = {}
    slew: Dict[str, float] = {}
    tree = design.clock_tree or {}
    for _clk, info in (tree.get("clock_nets") or {}).items():
        root = info.get("root") or "__clkport__"
        levels = info.get("levels") or []
        sinks = info.get("sinks") or {}
        level_by_net = {lvl.get("net"): lvl for lvl in levels if lvl.get("net")}
        for net_name, members in sinks.items():
            lvl = level_by_net.get(net_name) or {}
            buf = lvl.get("buffer") or root
            path = [str(root)]
            if buf and buf != root:
                path.append(str(buf))
            for inst in members:
                paths[inst] = path
                slew[inst] = 0.05 + 0.25 * abs(lat.get(inst, 0.0))
    for inst, delay in lat.items():
        paths.setdefault(inst, ["__clkport__"])
        slew.setdefault(inst, 0.05 + 0.25 * abs(delay))
    return {"lat": lat, "path": paths, "slew": slew}


def _crpr_ns(
    launch: Optional[str],
    capture: Optional[str],
    cts: Dict[str, Any],
    rc_min: float,
    rc_max: float,
) -> float:
    """Common-path pessimism removal: credit late−early on the shared clock prefix."""
    if not capture:
        return 0.0
    p_l = list((cts.get("path") or {}).get(launch or "", ["__clkport__"]))
    p_c = list((cts.get("path") or {}).get(capture, ["__clkport__"]))
    common = 0
    for a, b in zip(p_l, p_c):
        if a != b:
            break
        common += 1
    lat_map = cts.get("lat") or {}
    lat_l = float(lat_map.get(launch or "", 0.0))
    lat_c = float(lat_map.get(capture, 0.0))
    denom = max(max(len(p_l), len(p_c)), 1)
    frac = common / denom if common else (1.0 if launch == capture else 0.35)
    shared = min(lat_l, lat_c) if launch else lat_c
    shared = abs(shared) * frac
    return max(0.0, shared * max(0.0, rc_max - rc_min))


def _cts_latency_map(design: DesignObject, config: Dict[str, Any]) -> Dict[str, float]:
    """Map sequential instance -> clock arrival (ns) from CTS geometry."""
    sta_cfg = config.get("sta", {})
    if not sta_cfg.get("use_cts_latency", True):
        return {}
    tree = design.clock_tree or {}
    clock_nets = tree.get("clock_nets", {})
    layers = design.tech.get("layers", {})
    met2 = layers.get("met2", {})
    r_per = float(met2.get("r_per_um", 0.125))
    c_per = float(met2.get("c_per_um", 0.14e-15))
    lib_cells = design.library.get("cells", {})

    lat: Dict[str, float] = {}
    for _clk, info in clock_nets.items():
        root = info.get("root")
        levels = info.get("levels") or []
        sinks = info.get("sinks") or {}

        root_delay = 0.05
        if root and root in design.cells:
            ctype = design.cells[root]["cell_type"]
            cell = lib_cells.get(ctype, {})
            d, _ = lookup_cell_delay(cell, "A", "X", 0.05, 0.05, "rise")
            if d == 0.05:
                d, _ = lookup_cell_delay(cell, "A", "Y", 0.05, 0.05, "rise")
            root_delay = d if d != 0.05 else 0.08

        if info.get("pre_existing_buffers", 0) and not levels:
            minx, miny, maxx, maxy = design.die_area
            diag = (maxx - minx) + (maxy - miny)
            base = 0.69 * (r_per * diag * 0.25) * (c_per * diag * 0.25) * 1e9
            for _net, members in sinks.items():
                for inst in members:
                    lat[inst] = root_delay + base
            continue

        root_xy = None
        if root and root in design.instances:
            root_xy = (
                float(design.instances[root]["x"]),
                float(design.instances[root]["y"]),
            )

        level_by_net = {lvl.get("net"): lvl for lvl in levels if lvl.get("net")}
        for net_name, members in sinks.items():
            lvl = level_by_net.get(net_name)
            buf_delay = 0.08
            buf_xy = root_xy
            if lvl:
                bname = lvl.get("buffer")
                if bname and bname in design.cells:
                    ctype = design.cells[bname]["cell_type"]
                    cell = lib_cells.get(ctype, {})
                    d, _ = lookup_cell_delay(cell, "A", "X", 0.05, 0.05, "rise")
                    if d == 0.05:
                        d, _ = lookup_cell_delay(cell, "A", "Y", 0.05, 0.05, "rise")
                    buf_delay = d if d != 0.05 else 0.08
                if bname and bname in design.instances:
                    buf_xy = (
                        float(design.instances[bname]["x"]),
                        float(design.instances[bname]["y"]),
                    )
                elif lvl.get("xy"):
                    buf_xy = (float(lvl["xy"][0]), float(lvl["xy"][1]))

            wire_root = 0.0
            if root_xy and buf_xy:
                dist = abs(root_xy[0] - buf_xy[0]) + abs(root_xy[1] - buf_xy[1])
                wire_root = 0.69 * (r_per * dist) * (c_per * dist * 0.5) * 1e9

            for inst in members:
                if inst not in design.instances:
                    continue
                sx = float(design.instances[inst]["x"])
                sy = float(design.instances[inst]["y"])
                if buf_xy:
                    dist = abs(buf_xy[0] - sx) + abs(buf_xy[1] - sy)
                else:
                    dist = 0.0
                wire_sink = 0.69 * (r_per * dist) * (c_per * dist * 0.5) * 1e9
                lat[inst] = root_delay + wire_root + buf_delay + wire_sink
    return lat


def _lookup_delay_pair(
    cell: dict, related: str, out_pin: str, slew: float, load: float
) -> Tuple[float, float, float, float]:
    dr, sr = lookup_cell_delay(cell, related, out_pin, slew, load, "rise")
    df, sf = lookup_cell_delay(cell, related, out_pin, slew, load, "fall")
    return dr, df, sr, sf


def _unate_delays(
    cell_max: dict,
    cell_min: dict,
    related: str,
    out_pin: str,
    in_slew: float,
    load: float,
    in_sense: str,
    t_sense: str,
    ss_scale: float,
) -> Tuple[float, float, str, float]:
    """Return (d_max, d_min, out_sense, out_slew) using ss/max and ff/min NLDM."""
    drM, dfM, sr, sf = _lookup_delay_pair(cell_max, related, out_pin, in_slew, load)
    drN, dfN, _, _ = _lookup_delay_pair(cell_min, related, out_pin, in_slew, load)
    if ss_scale != 1.0 and cell_max is cell_min:
        drM *= ss_scale
        dfM *= ss_scale
    if t_sense == "negative_unate":
        if in_sense == "rise":
            d_max, s_out, out_s = dfM, "fall", sf
            d_min = drN
        else:
            d_max, s_out, out_s = drM, "rise", sr
            d_min = dfN
    else:
        if in_sense == "rise":
            d_max, s_out, out_s = drM, "rise", sr
            d_min = dfN
        else:
            d_max, s_out, out_s = dfM, "fall", sf
            d_min = drN
        if t_sense == "non_unate":
            if max(drM, dfM) > d_max:
                d_max = max(drM, dfM)
                s_out = "rise" if drM >= dfM else "fall"
                out_s = sr if s_out == "rise" else sf
            d_min = min(drN, dfN)
    return d_max, d_min, s_out, out_s


def _net_r_ohm(design: DesignObject, net: str) -> float:
    segs = design.routing.get(net, ()) or ()
    r_tot = 0.0
    prev = None
    layers = design.tech.get("layers", {})
    for seg in segs:
        r, _c, layer = _seg_rc(seg, design.tech)
        r_tot += r
        if prev is not None and layer != prev:
            r_tot += float(layers.get(layer, {}).get("via_r_ohm", 5.0))
        prev = layer
    return r_tot


def _find_launch_flop(
    pred: Dict[PinKey, PinKey],
    start: PinKey,
    lib_cells: Dict[str, Any],
    design: DesignObject,
) -> Optional[str]:
    cur: Optional[PinKey] = pred.get(start)
    hops = 0
    while cur is not None and hops < 64:
        ci, cp = cur
        if ci in design.cells:
            cinfo = lib_cells.get(design.cells[ci]["cell_type"], {})
            if cinfo.get("is_sequential") and str(cp).upper() in ("Q", "QN"):
                return ci
        cur = pred.get(cur)
        hops += 1
    return None


def run_sta(
    design: DesignObject, config: Dict[str, Any], clock_period_ns: float = 10.0
) -> Dict[str, Any]:
    sta_cfg = config.get("sta", {})
    setup_fb = float(sta_cfg.get("setup_ns", 0.05))
    hold_fb = float(sta_cfg.get("hold_ns", 0.02))
    sdc = load_sdc(config)
    uncertainty = float(
        sdc.get("uncertainty_ns")
        if sdc.get("uncertainty_ns") is not None
        else sta_cfg.get("uncertainty_ns", 0.05)
    )
    for clk in sdc.get("clocks") or []:
        if clk.get("period_ns"):
            clock_period_ns = float(clk["period_ns"])
            break
    corner = sta_cfg.get("corner", "ff")
    wire_model = str(sta_cfg.get("wire_model", "tree_elmore"))
    use_per_sink_cts = bool(sta_cfg.get("use_per_sink_cts", True))
    use_ceff = bool(sta_cfg.get("use_ceff", True))
    use_crpr = bool(sta_cfg.get("use_crpr", True))
    rc_min, rc_max = _rc_scales(config)
    ss_scale = _ss_delay_scale(design)
    lib_ff = _lib_cells(design, "ff")
    lib_ss = _lib_cells(design, "ss")
    lib_cells = design.library.get("cells", {}) or lib_ff

    at_max: Dict[PinKey, float] = {}
    at_min: Dict[PinKey, float] = {}
    slew: Dict[PinKey, float] = {}
    sense_at: Dict[PinKey, str] = {}  # rise/fall of signal at pin for max path
    pred: Dict[PinKey, PinKey] = {}  # predecessor for critical path

    sink_delay: Dict[str, Dict[PinKey, float]] = {}
    sink_slew_map: Dict[str, Dict[PinKey, float]] = {}
    use_tree = wire_model in ("tree_elmore", "d2m")
    if use_tree:
        loads, elmore, wire_slew, sink_delay, sink_slew_map = _tree_net_electricals(design)
    else:
        loads, elmore, wire_slew = _lumped_net_electricals(design)
    if wire_model == "d2m":
        elmore = {k: _d2m_delay(v) for k, v in elmore.items()}
        sink_delay = {
            n: {pk: _d2m_delay(d) for pk, d in sd.items()} for n, sd in sink_delay.items()
        }

    driver_nets = _driver_pin_nets(design)
    cts = _cts_timing(design, config)
    clk_lat = cts.get("lat") or {}
    clk_slew = cts.get("slew") or {}

    for pname, pinfo in design.ports.items():
        if pinfo.get("direction") == "input":
            key = (f"PORT:{pname}", "PAD")
            din = port_input_delay_ns(sdc, pname, 0.0)
            at_max[key] = din
            at_min[key] = din
            slew[key] = 0.05
            sense_at[key] = "rise"

    g = nx.DiGraph()
    for inst in design.cells:
        g.add_node(inst)
    for net, ninfo in design.nets.items():
        driver = ninfo.get("driver")
        if not driver:
            continue
        d_inst, d_pin = driver
        if str(d_inst).startswith("PORT:"):
            continue
        for s_inst, s_pin in ninfo.get("pins", []):
            if (s_inst, s_pin) == (d_inst, d_pin):
                continue
            if str(s_inst).startswith("PORT:"):
                continue
            if d_inst in design.cells and s_inst in design.cells:
                g.add_edge(d_inst, s_inst, net=net, d_pin=d_pin, s_pin=s_pin)

    for net, ninfo in design.nets.items():
        driver = ninfo.get("driver")
        if driver and str(driver[0]).startswith("PORT:"):
            for s_inst, s_pin in ninfo.get("pins", []):
                if str(s_inst).startswith("PORT:"):
                    continue
                key = (s_inst, s_pin)
                wd = sink_delay.get(net, {}).get(key, elmore.get(net, 0.0))
                ws = sink_slew_map.get(net, {}).get(key, wire_slew.get(net, 0.0))
                din = 0.0
                if str(s_inst).startswith("PORT:"):
                    din = 0.0
                at_max[key] = din + wd * rc_max
                at_min[key] = din + wd * rc_min
                slew[key] = 0.05 + ws
                sense_at[key] = "rise"
                pred[key] = (driver[0], driver[1])

    order, loops_broken = _topo_order(g, list(design.cells.keys()))

    lib_cells = design.library.get("cells", {}) or lib_ff
    cell_delay_at: Dict[PinKey, float] = {}
    net_delay_at: Dict[PinKey, float] = {}
    for inst in order:
        ctype = design.cells[inst]["cell_type"]
        cell = lib_cells.get(ctype, {})
        cell_max = lib_ss.get(ctype, cell)
        cell_min = lib_ff.get(ctype, cell)
        in_pins = _default_input_pins(cell)
        out_pins = _default_output_pins(cell) or ["Y", "X", "Q"]
        is_seq = cell.get("is_sequential", False)
        clk_pin = _clock_pin(cell)

        for op in out_pins:
            driven_net = driver_nets.get((inst, op))
            if driven_net is None:
                driven_net = design.cells[inst].get("pins", {}).get(op)
            load_raw = loads.get(driven_net, 0.0) if driven_net else 0.01
            r_net = _net_r_ohm(design, driven_net) if driven_net else 0.0
            in_slew_est = 0.05
            if is_seq and clk_pin:
                in_slew_est = float(clk_slew.get(inst, slew.get((inst, clk_pin), 0.05)))
            else:
                ins = _default_input_pins(cell)
                if ins:
                    in_slew_est = float(slew.get((inst, ins[0]), 0.05))
            if use_ceff and driven_net:
                c_pin = 0.0
                ninfo = design.nets.get(driven_net, {})
                dkey = tuple(ninfo.get("driver") or ())
                for s_inst, s_pin in ninfo.get("pins", []) or []:
                    if dkey and (s_inst, s_pin) == dkey:
                        continue
                    c_pin += _pin_cap(design, s_inst, s_pin)
                c_wire = max(0.0, load_raw - c_pin)
                load = _ceff_pf(c_pin, c_wire, r_net, in_slew_est)
            else:
                load = load_raw

            if is_seq and op.upper() in ("Q", "QN") and clk_pin:
                in_slew = float(clk_slew.get(inst, 0.05))
                drM, dfM, sr, sf = _lookup_delay_pair(cell_max, clk_pin, op, in_slew, load)
                drN, dfN, _, _ = _lookup_delay_pair(cell_min, clk_pin, op, in_slew, load)
                if drM == 0.05 and dfM == 0.05:
                    drM = dfM = 0.15 * ss_scale
                    sr = sf = in_slew
                elif ss_scale != 1.0 and cell_max is cell_min:
                    drM *= ss_scale
                    dfM *= ss_scale
                launch_late = float(clk_lat.get(inst, 0.0)) * rc_max
                launch_early = float(clk_lat.get(inst, 0.0)) * rc_min
                if drM >= dfM:
                    arr_max = launch_late + drM
                    out_slew = sr
                    path_sense_max = "rise"
                    cell_d = drM
                else:
                    arr_max = launch_late + dfM
                    out_slew = sf
                    path_sense_max = "fall"
                    cell_d = dfM
                arr_min = launch_early + min(drN, dfN)
                related = clk_pin
                cell_delay_at[(inst, op)] = cell_d
                pred[(inst, op)] = (inst, clk_pin)
            else:
                related_list = related_pins_for_output(cell, op)
                candidates = related_list or in_pins or ["A"]
                best_max = -1.0
                best_min = None
                best_slew = 0.05
                related = candidates[0]
                sense_max = "rise"
                best_pred: Optional[PinKey] = None
                best_cell_d = 0.0
                for rp in candidates:
                    key = (inst, rp)
                    in_at_max = at_max.get(key, 0.0)
                    in_at_min = at_min.get(key, in_at_max)
                    in_slew = slew.get(key, 0.05)
                    in_sense = sense_at.get(key, "rise")
                    t_sense = _timing_sense(cell, rp, op)
                    d_max, d_min, s_out, out_s = _unate_delays(
                        cell_max, cell_min, rp, op, in_slew, load, in_sense, t_sense, ss_scale
                    )
                    cand_max = in_at_max + d_max
                    cand_min = in_at_min + d_min
                    if cand_max >= best_max:
                        best_max = cand_max
                        best_slew = out_s
                        related = rp
                        sense_max = s_out
                        best_pred = key
                        best_cell_d = d_max
                    if best_min is None or cand_min < best_min:
                        best_min = cand_min
                arr_max = best_max if best_max >= 0 else 0.0
                arr_min = best_min if best_min is not None else arr_max
                out_slew = best_slew
                path_sense_max = sense_max
                cell_delay_at[(inst, op)] = best_cell_d
                if best_pred is not None:
                    pred[(inst, op)] = best_pred

            at_max[(inst, op)] = arr_max
            at_min[(inst, op)] = arr_min
            slew[(inst, op)] = out_slew
            sense_at[(inst, op)] = path_sense_max

            if driven_net and driven_net in design.nets:
                for s_inst, s_pin in design.nets[driven_net].get("pins", []):
                    if (s_inst, s_pin) == (inst, op):
                        continue
                    key = (s_inst, s_pin)
                    wd = sink_delay.get(driven_net, {}).get(key, elmore.get(driven_net, 0.0))
                    ws = sink_slew_map.get(driven_net, {}).get(
                        key, wire_slew.get(driven_net, 0.0)
                    )
                    wd_max = wd * rc_max
                    wd_min = wd * rc_min
                    sink_max = arr_max + wd_max
                    sink_min = arr_min + wd_min
                    sink_slew = out_slew + ws
                    if sink_max > at_max.get(key, -1.0):
                        at_max[key] = sink_max
                        slew[key] = sink_slew
                        sense_at[key] = path_sense_max
                        pred[key] = (inst, op)
                        net_delay_at[key] = wd_max
                    if key not in at_min or sink_min < at_min[key]:
                        at_min[key] = sink_min

    for pname, pinfo in design.ports.items():
        if pinfo.get("direction") != "output":
            continue
        net = pinfo.get("net") or pname
        ninfo = design.nets.get(net, {})
        driver = ninfo.get("driver")
        if driver:
            dkey = (driver[0], driver[1])
            pkey = (f"PORT:{pname}", "PAD")
            wd = sink_delay.get(net, {}).get(pkey, elmore.get(net, 0.0))
            at_max[pkey] = at_max.get(dkey, 0.0) + wd * rc_max
            at_min[pkey] = at_min.get(dkey, 0.0) + wd * rc_min
            sense_at[pkey] = sense_at.get(dkey, "rise")
            pred[pkey] = dkey
            net_delay_at[pkey] = wd * rc_max

    setup_eps: List[dict] = []
    hold_eps: List[dict] = []

    lat_vals = list(clk_lat.values())
    if lat_vals:
        cts_summary = {
            "min_ns": float(min(lat_vals)),
            "max_ns": float(max(lat_vals)),
            "mean_ns": float(sum(lat_vals) / len(lat_vals)),
            "skew_ns": float(max(lat_vals) - min(lat_vals)),
        }
    else:
        cts_summary = {"min_ns": 0.0, "max_ns": 0.0, "mean_ns": 0.0, "skew_ns": 0.0}

    def _trace_path_detail(endpoint: PinKey) -> List[Dict[str, Any]]:
        pins: List[PinKey] = []
        cur: Optional[PinKey] = endpoint
        seen: set = set()
        while cur is not None and cur not in seen and len(pins) < 64:
            seen.add(cur)
            pins.append(cur)
            cur = pred.get(cur)
        pins.reverse()
        rows: List[Dict[str, Any]] = []
        for pk in pins:
            rows.append(
                {
                    "pin": f"{pk[0]}/{pk[1]}",
                    "arrival_ns": float(at_max.get(pk, 0.0)),
                    "slew_ns": float(slew.get(pk, 0.0)),
                    "cell_delay_ns": float(cell_delay_at.get(pk, 0.0)),
                    "net_delay_ns": float(net_delay_at.get(pk, 0.0)),
                    "sense": sense_at.get(pk, "rise"),
                }
            )
        return rows

    def _record_check(
        check: str,
        endpoint: str,
        pkey: PinKey,
        launch: Optional[str],
        capture: Optional[str],
        arrival: float,
        required: float,
        slack: float,
        launch_lat: float,
        capture_lat: float,
        path_sense: str,
        crpr: float,
        constraint_ns: float,
    ) -> None:
        row = {
            "check": check,
            "endpoint": endpoint,
            "arrival_ns": arrival,
            "required_ns": required,
            "slack_ns": slack,
            "launch_latency_ns": launch_lat,
            "capture_latency_ns": capture_lat,
            "path_sense": path_sense,
            "crpr_ns": crpr,
            "constraint_ns": constraint_ns,
            "slew_ns": float(slew.get(pkey, 0.05)),
        }
        if check in ("setup", "recovery"):
            setup_eps.append(row)
        else:
            hold_eps.append(row)

    def _seq_checks(
        inst: str,
        data_pin: str,
        clk_pin: str,
        cell: dict,
        setup_kind: str,
        hold_kind: str,
        setup_default: float,
        hold_default: float,
    ) -> None:
        pkey = (inst, data_pin)
        capture_lat = float(clk_lat.get(inst, 0.0))
        d_slew = slew.get(pkey, 0.05)
        c_slew = float(clk_slew.get(inst, 0.05))
        path_sense = sense_at.get(pkey, "rise")
        setup_ns = lookup_setup_hold(cell, data_pin, clk_pin, d_slew, c_slew, setup_kind)
        hold_ns = lookup_setup_hold(cell, data_pin, clk_pin, d_slew, c_slew, hold_kind)
        if setup_ns is None:
            setup_ns = setup_default
        if hold_ns is None:
            hold_ns = hold_default

        launch = _find_launch_flop(pred, pkey, lib_cells, design) if use_per_sink_cts else inst
        if launch is None:
            launch = inst
        launch_lat = float(clk_lat.get(launch, cts_summary["mean_ns"]))
        if is_false_path(sdc, launch, inst):
            return
        crpr = _crpr_ns(launch, inst, cts, rc_min, rc_max) if use_crpr else 0.0
        mc_setup = multicycle_offset(sdc, launch, inst, "setup", clock_period_ns)
        mc_hold = multicycle_offset(sdc, launch, inst, "hold", clock_period_ns)
        capture_early = capture_lat * rc_min
        capture_late = capture_lat * rc_max
        arrival_max = at_max.get(pkey, 0.0)
        arrival_min = at_min.get(pkey, arrival_max)
        req_setup = clock_period_ns + capture_early - setup_ns - uncertainty + crpr + mc_setup
        req_hold = capture_late + hold_ns - crpr - mc_hold
        _record_check(
            setup_kind,
            f"{inst}/{data_pin}",
            pkey,
            launch,
            inst,
            arrival_max,
            req_setup,
            req_setup - arrival_max,
            launch_lat,
            capture_lat,
            path_sense,
            crpr,
            setup_ns,
        )
        _record_check(
            hold_kind,
            f"{inst}/{data_pin}",
            pkey,
            launch,
            inst,
            arrival_min,
            req_hold,
            arrival_min - req_hold,
            launch_lat,
            capture_lat,
            path_sense,
            crpr,
            hold_ns,
        )

    for inst, info in design.cells.items():
        cell = lib_cells.get(info["cell_type"], {})
        if not cell.get("is_sequential"):
            continue
        clk_pin = _clock_pin(cell) or "CLK"
        data_pin = "D" if "D" in cell.get("pins", {}) else None
        if data_pin is not None:
            _seq_checks(inst, data_pin, clk_pin, cell, "setup", "hold", setup_fb, hold_fb)
        for apin in async_constraint_pins(cell):
            if apin == data_pin:
                continue
            _seq_checks(inst, apin, clk_pin, cell, "recovery", "removal", setup_fb, hold_fb)

    for pname, pinfo in design.ports.items():
        if pinfo.get("direction") != "output":
            continue
        key = (f"PORT:{pname}", "PAD")
        if is_false_path(sdc, "*", pname) or is_false_path(sdc, "*", f"PORT:{pname}"):
            continue
        arrival_max = at_max.get(key, 0.0)
        arrival_min = at_min.get(key, arrival_max)
        od = port_output_delay_ns(sdc, pname, 0.0)
        launch = _find_launch_flop(pred, key, lib_cells, design)
        launch_lat = float(clk_lat.get(launch or "", 0.0))
        crpr = 0.0
        mc_setup = multicycle_offset(sdc, launch or "", pname, "setup", clock_period_ns)
        mc_hold = multicycle_offset(sdc, launch or "", pname, "hold", clock_period_ns)
        req_setup = clock_period_ns - od - setup_fb - uncertainty + mc_setup
        req_hold = od + hold_fb - mc_hold
        _record_check(
            "setup",
            f"PORT:{pname}/PAD",
            key,
            launch,
            None,
            arrival_max,
            req_setup,
            req_setup - arrival_max,
            launch_lat,
            0.0,
            sense_at.get(key, "max"),
            crpr,
            setup_fb,
        )
        _record_check(
            "hold",
            f"PORT:{pname}/PAD",
            key,
            launch,
            None,
            arrival_min,
            req_hold,
            arrival_min - req_hold,
            launch_lat,
            0.0,
            "min",
            crpr,
            hold_fb,
        )

    if not setup_eps:
        max_at = max(at_max.values()) if at_max else 0.0
        min_at = min(at_min.values()) if at_min else 0.0
        setup_eps.append(
            {
                "check": "setup",
                "endpoint": "max_arrival",
                "arrival_ns": max_at,
                "required_ns": clock_period_ns - setup_fb - uncertainty,
                "slack_ns": clock_period_ns - setup_fb - uncertainty - max_at,
                "launch_latency_ns": 0.0,
                "capture_latency_ns": 0.0,
                "path_sense": "max",
                "crpr_ns": 0.0,
            }
        )
        hold_eps.append(
            {
                "check": "hold",
                "endpoint": "min_arrival",
                "arrival_ns": min_at,
                "required_ns": hold_fb,
                "slack_ns": min_at - hold_fb,
                "launch_latency_ns": 0.0,
                "capture_latency_ns": 0.0,
                "path_sense": "min",
                "crpr_ns": 0.0,
            }
        )

    setup_eps.sort(key=lambda s: s["slack_ns"])
    hold_eps.sort(key=lambda s: s["slack_ns"])
    setup_wns = min(s["slack_ns"] for s in setup_eps)
    setup_tns = sum(s["slack_ns"] for s in setup_eps if s["slack_ns"] < 0)
    hold_wns = min(s["slack_ns"] for s in hold_eps)
    hold_tns = sum(s["slack_ns"] for s in hold_eps if s["slack_ns"] < 0)

    critical_path: Dict[str, Any] = {}
    if setup_eps:
        worst = setup_eps[0]
        ep = worst["endpoint"]
        if "/" in ep and not ep.startswith("max_"):
            inst, pin = ep.rsplit("/", 1)
            if ep.startswith("PORT:"):
                parts = ep.split("/")
                pkey = (parts[0], parts[1] if len(parts) > 1 else "PAD")
            else:
                pkey = (inst, pin)
            detail = _trace_path_detail(pkey)
            critical_path = {
                "endpoint": ep,
                "slack_ns": worst["slack_ns"],
                "arrival_ns": worst["arrival_ns"],
                "pins": [r["pin"] for r in detail],
                "stages": detail,
                "cell_delay_ns": sum(r["cell_delay_ns"] for r in detail),
                "net_delay_ns": sum(r["net_delay_ns"] for r in detail),
                "crpr_ns": float(worst.get("crpr_ns") or 0.0),
                "path_sense": worst.get("path_sense"),
            }

    corners_loaded = sorted(
        k for k, v in (design.library.get("corners") or {}).items() if v
    )
    endpoints = setup_eps[:25] + hold_eps[:25]
    return {
        "corner": corner,
        "corners_loaded": corners_loaded,
        "clock_period_ns": clock_period_ns,
        "setup_ns": setup_fb,
        "hold_ns": hold_fb,
        "uncertainty_ns": uncertainty,
        "wire_model": wire_model,
        "rc_min_scale": rc_min,
        "rc_max_scale": rc_max,
        "loops_broken": loops_broken,
        "use_crpr": use_crpr,
        "use_ceff": use_ceff,
        "sdc": {
            "clocks": len(sdc.get("clocks") or []),
            "false_paths": len(sdc.get("false_paths") or []),
            "multicycle": len(sdc.get("multicycle") or []),
            "input_delays": len(sdc.get("input_delays") or []),
            "output_delays": len(sdc.get("output_delays") or []),
        },
        "cts_latency": cts_summary,
        "critical_path": critical_path,
        "setup_wns_ps": setup_wns * 1000.0,
        "setup_tns_ps": setup_tns * 1000.0,
        "hold_wns_ps": hold_wns * 1000.0,
        "hold_tns_ps": hold_tns * 1000.0,
        "wns_ps": setup_wns * 1000.0,
        "tns_ps": setup_tns * 1000.0,
        "endpoints": endpoints,
        "summary": {
            "setup_endpoints": len(setup_eps),
            "hold_endpoints": len(hold_eps),
            "setup_failing": sum(1 for s in setup_eps if s["slack_ns"] < 0),
            "hold_failing": sum(1 for s in hold_eps if s["slack_ns"] < 0),
        },
        "num_at_points": len(at_max),
    }
