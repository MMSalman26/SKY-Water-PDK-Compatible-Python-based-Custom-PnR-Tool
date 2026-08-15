"""Static IR drop (PDNSim-inspired): MNA on planned VDD/VSS PDN + synthetic fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import lsmr, spsolve
import warnings

from pnr_tool.design.object import DesignObject

NodeXY = List[Tuple[float, float]]
Edge = Tuple[int, int, float]
EdgeMeta = Tuple[int, int, float, float, str, str]  # a, b, g, width_um, layer, kind

_CORNER_VDD = {"ff": 1.95, "tt": 1.8, "ss": 1.60}
_LAYER_RANK = {
    "poly": 0,
    "li1": 1,
    "met1": 2,
    "met2": 3,
    "met3": 4,
    "met4": 5,
    "met5": 6,
}
_J_HIST_EDGES = (0.0, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, float("inf"))


def run_ir_drop(
    design: DesignObject,
    config: Dict[str, Any],
    clock_period_ns: float = 10.0,
) -> Dict[str, Any]:
    if design.power_grid and design.power_grid.get("segments"):
        result = _ir_from_power_grid(design, config, clock_period_ns)
        if result is not None:
            return result
    out = _ir_synthetic_mesh(design, config, clock_period_ns)
    out["ir_mode"] = "synthetic_fallback"
    out["note"] = (
        "No usable power_grid — synthetic mesh IR (not PDN-based); "
        "static DC MNA, PDNSim-inspired"
    )
    return out


def _rail_vdd(design: DesignObject, config: Dict[str, Any]) -> float:
    """Nominal rail voltage: SkyWater liberty corners, else ``tech.vdd``."""
    ir_cfg = config.get("ir_drop", {}) or {}
    corner = str(ir_cfg.get("corner", "tt") or "tt").lower()
    if corner in _CORNER_VDD:
        return float(_CORNER_VDD[corner])
    return float((design.tech or {}).get("vdd", 1.8))


def _r_temp_scale(config: Dict[str, Any]) -> float:
    """``1 + tc*(T-25)``. ``temperature_c: null`` → 100°C for ss/ff else 25°C."""
    ir_cfg = config.get("ir_drop", {}) or {}
    tc = float(ir_cfg.get("r_tempco", 0.0039) or 0.0)
    t = ir_cfg.get("temperature_c", None)
    if t is None:
        corner = str(ir_cfg.get("corner", "tt") or "tt").lower()
        t = 100.0 if corner in ("ss", "ff") else 25.0
    return 1.0 + tc * (float(t) - 25.0)


def _layer_rank(name: str) -> int:
    n = str(name).lower()
    if n in _LAYER_RANK:
        return _LAYER_RANK[n]
    digits = "".join(ch for ch in n if ch.isdigit())
    return int(digits) + 10 if digits else 99


def _lower_layer(a: str, b: str) -> str:
    return a if _layer_rank(a) <= _layer_rank(b) else b


def _via_resistance(la: str, lb: str, layers: dict, fallback: float) -> float:
    """Via R at an H/V crossing: lower layer's ``via_r_ohm``, else scalar fallback."""
    lower = _lower_layer(la, lb)
    vr = (layers.get(lower) or {}).get("via_r_ohm")
    if vr is None:
        vr = (layers.get(la) or {}).get("via_r_ohm")
    if vr is None:
        vr = (layers.get(lb) or {}).get("via_r_ohm")
    if vr is None:
        vr = fallback
    return max(float(vr), 1e-12)


def _instance_currents(
    design: DesignObject,
    config: Dict[str, Any],
    vdd: float,
    clock_period_ns: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Dict[str, float]]:
    """Deterministic per-instance currents: I = (P_leak + α C V² f) / VDD."""
    ir_cfg = config.get("ir_drop", {})
    activity = float(ir_cfg.get("activity_factor", 0.1))
    leak_unit = float(ir_cfg.get("leakage_unit_w", 1e-9))
    use_int = bool(ir_cfg.get("use_internal_power", True))
    freq = 1e9 / max(float(clock_period_ns), 1e-9)
    lib_cells = design.library.get("cells", {})
    names = list(design.instances.keys())
    n = len(names)
    xs = np.zeros(n)
    ys = np.zeros(n)
    currents = np.zeros(n)
    empty = {
        "total_a": 0.0,
        "avg_cell_a": 0.0,
        "activity_factor": activity,
        "clock_period_ns": float(clock_period_ns),
        "model": "leakage_plus_alpha_cv2f",
        "internal_power_used": False,
    }
    if n == 0:
        return xs, ys, currents, empty

    internal_used = False
    for i, name in enumerate(names):
        inst = design.instances[name]
        w = float(inst.get("width", 0.0) or 0.0)
        h = float(inst.get("height", 0.0) or 0.0)
        ctype = design.cells.get(name, {}).get("cell_type", "")
        lib = lib_cells.get(ctype, {})
        if w <= 0:
            w = float(lib.get("width", 1.0) or 1.0)
        if h <= 0:
            h = float(lib.get("height", 2.72) or 2.72)
        # Prefer VPWR/VGND pin center when LEF rects exist; else cell centroid
        inj = _power_pin_xy(lib, float(inst["x"]), float(inst["y"]), w, h)
        xs[i], ys[i] = inj
        leak_raw = abs(float(lib.get("leakage_power", 0.0) or 0.0))
        p_leak = leak_raw * leak_unit
        physical = bool(inst.get("physical") or design.cells.get(name, {}).get("physical"))
        if physical:
            p_dyn = 0.0
        else:
            c_sw_pf = _switching_cap_pf(design, name, lib)
            c_f = c_sw_pf * 1e-12
            p_dyn = activity * c_f * (vdd**2) * freq
            p_int = 0.0
            if use_int:
                ip_nw = lib.get("internal_power_nw")
                ip = lib.get("internal_power")
                if isinstance(ip_nw, (int, float)):
                    p_int = abs(float(ip_nw)) * leak_unit
                    internal_used = True
                elif isinstance(ip, (int, float)):
                    p_int = abs(float(ip))
                    internal_used = True
            p_dyn += p_int
        currents[i] = (p_leak + p_dyn) / max(vdd, 1e-9)

    total = float(np.sum(currents))
    return xs, ys, currents, {
        "total_a": total,
        "avg_cell_a": total / n,
        "activity_factor": activity,
        "clock_period_ns": float(clock_period_ns),
        "model": "leakage_plus_alpha_cv2f",
        "internal_power_used": internal_used,
    }


def _power_pin_xy(
    lib: dict, ox: float, oy: float, w: float, h: float
) -> Tuple[float, float]:
    """Injection site: VPWR/VGND pin center if present, else cell centroid."""
    for pname in ("VPWR", "VPB", "VDD"):
        pinfo = lib.get("pins", {}).get(pname, {})
        rects = pinfo.get("rects") or []
        if rects:
            r = rects[0]
            try:
                return (
                    ox + 0.5 * (float(r["x1"]) + float(r["x2"])),
                    oy + 0.5 * (float(r["y1"]) + float(r["y2"])),
                )
            except (KeyError, TypeError, ValueError):
                pass
    return (ox + 0.5 * w, oy + 0.5 * h)


def _switching_cap_pf(design: DesignObject, inst_name: str, lib: dict) -> float:
    """Estimate switched capacitance from output pin + fanout pin caps (pF)."""
    cell_pins = design.cells.get(inst_name, {}).get("pins", {})
    lib_pins = lib.get("pins", {})
    c_out = 0.0
    for pname, pinfo in lib_pins.items():
        if pinfo.get("direction") != "output":
            continue
        c_out += float(pinfo.get("capacitance", 0.0) or 0.0)
        net = cell_pins.get(pname)
        if not net or net not in design.nets:
            continue
        driver = design.nets[net].get("driver")
        dkey = tuple(driver) if driver else None
        for sink_inst, sink_pin in design.nets[net].get("pins", []):
            if dkey is not None and (sink_inst, sink_pin) == dkey:
                continue
            if str(sink_inst).startswith("PORT:"):
                c_out += 0.005
                continue
            sink_type = design.cells.get(sink_inst, {}).get("cell_type", "")
            sink_lib = design.library.get("cells", {}).get(sink_type, {})
            c_out += float(
                sink_lib.get("pins", {}).get(sink_pin, {}).get("capacitance", 0.002) or 0.002
            )
    return max(c_out, 0.001)


def _seg_resistance(
    length_um: float,
    layer: str,
    width_um: float,
    layers: dict,
    width_ref: float,
    temp_scale: float = 1.0,
) -> float:
    r_per = float(layers.get(layer, {}).get("r_per_um", 0.05))
    w = max(float(width_um), 1e-6)
    r = (r_per * max(length_um, 1e-9)) * (width_ref / w)
    return r * max(float(temp_scale), 1e-12)


