"""TritonCTS-inspired clustered buffered clock tree synthesis."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

from pnr_tool.algorithms.base import ClockOptAlgorithm
from pnr_tool.algorithms.legalize import RowLegalizer
from pnr_tool.design.object import DesignObject

_CLOCK_PIN_NAMES = frozenset({"CLK", "CK", "CLK_N", "GCLK", "GATE"})
_BUFFER_HINTS = ("clkbuf", "clkinv", "buf_", "inv_", "clkdlybuf")

Sink = Tuple[str, str]  # (inst, pin)
Node = Union[Sink, str]  # sink pin or buffer instance name


def _lib_cell(design: DesignObject, inst: str) -> Dict[str, Any]:
    ctype = design.cells.get(inst, {}).get("cell_type", "")
    return design.library.get("cells", {}).get(ctype, {})


def _is_clock_pin(design: DesignObject, inst: str, pin: str) -> bool:
    if inst.startswith("PORT:") or inst not in design.cells:
        return False
    pinfo = _lib_cell(design, inst).get("pins", {}).get(pin, {})
    return bool(pinfo.get("is_clock")) or pin.upper() in _CLOCK_PIN_NAMES


def _is_buffer_like(design: DesignObject, inst: str) -> bool:
    ctype = design.cells.get(inst, {}).get("cell_type", "")
    if not any(hint in ctype for hint in _BUFFER_HINTS):
        return False
    pins = _lib_cell(design, inst).get("pins", {})
    inputs = [p for p, i in pins.items() if i.get("direction") == "input"]
    outputs = [p for p, i in pins.items() if i.get("direction") == "output"]
    return len(inputs) == 1 and len(outputs) == 1


def find_clock_roots(design: DesignObject) -> Dict[str, Dict[str, Any]]:
    """Trace every clock sink pin back to the net that feeds the clock tree."""
    roots: Dict[str, Dict[str, Any]] = {}
    resolved: Dict[str, Optional[Tuple[str, Tuple[str, ...]]]] = {}

    for net, ninfo in design.nets.items():
        sinks = [(i, p) for i, p in ninfo.get("pins", []) if _is_clock_pin(design, i, p)]
        if not sinks:
            continue
        found = _walk_to_root(design, net, resolved)
        if found is None:
            continue
        root_net, buffers = found
        entry = roots.setdefault(root_net, {"sinks": [], "existing_buffers": []})
        entry["sinks"].extend(sinks)
        for b in buffers:
            if b not in entry["existing_buffers"]:
                entry["existing_buffers"].append(b)
    return roots


def _walk_to_root(
    design: DesignObject,
    net: str,
    memo: Dict[str, Optional[Tuple[str, Tuple[str, ...]]]],
) -> Optional[Tuple[str, Tuple[str, ...]]]:
    chain: List[str] = []
    path: List[str] = []
    cur = net
    result: Optional[Tuple[str, Tuple[str, ...]]] = None
    while True:
        if cur in memo:
            cached = memo[cur]
            result = None if cached is None else (cached[0], tuple(chain) + cached[1])
            break
        if cur in path:
            break
        path.append(cur)
        driver = design.nets.get(cur, {}).get("driver")
        if not driver:
            result = (cur, tuple(chain))
            break
        d_inst = driver[0]
        if str(d_inst).startswith("PORT:") or not _is_buffer_like(design, d_inst):
            result = (cur, tuple(chain))
            break
        pins = _lib_cell(design, d_inst).get("pins", {})
        in_net = next(
            (
                n
                for p, n in design.cells[d_inst].get("pins", {}).items()
                if pins.get(p, {}).get("direction") == "input"
            ),
            None,
        )
        if in_net is None or in_net == cur:
            result = (cur, tuple(chain))
            break
        chain.append(d_inst)
        cur = in_net

    for depth, visited in enumerate(path):
        memo[visited] = None if result is None else (result[0], result[1][depth:])
    return result


class HTreeClockOpt(ClockOptAlgorithm):
    """TritonCTS-inspired recursive median clustering with bottom-up buffers."""

    def execute(self, design: DesignObject, config: Dict[str, Any]) -> Dict[str, Any]:
        cts_cfg = config.get("cts", {})
        max_fanout = max(2, int(cts_cfg.get("max_fanout", 16)))
        max_levels = max(1, int(cts_cfg.get("max_levels", 10)))
        buf_type = cts_cfg.get("buffer_cell") or self._pick_buffer(design)
        buf_lib = design.library.get("cells", {}).get(buf_type, {})
        buf_w = float(buf_lib.get("width", 1.38))
        buf_h = float(buf_lib.get("height", 2.72))
        out_pin = next(
            (p for p, i in buf_lib.get("pins", {}).items() if i.get("direction") == "output"),
            "X",
        )
        in_pin = next(
            (
                p
                for p, i in buf_lib.get("pins", {}).items()
                if i.get("direction") == "input" and not i.get("is_clock")
            ),
            "A",
        )

        roots = find_clock_roots(design)
        new_buffers: Dict[str, Any] = {}
        clock_out: Dict[str, Any] = {}
        if not roots:
            return {"new_buffers": {}, "clock_nets": {}}

        place_cfg = config.get("placement", {})
        row_h = max(
            float(place_cfg.get("row_height_um", design.tech.get("site_height_um", 2.72))),
            buf_h,
        )
        spacing = float(place_cfg.get("cell_spacing_um", 0.01))
        legalizer = RowLegalizer.from_instances(
            design.instances, design.die_area, row_h, spacing=spacing
        )

        for ci, (clk, info) in enumerate(sorted(roots.items())):
            sinks = [(i, p) for i, p in info["sinks"] if i in design.instances]
            existing = info["existing_buffers"]
            if existing or not sinks:
                clock_out[clk] = {
                    "root": existing[-1] if existing else None,
                    "levels": [],
                    "sinks": {clk: [i for i, _p in sinks]},
                    "pre_existing_buffers": len(existing),
                    "tree_depth": 0,
                }
                continue

            # Coordinates for sinks (and later for inserted buffers)
            xy: Dict[Node, Tuple[float, float]] = {
                (inst, pin): (
                    float(design.instances[inst]["x"])
                    + 0.5 * float(design.instances[inst].get("width", 0.0)),
                    float(design.instances[inst]["y"])
                    + 0.5 * float(design.instances[inst].get("height", 0.0)),
                )
                for inst, pin in sinks
            }

            # Bottom-up: leaf clusters → parent buffers → single root
            current: List[Node] = list(sinks)
            levels: List[dict] = []
            assignments: Dict[str, List[str]] = {}
            level = 0
            root_name: Optional[str] = None

            while True:
                clusters = _cluster_nodes(current, xy, max_fanout, max_levels)
                # If everything fits under one driver and we're at top, attach to clk
                if len(clusters) == 1 and (len(current) <= max_fanout or level >= max_levels - 1):
                    members = clusters[0]["nodes"]
                    cx, cy = clusters[0]["xy"]
                    root_name = f"CTS_ROOT_{ci}"
                    mid_net = f"{clk}_cts_root"
                    rx, ry = legalizer.reserve(buf_w, cx, cy)
                    self._add_buffer(
                        design,
                        root_name,
                        buf_type,
                        rx,
                        ry,
                        buf_w,
                        buf_h,
                        in_pin,
                        clk,
                        out_pin,
                        mid_net,
                    )
                    new_buffers[root_name] = {
                        "cell_type": buf_type,
                        "x": rx,
                        "y": ry,
                        "orientation": "N",
                        "drives": mid_net,
                        "level": level,
                    }
                    levels.append(
                        {"buffer": root_name, "net": mid_net, "xy": (rx, ry), "level": level}
                    )
                    sink_names: List[str] = []
                    for node in members:
                        if isinstance(node, tuple):
                            inst, pin = node
                            _rewire_pin(design, clk, mid_net, inst, pin)
                            sink_names.append(inst)
                        else:
                            # Parent of an intermediate buffer: rewire its input to mid_net
                            _rewire_buffer_input(design, node, in_pin, mid_net)
                            sink_names.append(node)
                    assignments[mid_net] = sink_names
                    break

                # Insert one buffer per cluster; those become next level's nodes
                next_nodes: List[Node] = []
                for li, cluster in enumerate(clusters):
                    members = cluster["nodes"]
                    cx, cy = cluster["xy"]
                    bname = f"CTS_{ci}_L{level}_{li}"
                    net_out = f"{clk}_cts_L{level}_{li}"
                    bx, by = legalizer.reserve(buf_w, cx, cy)
                    # Input net is temporary; parent level will rewire it
                    temp_in = f"{clk}_cts_tmp_L{level}_{li}"
                    self._add_buffer(
                        design,
                        bname,
                        buf_type,
                        bx,
                        by,
                        buf_w,
                        buf_h,
                        in_pin,
                        temp_in,
                        out_pin,
                        net_out,
                    )
                    new_buffers[bname] = {
                        "cell_type": buf_type,
                        "x": bx,
                        "y": by,
                        "orientation": "N",
                        "drives": net_out,
                        "level": level,
                    }
                    levels.append(
                        {"buffer": bname, "net": net_out, "xy": (bx, by), "level": level}
                    )
                    sink_names = []
                    for node in members:
                        if isinstance(node, tuple):
                            inst, pin = node
                            _rewire_pin(design, clk, net_out, inst, pin)
                            sink_names.append(inst)
                        else:
                            _rewire_buffer_input(design, node, in_pin, net_out)
                            sink_names.append(node)
                    assignments[net_out] = sink_names
                    xy[bname] = (bx + 0.5 * buf_w, by + 0.5 * buf_h)
                    next_nodes.append(bname)

                current = next_nodes
                level += 1
                if level >= max_levels:
                    # Force a root over remaining nodes
                    cx = sum(xy[n][0] for n in current) / len(current)
                    cy = sum(xy[n][1] for n in current) / len(current)
                    root_name = f"CTS_ROOT_{ci}"
                    mid_net = f"{clk}_cts_root"
                    rx, ry = legalizer.reserve(buf_w, cx, cy)
                    self._add_buffer(
                        design,
                        root_name,
                        buf_type,
                        rx,
                        ry,
                        buf_w,
                        buf_h,
                        in_pin,
                        clk,
                        out_pin,
                        mid_net,
                    )
                    new_buffers[root_name] = {
                        "cell_type": buf_type,
                        "x": rx,
                        "y": ry,
                        "orientation": "N",
                        "drives": mid_net,
                        "level": level,
                    }
                    levels.append(
                        {"buffer": root_name, "net": mid_net, "xy": (rx, ry), "level": level}
                    )
                    for node in current:
                        _rewire_buffer_input(design, str(node), in_pin, mid_net)
                    assignments[mid_net] = [str(n) for n in current]
                    break

            clock_out[clk] = {
                "root": root_name,
                "levels": levels,
                "sinks": assignments,
                "pre_existing_buffers": 0,
                "max_fanout": max_fanout,
                "tree_depth": level + 1,
                "num_buffers": sum(
                    1 for k in new_buffers if k.startswith(f"CTS_{ci}_") or k == f"CTS_ROOT_{ci}"
                ),
            }

        design.die_area = legalizer.die_area
        return {"new_buffers": new_buffers, "clock_nets": clock_out}

    @staticmethod
    def _pick_buffer(design: DesignObject) -> str:
        buf_type = design.tech.get("default_clock_buffer", "sky130_fd_sc_hd__clkbuf_4")
        if buf_type in design.library.get("cells", {}):
            return buf_type
        for name in design.library.get("cells", {}):
            if "clkbuf" in name or name.endswith("__buf_2"):
                return name
        return buf_type

    @staticmethod
    def _add_buffer(
        design: DesignObject,
        name: str,
        buf_type: str,
        x: float,
        y: float,
        width: float,
        height: float,
        in_pin: str,
        in_net: str,
        out_pin: str,
        out_net: str,
    ) -> None:
        design.instances[name] = {
            "x": float(x),
            "y": float(y),
            "orientation": "N",
            "is_fixed": True,
            "width": width,
            "height": height,
        }
        design.cells[name] = {"cell_type": buf_type, "pins": {in_pin: in_net, out_pin: out_net}}
        _add_pin(design, in_net, name, in_pin)
        _add_pin(design, out_net, name, out_pin)


def _cluster_nodes(
    nodes: List[Node],
    xy: Dict[Node, Tuple[float, float]],
    max_fanout: int,
    max_levels: int,
) -> List[Dict[str, Any]]:
    """Recursive median bipartition until each cluster has ≤ max_fanout nodes."""

    def centroid(group: List[Node]) -> Tuple[float, float]:
        xs = [xy[s][0] for s in group]
        ys = [xy[s][1] for s in group]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def split(group: List[Node], depth: int) -> List[Dict[str, Any]]:
        if len(group) <= max_fanout or depth >= max_levels:
            cx, cy = centroid(group)
            return [{"nodes": group, "xy": (cx, cy)}]
        axis = depth % 2
        ordered = sorted(group, key=lambda s: xy[s][axis])
        mid = len(ordered) // 2
        if mid == 0 or mid == len(ordered):
            cx, cy = centroid(group)
            return [{"nodes": group, "xy": (cx, cy)}]
        return split(ordered[:mid], depth + 1) + split(ordered[mid:], depth + 1)

    if not nodes:
        return []
    return split(nodes, 1)


def _ensure_net(design: DesignObject, net: str) -> dict:
    return design.nets.setdefault(net, {"pins": [], "drivers": [], "driver": None})


def _add_pin(design: DesignObject, net: str, inst: str, pin: str) -> None:
    ninfo = _ensure_net(design, net)
    if (inst, pin) not in ninfo["pins"]:
        ninfo["pins"].append((inst, pin))


def _rewire_pin(design: DesignObject, old_net: str, new_net: str, inst: str, pin: str) -> None:
    old = design.nets.get(old_net)
    if old is not None:
        old["pins"] = [p for p in old["pins"] if tuple(p) != (inst, pin)]
    if inst in design.cells:
        design.cells[inst].setdefault("pins", {})[pin] = new_net
    _add_pin(design, new_net, inst, pin)


def _rewire_buffer_input(design: DesignObject, buf: str, in_pin: str, new_net: str) -> None:
    """Point a CTS buffer's input pin at ``new_net``, removing any prior attachment."""
    cell = design.cells.get(buf)
    if cell is None:
        return
    pins = cell.setdefault("pins", {})
    old_net = pins.get(in_pin)
    if old_net and old_net in design.nets:
        design.nets[old_net]["pins"] = [
            p for p in design.nets[old_net]["pins"] if tuple(p) != (buf, in_pin)
        ]
        if (
            not design.nets[old_net]["pins"]
            and not design.nets[old_net].get("driver")
            and "_cts_tmp_" in str(old_net)
        ):
            del design.nets[old_net]
    pins[in_pin] = new_net
    _add_pin(design, new_net, buf, in_pin)