def _segment_axis(seg: dict) -> Optional[Tuple[str, float, float, float, str]]:
    """Return (orient, const, lo, hi, layer) for axis-aligned segment, else None."""
    try:
        x1, y1 = float(seg["x1"]), float(seg["y1"])
        x2, y2 = float(seg["x2"]), float(seg["y2"])
        layer = str(seg.get("layer", "met5"))
    except (KeyError, TypeError, ValueError):
        return None
    if abs(y2 - y1) < 1e-9 and abs(x2 - x1) >= 1e-9:
        return ("h", y1, min(x1, x2), max(x1, x2), layer)
    if abs(x2 - x1) < 1e-9 and abs(y2 - y1) >= 1e-9:
        return ("v", x1, min(y1, y2), max(y1, y2), layer)
    return None


def _build_rail_graph(
    segs: Sequence[dict],
    design: DesignObject,
    config: Dict[str, Any],
    sample: float,
) -> Tuple[NodeXY, List[Edge], List[int], int]:
    """Discretize PDN segments into layer-keyed nodes/edges; return via edge count.

    Cross-layer crossings insert a via resistor (lower-layer ``via_r_ohm``, else
    ``ir_drop.via_r_ohm``) between layer-local nodes at the same XY.
    Follow-pin segments stay in the graph so they via to straps at H/V crossings.
    """
    g = _build_rail_graph_ex(segs, design, config, sample)
    return g["node_xy"], g["edges"], g["ring_nodes"], g["via_n"]


def _build_rail_graph_ex(
    segs: Sequence[dict],
    design: DesignObject,
    config: Dict[str, Any],
    sample: float,
    temp_scale: Optional[float] = None,
) -> Dict[str, Any]:
    """Full rail graph: nodes, edges, roles, layers, and edge metadata."""
    pcfg = config.get("power", {})
    ir_cfg = config.get("ir_drop", {})
    width_ref = float(
        (design.power_grid or {}).get("width_ref_um", pcfg.get("width_ref_um", 0.48))
    )
    via_r_fallback = float(ir_cfg.get("via_r_ohm", 5.0))
    layers = design.tech.get("layers", {})
    if temp_scale is None:
        temp_scale = _r_temp_scale(config)

    axes = [(i, _segment_axis(s)) for i, s in enumerate(segs)]
    horiz = [(i, a) for i, a in axes if a and a[0] == "h"]
    vert = [(i, a) for i, a in axes if a and a[0] == "v"]
    extras: Dict[int, List[Tuple[float, float]]] = {i: [] for i in range(len(segs))}
    crossings: List[Tuple[float, float, str, str]] = []
    for hi, h in horiz:
        _o, y, xlo, xhi, hlayer = h  # type: ignore[misc]
        for vi, v in vert:
            _o2, x, ylo, yhi, vlayer = v  # type: ignore[misc]
            if hlayer == vlayer:
                continue
            if xlo - 1e-9 <= x <= xhi + 1e-9 and ylo - 1e-9 <= y <= yhi + 1e-9:
                extras[hi].append((x, y))
                extras[vi].append((x, y))
                crossings.append((x, y, hlayer, vlayer))

    node_xy: NodeXY = []
    node_layers: List[str] = []
    node_index: Dict[Tuple[int, int, str], int] = {}

    def nid(x: float, y: float, layer: str) -> int:
        key = (int(round(x / sample)), int(round(y / sample)), layer)
        if key in node_index:
            return node_index[key]
        idx = len(node_xy)
        node_index[key] = idx
        node_xy.append((key[0] * sample, key[1] * sample))
        node_layers.append(layer)
        return idx

    edges: List[Edge] = []
    edge_meta: List[EdgeMeta] = []
    ring_nodes: List[int] = []
    strap_nodes: List[int] = []
    follow_pin_nodes: List[int] = []

    for si, seg in enumerate(segs):
        try:
            x1, y1 = float(seg["x1"]), float(seg["y1"])
            x2, y2 = float(seg["x2"]), float(seg["y2"])
            layer = str(seg.get("layer", "met5"))
            width = float(seg.get("width_um", width_ref))
        except (KeyError, TypeError, ValueError):
            continue
        length = abs(x2 - x1) + abs(y2 - y1)
        n_samp = max(1, int(length / max(sample, 1e-9)))
        raw_pts: List[Tuple[float, float]] = []
        for i in range(n_samp + 1):
            t = i / n_samp
            raw_pts.append((x1 + t * (x2 - x1), y1 + t * (y2 - y1)))
        for px, py in extras.get(si, []):
            raw_pts.append((px, py))
        if abs(x2 - x1) >= abs(y2 - y1):
            raw_pts.sort(key=lambda p: p[0])
        else:
            raw_pts.sort(key=lambda p: p[1])

        pts = [nid(px, py, layer) for px, py in raw_pts]
        compact: List[int] = []
        for p in pts:
            if not compact or compact[-1] != p:
                compact.append(p)
        pts = compact
        role = str(seg.get("role", "") or "")
        if role == "ring":
            ring_nodes.extend(pts)
        elif role == "strap":
            strap_nodes.extend(pts)
        elif role == "follow_pin":
            follow_pin_nodes.extend(pts)
        for a, b in zip(pts, pts[1:]):
            if a == b:
                continue
            ax, ay = node_xy[a]
            bx, by = node_xy[b]
            seg_len = max(abs(bx - ax) + abs(by - ay), sample * 0.25)
            r = _seg_resistance(seg_len, layer, width, layers, width_ref, temp_scale)
            g = 1.0 / max(r, 1e-12)
            edges.append((a, b, g))
            edge_meta.append((a, b, g, width, layer, "metal"))

    via_edges = 0
    seen_via: set = set()
    for x, y, la, lb in crossings:
        a = nid(x, y, la)
        b = nid(x, y, lb)
        if a == b:
            continue
        key = (min(a, b), max(a, b))
        if key in seen_via:
            continue
        seen_via.add(key)
        r_via = _via_resistance(la, lb, layers, via_r_fallback)
        g_via = 1.0 / r_via
        edges.append((a, b, g_via))
        edge_meta.append((a, b, g_via, 0.0, f"{la}|{lb}", "via"))
        via_edges += 1

    return {
        "node_xy": node_xy,
        "node_layers": node_layers,
        "edges": edges,
        "edge_meta": edge_meta,
        "ring_nodes": list(dict.fromkeys(ring_nodes)),
        "strap_nodes": list(dict.fromkeys(strap_nodes)),
        "follow_pin_nodes": list(dict.fromkeys(follow_pin_nodes)),
        "via_n": via_edges,
    }


def _bump_nodes(
    node_xy: NodeXY,
    node_layers: Sequence[str],
    top_layer: str,
    die: Sequence[float],
    bump_n: int,
) -> List[int]:
    """N×N C4-style sites → nearest top-layer graph nodes."""
    n = max(1, int(bump_n))
    minx, miny, maxx, maxy = (float(v) for v in die[:4])
    pad_x = 0.1 * (maxx - minx) if maxx > minx else 0.0
    pad_y = 0.1 * (maxy - miny) if maxy > miny else 0.0
    xs = np.linspace(minx + pad_x, maxx - pad_x, n) if n > 1 else np.array([(minx + maxx) * 0.5])
    ys = np.linspace(miny + pad_y, maxy - pad_y, n) if n > 1 else np.array([(miny + maxy) * 0.5])
    top = str(top_layer)
    top_idx = [i for i, ly in enumerate(node_layers) if str(ly) == top]
    if not top_idx:
        top_idx = list(range(len(node_xy)))
    if not top_idx:
        return []
    coords = np.asarray([node_xy[i] for i in top_idx], dtype=float)
    picked: List[int] = []
    seen = set()
    for y in ys:
        for x in xs:
            d2 = (coords[:, 0] - float(x)) ** 2 + (coords[:, 1] - float(y)) ** 2
            nid = int(top_idx[int(np.argmin(d2))])
            if nid not in seen:
                seen.add(nid)
                picked.append(nid)
    return picked


def _requested_supply_nodes(
    graph: Dict[str, Any],
    source_type: str,
    design: DesignObject,
    config: Dict[str, Any],
) -> List[int]:
    st = str(source_type or "straps").lower()
    if st == "bumps":
        ir_cfg = config.get("ir_drop", {}) or {}
        pcfg = config.get("power", {}) or {}
        top = str(
            (design.power_grid or {}).get("vdd_layer")
            or pcfg.get("vdd_layer", "met5")
        )
        return _bump_nodes(
            graph["node_xy"],
            graph.get("node_layers") or [],
            top,
            design.die_area,
            int(ir_cfg.get("bump_n", 4) or 4),
        )
    if st == "ring":
        return list(graph.get("ring_nodes") or [])
    return list(graph.get("strap_nodes") or [])


def _resolve_supplies(graph: Dict[str, Any], requested: Sequence[int]) -> List[int]:
    """Prefer requested sources; fall back to ring, then any node."""
    n = len(graph.get("node_xy") or [])
    edges = graph.get("edges") or []
    req = [int(s) for s in requested if 0 <= int(s) < n]
    if req:
        floating = set(_floating_nodes(n, edges, req))
        ok = [s for s in req if s not in floating]
        if ok:
            return ok
    ring = [int(s) for s in (graph.get("ring_nodes") or []) if 0 <= int(s) < n]
    if ring:
        floating = set(_floating_nodes(n, edges, ring))
        ok = [s for s in ring if s not in floating]
        if ok:
            return ok
    return [0] if n else []


def _floating_nodes(
    n: int, edges: Sequence[Edge], supplies: Sequence[int]
) -> List[int]:
    if n == 0:
        return []
    if not edges:
        return list(range(n))
    a = np.array([e[0] for e in edges], dtype=np.int64)
    b = np.array([e[1] for e in edges], dtype=np.int64)
    adj = sparse.coo_matrix(
        (np.ones(a.size), (a, b)), shape=(n, n)
    ).tocsr()
    # symmetrize
    adj = adj + adj.T
    _nc, labels = connected_components(adj, directed=False)
    if not supplies:
        return list(range(n))
    supply_labels = set(int(labels[s]) for s in supplies if 0 <= s < n)
    return [i for i in range(n) if int(labels[i]) not in supply_labels]


def _nearest_node_voltages(
    xs: np.ndarray,
    ys: np.ndarray,
    node_xy: NodeXY,
    V: np.ndarray,
    connected: Sequence[int],
    node_layers: Optional[Sequence[str]] = None,
    prefer_layer: Optional[str] = None,
) -> np.ndarray:
    """Sample rail voltage at each (x,y) from the nearest connected grid node."""
    n = int(xs.size)
    out = np.full(n, np.nan, dtype=float)
    if n == 0 or not connected or V is None or len(V) == 0:
        return out
    pool = list(connected)
    if prefer_layer and node_layers:
        subset = [i for i in pool if i < len(node_layers) and str(node_layers[i]) == str(prefer_layer)]
        if subset:
            pool = subset
    coords = np.asarray([node_xy[i] for i in pool], dtype=float)
    conn_idx = np.asarray(pool, dtype=np.int64)
    for i, (x, y) in enumerate(zip(xs, ys)):
        d2 = (coords[:, 0] - float(x)) ** 2 + (coords[:, 1] - float(y)) ** 2
        nearest = int(conn_idx[int(np.argmin(d2))])
        vv = float(V[nearest])
        out[i] = vv if np.isfinite(vv) else np.nan
    return out


def _instance_ir_drops(
    names: Sequence[str],
    voltages: np.ndarray,
    *,
    vdd: float,
    threshold: float,
    rail: str,
) -> Tuple[List[Dict[str, Any]], int]:
    """Instances with local VDD below threshold, sorted by drop % (desc)."""
    rows: List[Dict[str, Any]] = []
    for name, v in zip(names, voltages):
        if not np.isfinite(v):
            continue
        # Physical local VDD is never below ground
        vv = float(np.clip(float(v), 0.0, float(vdd)))
        if vv >= threshold - 1e-12:
            continue
        drop_v = max(0.0, float(vdd) - vv)
        drop_pct = 100.0 * drop_v / max(float(vdd), 1e-12)
        rows.append(
            {
                "type": "voltage",
                "instance": str(name),
                "rail": rail,
                "voltage": vv,
                "drop_v": drop_v,
                "drop_pct": drop_pct,
            }
        )
    rows.sort(key=lambda r: (-float(r["drop_pct"]), str(r["instance"])))
    return rows, len(rows)


def _instance_heatmap(
    names: Sequence[str],
    xs: np.ndarray,
    ys: np.ndarray,
    voltages: np.ndarray,
    vdd: float,
    cap: int = 500,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name, x, y, v in zip(names, xs, ys, voltages):
        if not np.isfinite(v):
            continue
        vv = float(np.clip(float(v), 0.0, float(vdd)))
        drop_v = max(0.0, float(vdd) - vv)
        drop_pct = 100.0 * drop_v / max(float(vdd), 1e-12)
        rows.append(
            {
                "instance": str(name),
                "x": float(x),
                "y": float(y),
                "v": vv,
                "drop_pct": drop_pct,
            }
        )
    rows.sort(key=lambda r: (-float(r["drop_pct"]), str(r["instance"])))
    return rows[: max(1, int(cap))]


def _worst_nodes_by_layer(
    node_xy: NodeXY,
    node_layers: Sequence[str],
    V: np.ndarray,
    connected: Sequence[int],
    is_ground: bool,
) -> Dict[str, Dict[str, Any]]:
    worst: Dict[str, Dict[str, Any]] = {}
    for i in connected:
        if i >= len(V) or not np.isfinite(V[i]):
            continue
        ly = str(node_layers[i]) if i < len(node_layers) else "?"
        v = float(V[i])
        prev = worst.get(ly)
        if prev is None:
            better = True
        elif is_ground:
            better = v > float(prev["v"])
        else:
            better = v < float(prev["v"])
        if better:
            worst[ly] = {
                "layer": ly,
                "x": float(node_xy[i][0]),
                "y": float(node_xy[i][1]),
                "v": v,
            }
    return worst


def _current_density(
    edge_meta: Optional[Sequence[EdgeMeta]],
    V: np.ndarray,
    j_max: float,
) -> Dict[str, Any]:
    js: List[float] = []
    viol = 0
    max_j = 0.0
    worst: Optional[Dict[str, Any]] = None
    if edge_meta:
        for a, b, g, width, layer, kind in edge_meta:
            if str(kind) != "metal":
                continue
            if a >= len(V) or b >= len(V):
                continue
            va, vb = V[int(a)], V[int(b)]
            if not (np.isfinite(va) and np.isfinite(vb)):
                continue
            i_amp = float(g) * (float(va) - float(vb))
            w = max(float(width), 1e-12)
            j = abs(i_amp) * 1e3 / w  # mA/µm
            js.append(j)
            if j > max_j:
                max_j = j
                worst = {"layer": str(layer), "j_ma_per_um": j}
            if j > float(j_max):
                viol += 1
    arr = np.asarray(js, dtype=float) if js else np.zeros(0)
    hist: List[Dict[str, Any]] = []
    for lo, hi in zip(_J_HIST_EDGES, _J_HIST_EDGES[1:]):
        if arr.size:
            if np.isinf(hi):
                cnt = int(np.sum(arr >= lo))
            else:
                cnt = int(np.sum((arr >= lo) & (arr < hi)))
        else:
            cnt = 0
        hist.append({"lo": lo, "hi": None if np.isinf(hi) else hi, "count": cnt})
    return {
        "max_j_ma_per_um": float(max_j),
        "j_violations": int(viol),
        "j_max_limit": float(j_max),
        "j_histogram": hist,
        "worst_segment": worst,
        "n_metal_segments": len(js),
    }


def _tap_injections(
    xs: np.ndarray,
    ys: np.ndarray,
    cell_i: np.ndarray,
    connected: Sequence[int],
    node_xy: NodeXY,
    *,
    node_layers: Optional[Sequence[str]] = None,
    follow_layer: Optional[str] = None,
    tap_mode: Optional[str] = None,
    follow_pin_nodes: Optional[Sequence[int]] = None,
) -> List[Tuple[List[int], List[float], float]]:
    """Per-cell current split onto local (connected-subgraph) node indices.

    Default (no tap_mode): k=4 IDW across all connected nodes.
    ``tap_mode='follow_pin'``: nearest 1–2 follow-pin nodes; else nearest any.
    """
    m = len(connected)
    if m == 0 or cell_i.size == 0:
        return []
    coords = np.asarray([node_xy[i] for i in connected], dtype=float)
    fp_locals: List[int] = []
    if tap_mode == "follow_pin":
        if follow_pin_nodes is not None:
            fpset = set(int(x) for x in follow_pin_nodes)
            fp_locals = [j for j, old in enumerate(connected) if old in fpset]
        if not fp_locals and node_layers is not None and follow_layer:
            fl = str(follow_layer)
            fp_locals = [
                j
                for j, old in enumerate(connected)
                if old < len(node_layers) and str(node_layers[old]) == fl
            ]
    if tap_mode == "follow_pin" and fp_locals:
        subset = fp_locals
        k_soft = min(2, len(subset))
        sub_coords = coords[subset]
    else:
        subset = list(range(m))
        k_soft = 1 if tap_mode == "follow_pin" else min(4, m)
        sub_coords = coords

    out: List[Tuple[List[int], List[float], float]] = []
    for x, y, cur in zip(xs, ys, cell_i):
        c = float(cur)
        if c == 0.0 or not np.isfinite(c):
            continue
        d2 = (sub_coords[:, 0] - float(x)) ** 2 + (sub_coords[:, 1] - float(y)) ** 2
        k_use = min(k_soft, int(d2.size))
        if k_use <= 1:
            jsub = int(np.argmin(d2))
            out.append(([int(subset[jsub])], [1.0], c))
        else:
            idxs = np.argpartition(d2, k_use - 1)[:k_use]
            w = 1.0 / np.maximum(np.sqrt(d2[idxs]), 1e-6)
            weights = w / float(np.sum(w))
            js = [int(subset[int(t)]) for t in idxs]
            ws = [float(wj) for wj in weights]
            out.append((js, ws, c))
    return out


def _stamp_edges(
    row_list: List[int],
    col_list: List[int],
    data_list: List[float],
    edges: Sequence[Edge],
    old_to_new: Dict[int, int],
    offset: int = 0,
) -> None:
    for a, b, g in edges:
        ia = old_to_new.get(int(a))
        ib = old_to_new.get(int(b))
        if ia is None or ib is None or ia == ib:
            continue
        gg = float(g)
        if gg <= 0.0:
            continue
        ia += offset
        ib += offset
        row_list.extend([ia, ia, ib, ib])
        col_list.extend([ia, ib, ib, ia])
        data_list.extend([gg, -gg, gg, -gg])


def _mna_solve(G, J: np.ndarray, supply_voltage: float) -> Tuple[np.ndarray, str]:
    m = int(J.size)
    solve_method = "spsolve"
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            V_red = np.asarray(spsolve(G, J), dtype=float).reshape(-1)
            bad = (
                V_red.size != m
                or not np.all(np.isfinite(V_red))
                or any("singular" in str(w.message).lower() for w in caught)
            )
            if bad:
                raise np.linalg.LinAlgError("singular or non-finite spsolve result")
    except Exception:
        try:
            x0 = np.full(m, float(supply_voltage), dtype=float)
            try:
                sol = lsmr(G, J, atol=1e-10, btol=1e-10, x0=x0)
            except TypeError:
                sol = lsmr(G, J, atol=1e-10, btol=1e-10)
            V_red = np.asarray(sol[0], dtype=float).reshape(-1)
            solve_method = "lsmr"
            if V_red.size != m or not np.all(np.isfinite(V_red)):
                raise ValueError("non-finite lsmr result")
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
    return V_red, solve_method


def _empty_rail(rail: str, n: int, edges_n: int, vdd: float, detail: str, floating=None) -> Dict[str, Any]:
    fl = floating if floating is not None else list(range(n))
    return {
        "error": f"{rail}: {detail}",
        "min_voltage": 0.0,
        "min_voltage_raw": 0.0,
        "solver_residual": None,
        "max_drop": vdd,
        "nodes": n,
        "edges": edges_n,
        "violations": [{"type": "connectivity", "rail": rail, "detail": detail}],
        "floating_nodes": fl[:50] if fl and not isinstance(fl[0], dict) else fl,
        "floating_count": len(fl) if not (fl and isinstance(fl[0], dict)) else len(fl),
        "max_j_ma_per_um": 0.0,
        "j_violations": 0,
        "j_histogram": [],
    }


def _solve_rail(
    *,
    node_xy: NodeXY,
    edges: List[Edge],
    supply_nodes: List[int],
    supply_voltage: float,
    currents_xy: Tuple[np.ndarray, np.ndarray, np.ndarray],
    external_r: float,
    rail: str,
    is_ground: bool,
    min_ratio: float,
    vdd: float,
    node_layers: Optional[Sequence[str]] = None,
    follow_layer: Optional[str] = None,
    tap_mode: Optional[str] = None,
    follow_pin_nodes: Optional[Sequence[int]] = None,
    edge_meta: Optional[Sequence[EdgeMeta]] = None,
    j_max_ma_per_um: float = 1.0,
) -> Dict[str, Any]:
    """Solve DC IR on the supply-connected subgraph only.

    Floating / unreachable PDN nodes are excluded from the MNA system. Solving
    the full singular matrix previously made ``spsolve`` fail and ``lsmr``
    return non-physical negative VPWR voltages.
    """
    n = len(node_xy)
    if n < 2 or not edges:
        return _empty_rail(rail, n, 0, vdd, "empty grid")

    supplies = [s for s in (supply_nodes or [0]) if 0 <= int(s) < n]
    if not supplies:
        supplies = [0]
    floating = set(_floating_nodes(n, edges, supplies))
    connected = [i for i in range(n) if i not in floating]
    floating_list = sorted(floating)
    float_xy = [{"x": float(node_xy[i][0]), "y": float(node_xy[i][1])} for i in floating_list[:50]]
    if not connected:
        out = _empty_rail(rail, n, len(edges), vdd, "all floating", float_xy)
        out["floating_count"] = len(floating_list)
        return out

    old_to_new = {old: new for new, old in enumerate(connected)}
    m = len(connected)
    row_list: List[int] = []
    col_list: List[int] = []
    data_list: List[float] = []
    _stamp_edges(row_list, col_list, data_list, edges, old_to_new)

    J = np.zeros(m, dtype=float)
    # Ideal package pin → source: large Thevenin conductance (Dirichlet-like)
    g_ext = 1.0 / max(float(external_r), 1e-12) if external_r and external_r > 0 else 1e6
    supply_local = 0
    for s in supplies:
        i = old_to_new.get(int(s))
        if i is None:
            continue
        row_list.append(i)
        col_list.append(i)
        data_list.append(g_ext)
        J[i] += g_ext * float(supply_voltage)
        supply_local += 1
    if supply_local == 0:
        out = _empty_rail(rail, n, len(edges), vdd, "no supply in component", float_xy)
        out["floating_count"] = len(floating_list)
        return out

    g_eps = 1e-12
    for i in range(m):
        row_list.append(i)
        col_list.append(i)
        data_list.append(g_eps)

    xs, ys, cell_i = currents_xy
    taps = _tap_injections(
        xs, ys, cell_i, connected, node_xy,
        node_layers=node_layers,
        follow_layer=follow_layer,
        tap_mode=tap_mode,
        follow_pin_nodes=follow_pin_nodes,
    )
    injection_nodes: List[int] = []
    spice_i: List[Tuple[int, float]] = []
    for js, ws, c in taps:
        for j, wj in zip(js, ws):
            amp = c * float(wj)
            if is_ground:
                J[int(j)] += amp
            else:
                J[int(j)] -= amp
            injection_nodes.append(int(connected[int(j)]))
            spice_i.append((int(connected[int(j)]), float(amp)))

    G = sparse.coo_matrix(
        (np.asarray(data_list, dtype=float), (np.asarray(row_list), np.asarray(col_list))),
        shape=(m, m),
    ).tocsr()

    try:
        V_red_raw, solve_method = _mna_solve(G, J, supply_voltage)
    except Exception as exc:
        out = _empty_rail(rail, n, len(edges), vdd, f"solve failed: {exc}", float_xy)
        out["solve_method"] = "failed"
        out["floating_count"] = len(floating_list)
        out["violations"] = [{"type": "solve_failure", "rail": rail, "detail": str(exc)}]
        return out

    residual = float(np.linalg.norm(G.dot(V_red_raw) - J))
    if is_ground:
        min_voltage_raw = float(np.min(V_red_raw)) if V_red_raw.size else 0.0
        max_voltage_raw = float(np.max(V_red_raw)) if V_red_raw.size else 0.0
        V_red = np.clip(V_red_raw, 0.0, float(vdd))
    else:
        min_voltage_raw = float(np.min(V_red_raw)) if V_red_raw.size else float(supply_voltage)
        max_voltage_raw = float(np.max(V_red_raw)) if V_red_raw.size else float(supply_voltage)
        V_red = np.clip(V_red_raw, 0.0, float(supply_voltage))

    V = np.full(n, np.nan, dtype=float)
    V_raw = np.full(n, np.nan, dtype=float)
    for old, new in old_to_new.items():
        V[old] = float(V_red[new])
        V_raw[old] = float(V_red_raw[new])

    j_info = _current_density(edge_meta, V_raw, j_max_ma_per_um)
    layers_seq = list(node_layers) if node_layers is not None else []
    worst_layer = _worst_nodes_by_layer(node_xy, layers_seq, V, connected, is_ground)

    float_viol = [
        {
            "type": "floating",
            "rail": rail,
            "x": float(node_xy[idx][0]),
            "y": float(node_xy[idx][1]),
            "detail": "node not connected to supply",
        }
        for idx in floating_list[:20]
    ]

    if is_ground:
        max_v = float(np.max(V_red)) if V_red.size else 0.0
        min_v = float(np.min(V_red)) if V_red.size else 0.0
        max_drop = max(0.0, max_v)
        allow = (1.0 - min_ratio) * vdd
        viol_idx = [connected[i] for i, vv in enumerate(V_red) if vv > allow]
        violations = [
            {
                "type": "voltage",
                "rail": rail,
                "x": float(node_xy[idx][0]),
                "y": float(node_xy[idx][1]),
                "voltage": float(V[idx]),
            }
            for idx in viol_idx[:100]
        ] + float_viol
        return {
            "min_voltage": min_v,
            "min_voltage_raw": min_voltage_raw,
            "max_voltage": max_v,
            "max_voltage_raw": max_voltage_raw,
            "max_drop": max_drop,
            "solver_residual": residual,
            "nodes": n,
            "edges": len(edges),
            "solve_method": solve_method,
            "violations": violations,
            "floating_nodes": float_xy,
            "floating_count": len(floating_list),
            "V": V,
            "connected": connected,
            "injection_nodes": list(dict.fromkeys(injection_nodes)),
            "spice_i": spice_i,
            "supply_count": supply_local,
            "worst_node_by_layer": worst_layer,
            **j_info,
        }

    min_v = float(np.min(V_red)) if V_red.size else float(supply_voltage)
    max_drop = max(0.0, float(supply_voltage) - min_v)
    threshold = min_ratio * vdd
    viol_idx = [connected[i] for i, vv in enumerate(V_red) if vv < threshold]
    violations = [
        {
            "type": "voltage",
            "rail": rail,
            "x": float(node_xy[idx][0]),
            "y": float(node_xy[idx][1]),
            "voltage": float(V[idx]),
        }
        for idx in viol_idx[:100]
    ] + float_viol
    return {
        "min_voltage": min_v,
        "min_voltage_raw": min_voltage_raw,
        "max_voltage_raw": max_voltage_raw,
        "max_drop": max_drop,
        "solver_residual": residual,
        "nodes": n,
        "edges": len(edges),
        "solve_method": solve_method,
        "violations": violations,
        "floating_nodes": float_xy,
        "floating_count": len(floating_list),
        "V": V,
        "connected": connected,
        "injection_nodes": list(dict.fromkeys(injection_nodes)),
        "spice_i": spice_i,
        "supply_count": supply_local,
        "worst_node_by_layer": worst_layer,
        **j_info,
    }


def _solve_coupled(
    *,
    vdd_graph: Dict[str, Any],
    vss_graph: Dict[str, Any],
    vdd_supplies: List[int],
    vss_supplies: List[int],
    currents_xy: Tuple[np.ndarray, np.ndarray, np.ndarray],
    external_r: float,
    vdd: float,
    min_ratio: float,
    vdd_net: str,
    vss_net: str,
    follow_layer: Optional[str],
    tap_mode: Optional[str],
    j_max: float,
) -> Tuple[Dict[str, Any], Dict[str, Any], float]:
    """One MNA: instance current leaves VPWR and enters VGND. Returns (vdd, vss, residual)."""
    n1 = len(vdd_graph["node_xy"])
    n2 = len(vss_graph["node_xy"])
    e1 = vdd_graph["edges"]
    e2 = vss_graph["edges"]
    s1 = [s for s in vdd_supplies if 0 <= int(s) < n1] or [0]
    s2 = [s for s in vss_supplies if 0 <= int(s) < n2] or [0]
    fl1 = set(_floating_nodes(n1, e1, s1))
    fl2 = set(_floating_nodes(n2, e2, s2))
    c1 = [i for i in range(n1) if i not in fl1]
    c2 = [i for i in range(n2) if i not in fl2]
    if not c1:
        vdd_rail = _empty_rail(vdd_net, n1, len(e1), vdd, "all floating")
        vss_rail = _empty_rail(vss_net, n2, len(e2), vdd, "skipped")
        return vdd_rail, vss_rail, 0.0

    map1 = {old: new for new, old in enumerate(c1)}
    map2 = {old: new for new, old in enumerate(c2)}
    m1, m2 = len(c1), len(c2)
    m = m1 + m2
    row_list: List[int] = []
    col_list: List[int] = []
    data_list: List[float] = []
    _stamp_edges(row_list, col_list, data_list, e1, map1, 0)
    _stamp_edges(row_list, col_list, data_list, e2, map2, m1)

    J = np.zeros(m, dtype=float)
    g_ext = 1.0 / max(float(external_r), 1e-12) if external_r and external_r > 0 else 1e6
    n_src = 0
    for s in s1:
        i = map1.get(int(s))
        if i is None:
            continue
        row_list.append(i)
        col_list.append(i)
        data_list.append(g_ext)
        J[i] += g_ext * float(vdd)
        n_src += 1
    for s in s2:
        i = map2.get(int(s))
        if i is None:
            continue
        ii = i + m1
        row_list.append(ii)
        col_list.append(ii)
        data_list.append(g_ext)
        # VSS Dirichlet at 0 V
        n_src += 1
    for i in range(m):
        row_list.append(i)
        col_list.append(i)
        data_list.append(1e-12)

    xs, ys, cell_i = currents_xy
    taps_v = _tap_injections(
        xs, ys, cell_i, c1, vdd_graph["node_xy"],
        node_layers=vdd_graph.get("node_layers"),
        follow_layer=follow_layer,
        tap_mode=tap_mode,
        follow_pin_nodes=vdd_graph.get("follow_pin_nodes"),
    )
    taps_g = _tap_injections(
        xs, ys, cell_i, c2, vss_graph["node_xy"],
        node_layers=vss_graph.get("node_layers"),
        follow_layer=follow_layer,
        tap_mode=tap_mode,
        follow_pin_nodes=vss_graph.get("follow_pin_nodes"),
    ) if c2 else []
    spice_i: List[Tuple[int, int, float]] = []
    # Pair by instance order: both tap lists skip zero currents in the same zip order
    for (js_v, ws_v, c), tap_g in zip(taps_v, taps_g if taps_g else [([], [], 0.0)] * len(taps_v)):
        for j, wj in zip(js_v, ws_v):
            J[int(j)] -= c * float(wj)
        if tap_g[0]:
            js_g, ws_g, _cg = tap_g
            for j, wj in zip(js_g, ws_g):
                J[m1 + int(j)] += c * float(wj)
            spice_i.append((int(c1[int(js_v[0])]), int(c2[int(js_g[0])]) + n1, float(c)))
        else:
            spice_i.append((int(c1[int(js_v[0])]), -1, float(c)))

    G = sparse.coo_matrix(
        (np.asarray(data_list, dtype=float), (np.asarray(row_list), np.asarray(col_list))),
        shape=(m, m),
    ).tocsr()
    try:
        V_all_raw, method = _mna_solve(G, J, vdd)
    except Exception as exc:
        vdd_rail = _empty_rail(vdd_net, n1, len(e1), vdd, f"solve failed: {exc}")
        vdd_rail["solve_method"] = "failed"
        vss_rail = _empty_rail(vss_net, n2, len(e2), vdd, "coupled failed")
        return vdd_rail, vss_rail, 0.0

    residual = float(np.linalg.norm(G.dot(V_all_raw) - J))
    V1_raw = V_all_raw[:m1]
    V2_raw = V_all_raw[m1:] if m2 else np.zeros(0)
    V1 = np.clip(V1_raw, 0.0, float(vdd))
    V2 = np.clip(V2_raw, 0.0, float(vdd)) if m2 else V2_raw

    V_vdd = np.full(n1, np.nan)
    V_vdd_raw = np.full(n1, np.nan)
    for old, new in map1.items():
        V_vdd[old] = float(V1[new])
        V_vdd_raw[old] = float(V1_raw[new])
    V_vss = np.full(n2, np.nan)
    V_vss_raw = np.full(n2, np.nan)
    for old, new in map2.items():
        V_vss[old] = float(V2[new])
        V_vss_raw[old] = float(V2_raw[new])

    min_v = float(np.min(V1)) if V1.size else float(vdd)
    min_raw = float(np.min(V1_raw)) if V1_raw.size else float(vdd)
    max_g = float(np.max(V2)) if V2.size else 0.0
    max_g_raw = float(np.max(V2_raw)) if V2_raw.size else 0.0
    j_vdd = _current_density(vdd_graph.get("edge_meta"), V_vdd_raw, j_max)
    j_vss = _current_density(vss_graph.get("edge_meta"), V_vss_raw, j_max)
    fl1_xy = [{"x": float(vdd_graph["node_xy"][i][0]), "y": float(vdd_graph["node_xy"][i][1])} for i in sorted(fl1)[:50]]
    fl2_xy = [{"x": float(vss_graph["node_xy"][i][0]), "y": float(vss_graph["node_xy"][i][1])} for i in sorted(fl2)[:50]]

    vdd_rail = {
        "min_voltage": min_v,
        "min_voltage_raw": min_raw,
        "max_drop": max(0.0, float(vdd) - min_v),
        "solver_residual": residual,
        "nodes": n1,
        "edges": len(e1),
        "solve_method": method,
        "violations": [],
        "floating_nodes": fl1_xy,
        "floating_count": len(fl1),
        "V": V_vdd,
        "connected": c1,
        "supply_count": n_src,
        "spice_i_coupled": spice_i,
        "worst_node_by_layer": _worst_nodes_by_layer(
            vdd_graph["node_xy"], vdd_graph.get("node_layers") or [], V_vdd, c1, False
        ),
        **j_vdd,
    }
    vss_rail = {
        "min_voltage": float(np.min(V2)) if V2.size else 0.0,
        "min_voltage_raw": float(np.min(V2_raw)) if V2_raw.size else 0.0,
        "max_voltage": max_g,
        "max_voltage_raw": max_g_raw,
        "max_drop": max(0.0, max_g),
        "solver_residual": residual,
        "nodes": n2,
        "edges": len(e2),
        "solve_method": method,
        "violations": [],
        "floating_nodes": fl2_xy,
        "floating_count": len(fl2),
        "V": V_vss,
        "connected": c2,
        "worst_node_by_layer": _worst_nodes_by_layer(
            vss_graph["node_xy"], vss_graph.get("node_layers") or [], V_vss, c2, True
        ),
        **j_vss,
    }
    return vdd_rail, vss_rail, residual


def _spice_payload(
    graph: Dict[str, Any],
    supplies: Sequence[int],
    supply_voltage: float,
    spice_i: Sequence[Tuple[int, float]],
    *,
    source_type: str,
    corner: str,
    vss_graph: Optional[Dict[str, Any]] = None,
    coupled_i: Optional[Sequence[Tuple[int, int, float]]] = None,
) -> Dict[str, Any]:
    edges_r = []
    for a, b, g in graph.get("edges") or []:
        edges_r.append((int(a), int(b), 1.0 / max(float(g), 1e-12)))
    offset = len(graph.get("node_xy") or [])
    v_sources = [(int(s), float(supply_voltage)) for s in supplies]
    i_sources: List[Tuple[int, int, float]] = []
    if coupled_i:
        i_sources = [(int(a), int(b) if int(b) >= 0 else 0, float(c)) for a, b, c in coupled_i]
        if vss_graph:
            for a, b, g in vss_graph.get("edges") or []:
                edges_r.append((int(a) + offset, int(b) + offset, 1.0 / max(float(g), 1e-12)))
            for s in vss_graph.get("_supplies") or []:
                v_sources.append((int(s) + offset, 0.0))
    else:
        i_sources = [(int(n), 0, float(c)) for n, c in spice_i if c]
    return {
        "source_type": source_type,
        "corner": corner,
        "edges": edges_r,
        "v_sources": v_sources,
        "i_sources": i_sources,
        "n_nodes": offset + (len(vss_graph["node_xy"]) if vss_graph else 0),
    }


def _format_spice_netlist(payload: Dict[str, Any], title: str = "IR") -> str:
    lines = [
        f"* {title} static DC IR netlist (PDNSim-inspired, not signoff)",
        f"* source_type={payload.get('source_type', '')} corner={payload.get('corner', '')}",
    ]
    for i, e in enumerate(payload.get("edges") or []):
        a, b = int(e[0]), int(e[1])
        r = max(float(e[2]) if len(e) > 2 else 1.0, 1e-12)
        lines.append(f"R{i} n{a} n{b} {r:.8g}")
    for i, vs in enumerate(payload.get("v_sources") or []):
        node, volt = int(vs[0]), float(vs[1])
        lines.append(f"V{i} n{node} 0 {volt:.8g}")
    for i, src in enumerate(payload.get("i_sources") or []):
        if len(src) >= 3:
            a, b, amp = int(src[0]), int(src[1]), float(src[2])
        else:
            a, amp = int(src[0]), float(src[1])
            b = 0
        if amp == 0.0:
            continue
        lines.append(f"I{i} n{a} n{b} {amp:.8g}")
    if not any(l.startswith("R") for l in lines):
        lines.append("Rdummy n0 0 1e9")
    if not any(l.startswith("V") for l in lines):
        lines.append("Vdummy n0 0 1.8")
    lines.append(".op")
    lines.append(".end")
    lines.append("")
    return "\n".join(lines)


def write_ir_spice(ir: Dict[str, Any], path: Union[str, Path]) -> Path:
    """Write a SPICE netlist (PDN R + V sources + I sources, .op)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ir.get("spice_netlist")
    if not text:
        payload = ir.get("spice") or {}
        title = str(ir.get("design") or "IR")
        text = _format_spice_netlist(payload, title=title)
    path.write_text(text, encoding="utf-8")
    return path


def _merge_j(*infos: Dict[str, Any]) -> Dict[str, Any]:
    max_j = 0.0
    viol = 0
    n_seg = 0
    worst = None
    limit = 1.0
    n_bins = len(_J_HIST_EDGES) - 1
    counts = [0] * n_bins
    for info in infos:
        if not info:
            continue
        mj = float(info.get("max_j_ma_per_um") or 0.0)
        if mj > max_j:
            max_j = mj
            worst = info.get("worst_segment")
        viol += int(info.get("j_violations") or 0)
        n_seg += int(info.get("n_metal_segments") or 0)
        limit = float(info.get("j_max_limit") or limit)
        for i, bin_ in enumerate(info.get("j_histogram") or []):
            if i < n_bins:
                counts[i] += int(bin_.get("count") or 0)
    hist = [
        {"lo": lo, "hi": None if np.isinf(hi) else hi, "count": counts[i]}
        for i, (lo, hi) in enumerate(zip(_J_HIST_EDGES, _J_HIST_EDGES[1:]))
    ]
    return {
        "max_j_ma_per_um": max_j,
        "j_violations": viol,
        "j_max_limit": limit,
        "j_histogram": hist,
        "worst_segment": worst,
        "n_metal_segments": n_seg,
    }


def _ir_from_power_grid(
    design: DesignObject,
    config: Dict[str, Any],
    clock_period_ns: float,
) -> Optional[Dict[str, Any]]:
    ir_cfg = config.get("ir_drop", {}) or {}
    pcfg = config.get("power", {}) or {}
    vdd = _rail_vdd(design, config)
    corner = str(ir_cfg.get("corner", "tt") or "tt").lower()
    min_ratio = float(config.get("thresholds", {}).get("ir_min_vdd_ratio", 0.95))
    sample = float(ir_cfg.get("pdn_sample_um", 2.0))
    external_r = float(ir_cfg.get("external_resistance_ohm", 0.0))
    analyze_vss = bool(ir_cfg.get("analyze_vss", True))
    source_type = str(ir_cfg.get("source_type", "straps") or "straps").lower()
    coupled = bool(ir_cfg.get("coupled", False))
    follow_layer = str(pcfg.get("follow_pin_layer", "met1"))
    j_max = float(ir_cfg.get("j_max_ma_per_um", 1.0) or 1.0)
    temp_scale = _r_temp_scale(config)

    vdd_net = design.power_grid.get("vdd_net", "VPWR")
    vss_net = design.power_grid.get("vss_net", "VGND")
    all_segs = design.power_grid.get("segments", [])
    vdd_segs = [s for s in all_segs if s.get("net") == vdd_net]
    vss_segs = [s for s in all_segs if s.get("net") == vss_net]
    if len(vdd_segs) < 2:
        return None

    xs, ys, currents, current_summary = _instance_currents(
        design, config, vdd, clock_period_ns
    )
    currents_xy = (xs, ys, currents)
    names = list(design.instances.keys())

    g_vdd = _build_rail_graph_ex(vdd_segs, design, config, sample, temp_scale=temp_scale)
    vdd_src = _resolve_supplies(g_vdd, _requested_supply_nodes(g_vdd, source_type, design, config))

    g_vss: Optional[Dict[str, Any]] = None
    vss_src: List[int] = []
    if analyze_vss and len(vss_segs) >= 2:
        g_vss = _build_rail_graph_ex(vss_segs, design, config, sample, temp_scale=temp_scale)
        vss_type = "straps" if source_type == "straps" else source_type
        if source_type == "bumps":
            # Ground bumps on VSS top layer (power.vss_layer)
            ir_b = dict(ir_cfg)
            top = str((design.power_grid or {}).get("vss_layer") or pcfg.get("vss_layer", "met4"))
            vss_src = _bump_nodes(
                g_vss["node_xy"], g_vss.get("node_layers") or [], top,
                design.die_area, int(ir_cfg.get("bump_n", 4) or 4),
            )
            vss_src = _resolve_supplies(g_vss, vss_src)
        else:
            vss_src = _resolve_supplies(
                g_vss, _requested_supply_nodes(g_vss, vss_type, design, config)
            )

    via_vdd = int(g_vdd["via_n"])
    via_vss = int(g_vss["via_n"]) if g_vss else 0
    solve_kw = dict(
        node_layers=g_vdd.get("node_layers"),
        follow_layer=follow_layer,
        tap_mode="follow_pin",
        follow_pin_nodes=g_vdd.get("follow_pin_nodes"),
        edge_meta=g_vdd.get("edge_meta"),
        j_max_ma_per_um=j_max,
    )

    used_coupled = False
    if coupled and g_vss is not None:
        vdd_rail, vss_rail, _cres = _solve_coupled(
            vdd_graph=g_vdd,
            vss_graph=g_vss,
            vdd_supplies=vdd_src,
            vss_supplies=vss_src,
            currents_xy=currents_xy,
            external_r=external_r,
            vdd=vdd,
            min_ratio=min_ratio,
            vdd_net=vdd_net,
            vss_net=vss_net,
            follow_layer=follow_layer,
            tap_mode="follow_pin",
            j_max=j_max,
        )
        used_coupled = vdd_rail.get("error") is None
    else:
        vdd_rail = _solve_rail(
            node_xy=g_vdd["node_xy"],
            edges=g_vdd["edges"],
            supply_nodes=vdd_src,
            supply_voltage=vdd,
            currents_xy=currents_xy,
            external_r=external_r,
            rail=vdd_net,
            is_ground=False,
            min_ratio=min_ratio,
            vdd=vdd,
            **solve_kw,
        )
        vss_rail = {
            "min_voltage": 0.0,
            "max_drop": 0.0,
            "nodes": 0,
            "edges": 0,
            "violations": [],
            "floating_nodes": [],
            "floating_count": 0,
            "solve_method": "n/a",
            "min_voltage_raw": 0.0,
            "solver_residual": 0.0,
            "max_j_ma_per_um": 0.0,
            "j_violations": 0,
            "j_histogram": [],
        }
        if g_vss is not None:
            vss_rail = _solve_rail(
                node_xy=g_vss["node_xy"],
                edges=g_vss["edges"],
                supply_nodes=vss_src,
                supply_voltage=0.0,
                currents_xy=currents_xy,
                external_r=external_r,
                rail=vss_net,
                is_ground=True,
                min_ratio=min_ratio,
                vdd=vdd,
                node_layers=g_vss.get("node_layers"),
                follow_layer=follow_layer,
                tap_mode="follow_pin",
                follow_pin_nodes=g_vss.get("follow_pin_nodes"),
                edge_meta=g_vss.get("edge_meta"),
                j_max_ma_per_um=j_max,
            )

    max_ir = float(vdd_rail.get("max_drop", 0.0) or 0.0)
    max_gnd = float(vss_rail.get("max_drop", 0.0) or 0.0)
    collapse = max_ir + max_gnd
    threshold = min_ratio * vdd
    min_v = float(vdd_rail.get("min_voltage", vdd))
    min_v_raw = float(vdd_rail.get("min_voltage_raw", min_v) or min_v)
    sample_n = max(1, int(ir_cfg.get("instance_sample", 200)))
    residual = vdd_rail.get("solver_residual")
    if vss_rail.get("solver_residual") is not None and residual is not None:
        residual = max(float(residual), float(vss_rail.get("solver_residual") or 0.0))

    floating_count = int(vdd_rail.get("floating_count", 0)) + int(
        vss_rail.get("floating_count", 0)
    )

    V_vdd = vdd_rail.get("V")
    conn_vdd = list(vdd_rail.get("connected") or [])
    instance_drops: List[Dict[str, Any]] = []
    instances_affected = 0
    inst_v = np.full(len(names), np.nan)
    if V_vdd is not None and conn_vdd and names:
        inst_v = _nearest_node_voltages(
            xs, ys, g_vdd["node_xy"], np.asarray(V_vdd), conn_vdd,
            node_layers=g_vdd.get("node_layers"),
            prefer_layer=follow_layer,
        )
        if used_coupled:
            V_vss = vss_rail.get("V")
            conn_vss = list(vss_rail.get("connected") or [])
            if V_vss is not None and conn_vss and g_vss is not None:
                inst_g = _nearest_node_voltages(
                    xs, ys, g_vss["node_xy"], np.asarray(V_vss), conn_vss,
                    node_layers=g_vss.get("node_layers"),
                    prefer_layer=follow_layer,
                )
                inst_g = np.where(np.isfinite(inst_g), inst_g, 0.0)
                inst_v = inst_v - inst_g
        instance_drops, instances_affected = _instance_ir_drops(
            names, inst_v, vdd=vdd, threshold=threshold, rail=vdd_net
        )
        if instance_drops:
            max_ir = max(max_ir, float(instance_drops[0]["drop_v"]))
            min_v = min(min_v, float(instance_drops[0]["voltage"]))
            collapse = max_ir + max_gnd
        if used_coupled and names:
            # Physical collapse at the worst instance
            finite = inst_v[np.isfinite(inst_v)]
            if finite.size:
                local = np.clip(finite, 0.0, float(vdd))
                collapse = float(np.max(vdd - local))

    heatmap = _instance_heatmap(names, xs, ys, inst_v, vdd, cap=500) if names else []
    j_all = _merge_j(vdd_rail, vss_rail if g_vss is not None else {})
    worst_layers = dict(vdd_rail.get("worst_node_by_layer") or {})
    if vss_rail.get("worst_node_by_layer"):
        for ly, rec in (vss_rail["worst_node_by_layer"] or {}).items():
            worst_layers[f"vss:{ly}"] = rec

    if used_coupled:
        spice = _spice_payload(
            g_vdd, vdd_src, vdd, [],
            source_type=source_type, corner=corner,
            vss_graph={**g_vss, "_supplies": vss_src} if g_vss else None,
            coupled_i=vdd_rail.get("spice_i_coupled"),
        )
    else:
        spice = _spice_payload(
            g_vdd, vdd_src, vdd, vdd_rail.get("spice_i") or [],
            source_type=source_type, corner=corner,
        )
    spice_netlist = _format_spice_netlist(spice, title=design.name)

    err = vdd_rail.get("error") or vss_rail.get("error")
    note = (
        "Static DC MNA (PDNSim-inspired, not signoff); follow-pin taps; "
        f"source_type={source_type}; no vector/dynamic IR"
        + ("; coupled VDD+VSS" if used_coupled else "")
    )
    return {
        "vdd": vdd,
        "threshold": threshold,
        "min_voltage": min_v,
        "min_voltage_raw": min_v_raw,
        "solver_residual": residual,
        "max_drop": max_ir,
        "max_ir_drop": max_ir,
        "max_ground_bounce": max_gnd,
        "max_supply_collapse": collapse,
        "violation_count": instances_affected,
        "instances_affected": instances_affected,
        "instance_drops": instance_drops[:sample_n],
        "violations": instance_drops[:sample_n],
        "instance_heatmap": heatmap,
        "worst_node_by_layer": worst_layers,
        "floating_nodes": floating_count,
        "floating_sample": (vdd_rail.get("floating_nodes") or [])[:20]
        + (vss_rail.get("floating_nodes") or [])[:20],
        "total_current_a": current_summary["total_a"],
        "avg_cell_current_a": current_summary["avg_cell_a"],
        "currents": current_summary,
        "vdd_rail": {
            "min_voltage": vdd_rail.get("min_voltage"),
            "min_voltage_raw": vdd_rail.get("min_voltage_raw"),
            "max_drop": vdd_rail.get("max_drop"),
            "nodes": vdd_rail.get("nodes"),
            "edges": vdd_rail.get("edges"),
            "instances_affected": instances_affected,
            "supply_count": vdd_rail.get("supply_count"),
            "solver_residual": vdd_rail.get("solver_residual"),
        },
        "vss_rail": {
            "min_voltage": vss_rail.get("min_voltage"),
            "max_drop": vss_rail.get("max_drop"),
            "max_voltage": vss_rail.get("max_voltage"),
            "nodes": vss_rail.get("nodes"),
            "edges": vss_rail.get("edges"),
            "solver_residual": vss_rail.get("solver_residual"),
        },
        "ir_mode": "power_grid",
        "note": note,
        "via_edges": int(via_vdd + via_vss),
        "solve_method": {
            "vdd": vdd_rail.get("solve_method"),
            "vss": vss_rail.get("solve_method"),
        },
        "grid": {
            "nodes": int(vdd_rail.get("nodes", 0)) + int(vss_rail.get("nodes", 0)),
            "edges": int(vdd_rail.get("edges", 0)) + int(vss_rail.get("edges", 0)),
            "sample_um": sample,
            "via_edges": int(via_vdd + via_vss),
            "follow_pin_nodes": len(g_vdd.get("follow_pin_nodes") or []),
            "strap_nodes": len(g_vdd.get("strap_nodes") or []),
        },
        "error": err,
        "source_type": source_type,
        "source_count": int(vdd_rail.get("supply_count") or len(vdd_src)),
        "corner": corner,
        "coupled": used_coupled,
        "max_j_ma_per_um": j_all["max_j_ma_per_um"],
        "j_violations": j_all["j_violations"],
        "j_histogram": j_all["j_histogram"],
        "j_max_limit": j_all["j_max_limit"],
        "spice": spice,
        "spice_netlist": spice_netlist,
        "design": design.name,
    }


def _ir_synthetic_mesh(
    design: DesignObject,
    config: Dict[str, Any],
    clock_period_ns: float,
) -> Dict[str, Any]:
    ir_cfg = config.get("ir_drop", {})
    layers = list(
        ir_cfg.get("layers", design.tech.get("power_layers", ["met1", "met2", "met3", "met4", "met5"]))
    )
    pitch = float(ir_cfg.get("grid_pitch_um", 10.0))
    vdd = _rail_vdd(design, config)
    corner = str(ir_cfg.get("corner", "tt") or "tt").lower()
    min_ratio = float(config.get("thresholds", {}).get("ir_min_vdd_ratio", 0.95))
    via_r = float(ir_cfg.get("via_r_ohm", 5.0))

    minx, miny, maxx, maxy = design.die_area
    nx = max(2, int((maxx - minx) / pitch) + 1)
    ny = max(2, int((maxy - miny) / pitch) + 1)
    n_layers = len(layers)
    per_layer = nx * ny
    n_nodes = per_layer * n_layers

    grid_x, grid_y = np.meshgrid(np.arange(nx), np.arange(ny), indexing="xy")
    grid_x = grid_x.ravel()
    grid_y = grid_y.ravel()
    base = grid_y * nx + grid_x

    edge_a: List[np.ndarray] = []
    edge_b: List[np.ndarray] = []
    edge_g: List[np.ndarray] = []
    g_via = 1.0 / max(via_r, 1e-9)
    temp_scale = _r_temp_scale(config)
    for il, layer in enumerate(layers):
        r_per = float(design.tech.get("layers", {}).get(layer, {}).get("r_per_um", 0.1))
        g_seg = 1.0 / max(r_per * pitch * temp_scale, 1e-12)
        offset = il * per_layer
        right = base[grid_x < nx - 1] + offset
        edge_a.append(right)
        edge_b.append(right + 1)
        edge_g.append(np.full(right.size, g_seg))
        up = base[grid_y < ny - 1] + offset
        edge_a.append(up)
        edge_b.append(up + nx)
        edge_g.append(np.full(up.size, g_seg))
        if il + 1 < n_layers:
            via = base + offset
            edge_a.append(via)
            edge_b.append(via + per_layer)
            edge_g.append(np.full(via.size, g_via))

    a = np.concatenate(edge_a) if edge_a else np.array([], dtype=np.int64)
    b = np.concatenate(edge_b) if edge_b else np.array([], dtype=np.int64)
    g = np.concatenate(edge_g) if edge_g else np.array([], dtype=float)

    rows = np.concatenate([a, a, b, b]) if a.size else np.array([], dtype=np.int64)
    cols = np.concatenate([a, b, b, a]) if a.size else np.array([], dtype=np.int64)
    data = np.concatenate([g, -g, g, -g]) if a.size else np.array([], dtype=float)

    g_big = 1e6
    J = np.zeros(n_nodes)
    boundary = (grid_x == 0) | (grid_x == nx - 1) | (grid_y == 0) | (grid_y == ny - 1)
    supply = np.unique(base[boundary] + (n_layers - 1) * per_layer)
    rows = np.concatenate([rows, supply])
    cols = np.concatenate([cols, supply])
    data = np.concatenate([data, np.full(supply.size, g_big)])
    J[supply] += g_big * vdd

    xs, ys, currents, current_summary = _instance_currents(
        design, config, vdd, clock_period_ns
    )
    if currents.size:
        ix = np.clip(((xs - minx) / pitch).astype(np.int64), 0, nx - 1)
        iy = np.clip(((ys - miny) / pitch).astype(np.int64), 0, ny - 1)
        nodes = iy * nx + ix
        J -= np.bincount(nodes, weights=currents, minlength=n_nodes)

    G = sparse.coo_matrix((data, (rows, cols)), shape=(n_nodes, n_nodes)).tocsr()

    empty_extra = {
        "min_voltage_raw": 0.0,
        "solver_residual": None,
        "source_type": "ring",
        "corner": corner,
        "coupled": False,
        "max_j_ma_per_um": 0.0,
        "j_violations": 0,
        "j_histogram": [],
        "instance_heatmap": [],
        "worst_node_by_layer": {},
    }

    if not _connected_to_supply(a, b, supply, n_nodes):
        return {
            "error": "Power grid not fully connected to voltage sources",
            "violation_count": 1,
            "min_voltage": 0.0,
            "vdd": vdd,
            "threshold": min_ratio * vdd,
            "max_ir_drop": vdd,
            "max_ground_bounce": 0.0,
            "max_supply_collapse": vdd,
            "total_current_a": current_summary["total_a"],
            "avg_cell_current_a": current_summary["avg_cell_a"],
            "currents": current_summary,
            "floating_nodes": 1,
            "violations": [{"type": "connectivity", "detail": "unreachable nodes", "rail": "VPWR"}],
            "vdd_rail": {"min_voltage": 0.0, "max_drop": vdd, "nodes": n_nodes, "edges": int(a.size), "violations_sample": []},
            "vss_rail": {"min_voltage": 0.0, "max_drop": 0.0, "nodes": 0, "edges": 0, "violations_sample": []},
            "grid": {"nx": nx, "ny": ny, "layers": layers, "pitch_um": pitch},
            **empty_extra,
        }

    try:
        V_raw = np.asarray(spsolve(G, J), dtype=float).reshape(-1)
        if V_raw.size != n_nodes or not np.all(np.isfinite(V_raw)):
            raise np.linalg.LinAlgError("non-finite synthetic IR solve")
    except Exception as exc:
        return {
            "error": f"IR solve failed: {exc}",
            "violation_count": 1,
            "min_voltage": 0.0,
            "vdd": vdd,
            "threshold": min_ratio * vdd,
            "max_ir_drop": vdd,
            "max_ground_bounce": 0.0,
            "max_supply_collapse": vdd,
            "total_current_a": current_summary["total_a"],
            "avg_cell_current_a": current_summary["avg_cell_a"],
            "currents": current_summary,
            "floating_nodes": 0,
            "violations": [{"type": "solve_failure", "detail": str(exc), "rail": "VPWR"}],
            "vdd_rail": {"min_voltage": 0.0, "max_drop": vdd, "nodes": n_nodes, "edges": int(a.size), "violations_sample": []},
            "vss_rail": {"min_voltage": 0.0, "max_drop": 0.0, "nodes": 0, "edges": 0, "violations_sample": []},
            "grid": {"nx": nx, "ny": ny, "layers": layers, "pitch_um": pitch},
            **empty_extra,
        }

    residual = float(np.linalg.norm(G.dot(V_raw) - J))
    min_v_raw = float(np.min(V_raw[0:per_layer])) if per_layer else float(np.min(V_raw))
    V = np.clip(V_raw, 0.0, float(vdd))
    threshold = min_ratio * vdd
    sample_n = max(1, int(ir_cfg.get("instance_sample", 200)))
    met1_v = V[0:per_layer]
    min_v = float(np.min(met1_v)) if met1_v.size else vdd
    max_ir = max(0.0, vdd - min_v)

    names = list(design.instances.keys())
    instance_drops: List[Dict[str, Any]] = []
    instances_affected = 0
    inst_v = np.full(len(names), np.nan)
    if names and currents.size:
        ix = np.clip(((xs - minx) / pitch).astype(np.int64), 0, nx - 1)
        iy = np.clip(((ys - miny) / pitch).astype(np.int64), 0, ny - 1)
        inst_v = met1_v[iy * nx + ix]
        instance_drops, instances_affected = _instance_ir_drops(
            names, inst_v, vdd=vdd, threshold=threshold, rail="VPWR"
        )
        if instance_drops:
            max_ir = max(max_ir, float(instance_drops[0]["drop_v"]))
            min_v = min(min_v, float(instance_drops[0]["voltage"]))

    heatmap = _instance_heatmap(names, xs, ys, inst_v, vdd) if names else []
    return {
        "vdd": vdd,
        "threshold": threshold,
        "min_voltage": min_v,
        "min_voltage_raw": min_v_raw,
        "solver_residual": residual,
        "max_drop": max_ir,
        "max_ir_drop": max_ir,
        "max_ground_bounce": 0.0,
        "max_supply_collapse": max_ir,
        "violation_count": instances_affected,
        "instances_affected": instances_affected,
        "instance_drops": instance_drops[:sample_n],
        "violations": instance_drops[:sample_n],
        "instance_heatmap": heatmap,
        "worst_node_by_layer": {"met1": {"layer": "met1", "v": min_v}},
        "floating_nodes": 0,
        "total_current_a": current_summary["total_a"],
        "avg_cell_current_a": current_summary["avg_cell_a"],
        "currents": current_summary,
        "vdd_rail": {
            "min_voltage": min_v,
            "max_drop": max_ir,
            "nodes": n_nodes,
            "edges": int(a.size),
            "instances_affected": instances_affected,
        },
        "vss_rail": {
            "min_voltage": 0.0,
            "max_drop": 0.0,
            "nodes": 0,
            "edges": 0,
        },
        "note": "Synthetic mesh IR; count = instances below VDD threshold",
        "grid": {"nx": nx, "ny": ny, "layers": layers, "pitch_um": pitch},
        "source_type": "ring",
        "corner": corner,
        "coupled": False,
        "max_j_ma_per_um": 0.0,
        "j_violations": 0,
        "j_histogram": [],
    }


def _connected_to_supply(
    a: np.ndarray, b: np.ndarray, supply: np.ndarray, n: int
) -> bool:
    if a.size == 0:
        return False
    adj = sparse.coo_matrix((np.ones(a.size), (a, b)), shape=(n, n)).tocsr()
    adj = adj + adj.T
    _n_comp, labels = connected_components(adj, directed=False)
    reachable = int(np.isin(labels, np.unique(labels[supply])).sum())
    return reachable >= max(1, int(0.9 * n))
