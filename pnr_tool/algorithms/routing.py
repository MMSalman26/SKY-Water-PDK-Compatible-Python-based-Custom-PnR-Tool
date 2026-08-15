"""FastRoute-inspired global router: pattern route, then overflow maze repair."""

from __future__ import annotations

import heapq
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from pnr_tool.algorithms.base import RoutingAlgorithm
from pnr_tool.algorithms.detail_route import add_pin_stubs
from pnr_tool.algorithms.floorplan import assign_port_positions
from pnr_tool.algorithms.pins import instance_pin_xy, power_blocked_grid_edges
from pnr_tool.design.object import DesignObject

GridPos = Tuple[int, int]
Run = Tuple[GridPos, GridPos, bool]  # start, end, is_horizontal

_NEAREST_CHAIN_LIMIT = 64


def _direction_layers(
    design: DesignObject, cfg: Dict[str, Any], plural_key: str, single_key: str, direction: str
) -> List[str]:
    tech_layers = design.tech.get("layers", {})
    named = cfg.get(plural_key)
    if isinstance(named, (list, tuple)) and named:
        chosen = [str(n) for n in named if str(n) in tech_layers]
        if chosen:
            return chosen
    single = cfg.get(single_key)
    from_tech = [
        name
        for name, info in tech_layers.items()
        if info.get("direction") == direction and name != "li1"
    ]
    if single and str(single) in tech_layers:
        ordered = [str(single)] + [n for n in from_tech if n != str(single)]
        return ordered
    return from_tech or [str(cfg.get("default_layer", "met2"))]


class GlobalRouter(RoutingAlgorithm):
    def execute(self, design: DesignObject, config: Dict[str, Any]) -> Dict[str, Any]:
        rcfg = config.get("routing", {})
        pitch = float(rcfg.get("grid_pitch_um", 2.0))
        step_budget = int(rcfg.get("step_budget", 50000))
        weight = float(rcfg.get("congestion_weight", 3.0))
        pattern_routing = bool(rcfg.get("pattern_routing", True))
        overflow_passes = max(0, int(rcfg.get("overflow_passes", 3)))
        h_layers = _direction_layers(
            design, rcfg, "horizontal_layers", "horizontal_layer", "horizontal"
        )
        v_layers = _direction_layers(design, rcfg, "vertical_layers", "vertical_layer", "vertical")

        if not design.meta.get("port_positions"):
            assign_port_positions(design)
        port_xy = design.meta.get("port_positions", {})

        minx, miny, maxx, maxy = design.die_area
        gw = max(1, int((maxx - minx) / pitch) + 1)
        gh = max(1, int((maxy - miny) / pitch) + 1)
        gh_edges = max(gh - 1, 1)

        def to_grid(x: float, y: float) -> GridPos:
            gx = int(min(max((x - minx) / pitch, 0), gw - 1))
            gy = int(min(max((y - miny) / pitch, 0), gh - 1))
            return gx, gy

        def to_um(g: GridPos) -> Tuple[float, float]:
            return minx + g[0] * pitch, miny + g[1] * pitch

        n_h = max(gw - 1, 1) * gh
        n_v = gw * gh_edges
        occ_h = [[0] * n_h for _ in h_layers]
        occ_v = [[0] * n_v for _ in v_layers]
        total_h = [0] * n_h
        total_v = [0] * n_v

        blocked_h: set = set()
        blocked_v: set = set()
        if rcfg.get("avoid_power", True) and design.power_grid:
            blocked_h, blocked_v = power_blocked_grid_edges(design, pitch, gw, gh, gh_edges)

        routing: Dict[str, Any] = {}
        meta_flags: List[str] = []
        # Remember which nets used which edges for rip-up
        net_edges: Dict[str, Tuple[List[Tuple[bool, int]], List[GridPos]]] = {}

        # Prefer short 2-pin nets first (FastRoute-style easy nets before hard).
        ordered_nets: List[Tuple[int, float, str, List[GridPos]]] = []
        for net, ninfo in design.nets.items():
            terminals = self._net_terminals(design, ninfo, port_xy, to_grid)
            if len(terminals) < 2:
                continue
            xs = [t[0] for t in terminals]
            ys = [t[1] for t in terminals]
            span = float((max(xs) - min(xs)) + (max(ys) - min(ys)))
            ordered_nets.append((len(terminals), span, net, terminals))
        ordered_nets.sort(key=lambda item: (item[0], item[1], item[2]))

        cap_h = len(h_layers)
        cap_v = len(v_layers)

        # --- Pass 1: FastRoute-style pattern routing (L / Z) ---
        for _npins, _span, net, terminals in ordered_nets:
            segs: List[dict] = []
            all_edges: List[Tuple[bool, int]] = []
            source = terminals[0]
            full_path: List[GridPos] = [source]
            for sink in terminals[1:]:
                path: Optional[List[GridPos]] = None
                if pattern_routing:
                    path = self._pattern_route(source, sink, blocked_h, blocked_v, gh, gh_edges)
                if path is None:
                    path = self._route_one(
                        source,
                        sink,
                        gw,
                        gh,
                        gh_edges,
                        total_h,
                        total_v,
                        cap_h,
                        cap_v,
                        step_budget,
                        weight,
                        blocked_h,
                        blocked_v,
                    )
                if path is None:
                    path = self._manhattan(source, sink)
                    meta_flags.append(net)
                committed, edges = _commit_path(
                    path,
                    to_um,
                    gh,
                    gh_edges,
                    h_layers,
                    v_layers,
                    occ_h,
                    occ_v,
                    total_h,
                    total_v,
                )
                for s in committed:
                    s["role"] = "global"
                segs.extend(committed)
                all_edges.extend(edges)
                full_path.extend(path[1:])
                source = sink
            routing[net] = segs
            net_edges[net] = (all_edges, terminals)

        # --- Overflow repair: rip-up & maze-reroute congested nets ---
        for _pass in range(overflow_passes):
            overflow_h = {e for e, u in enumerate(total_h) if u > cap_h}
            overflow_v = {e for e, u in enumerate(total_v) if u > cap_v}
            if not overflow_h and not overflow_v:
                break
            victims = [
                net
                for net, (edges, _terms) in net_edges.items()
                if any((h and eid in overflow_h) or ((not h) and eid in overflow_v) for h, eid in edges)
            ]
            # Rip up longest victims first
            victims.sort(key=lambda n: -len(net_edges[n][0]))
            for net in victims[: max(1, len(victims) // 2 + 1)]:
                edges, terminals = net_edges[net]
                _rip_edges(edges, occ_h, occ_v, total_h, total_v)
                segs = []
                all_edges: List[Tuple[bool, int]] = []
                source = terminals[0]
                used_fallback = False
                for sink in terminals[1:]:
                    path = self._route_one(
                        source,
                        sink,
                        gw,
                        gh,
                        gh_edges,
                        total_h,
                        total_v,
                        cap_h,
                        cap_v,
                        step_budget,
                        weight * 1.5,
                        blocked_h,
                        blocked_v,
                    )
                    if path is None:
                        path = self._manhattan(source, sink)
                        used_fallback = True
                    committed, new_edges = _commit_path(
                        path,
                        to_um,
                        gh,
                        gh_edges,
                        h_layers,
                        v_layers,
                        occ_h,
                        occ_v,
                        total_h,
                        total_v,
                    )
                    for s in committed:
                        s["role"] = "global"
                    segs.extend(committed)
                    all_edges.extend(new_edges)
                    source = sink
                routing[net] = segs
                net_edges[net] = (all_edges, terminals)
                if used_fallback and net not in meta_flags:
                    meta_flags.append(net)

        routing = add_pin_stubs(design, routing, config)
        design.meta["routing_fallbacks"] = meta_flags
        design.meta["routing_overflow_h"] = sum(1 for u in total_h if u > cap_h)
        design.meta["routing_overflow_v"] = sum(1 for u in total_v if u > cap_v)
        return routing

    @staticmethod
    def _pattern_route(
        src: GridPos,
        dst: GridPos,
        blocked_h: Set[int],
        blocked_v: Set[int],
        gh: int,
        gh_edges: int,
    ) -> Optional[List[GridPos]]:
        """Try L-shape then Z-shape patterns; return None if blocked."""
        if src == dst:
            return [src]
        candidates = [
            _l_path(src, dst, bend_h_first=True),
            _l_path(src, dst, bend_h_first=False),
        ]
        # Z / U via midpoints + offset bends for PDN dodge
        mx = (src[0] + dst[0]) // 2
        my = (src[1] + dst[1]) // 2
        candidates.append(_via_point_path(src, (mx, src[1]), (mx, dst[1]), dst))
        candidates.append(_via_point_path(src, (src[0], my), (dst[0], my), dst))
        for off in (1, 2, -1, -2):
            candidates.append(
                _via_point_path(src, (mx + off, src[1]), (mx + off, dst[1]), dst)
            )
            candidates.append(
                _via_point_path(src, (src[0], my + off), (dst[0], my + off), dst)
            )

        for path in candidates:
            if path and _path_clear(path, blocked_h, blocked_v, gh, gh_edges):
                return path
        return None

    @staticmethod
    def _net_terminals(
        design: DesignObject,
        ninfo: Dict[str, Any],
        port_xy: Dict[str, Any],
        to_grid: Callable[[float, float], GridPos],
    ) -> List[GridPos]:
        driver = ninfo.get("driver")
        ordered: List[Tuple[str, str]] = list(ninfo.get("pins", []))
        if driver is not None:
            key = tuple(driver)
            ordered.sort(key=lambda p: 0 if tuple(p) == key else 1)

        terminals: List[GridPos] = []
        for inst, pin in ordered:
            xy = instance_pin_xy(design, inst, pin)
            if xy is None:
                if inst.startswith("PORT:"):
                    pos = port_xy.get(inst.split(":", 1)[1])
                    if pos is None:
                        continue
                    xy = (float(pos[0]), float(pos[1]))
                else:
                    placed = design.instances.get(inst)
                    if placed is None:
                        continue
                    xy = (float(placed["x"]), float(placed["y"]))
            terminals.append(to_grid(float(xy[0]), float(xy[1])))
        terminals = list(dict.fromkeys(terminals))
        return GlobalRouter._nearest_chain(terminals)

    @staticmethod
    def _nearest_chain(terminals: List[GridPos]) -> List[GridPos]:
        if len(terminals) < 3 or len(terminals) > _NEAREST_CHAIN_LIMIT:
            return terminals
        remaining = terminals[1:]
        chain = [terminals[0]]
        cur = terminals[0]
        while remaining:
            best_i = min(
                range(len(remaining)),
                key=lambda i: abs(remaining[i][0] - cur[0]) + abs(remaining[i][1] - cur[1]),
            )
            cur = remaining.pop(best_i)
            chain.append(cur)
        return chain

    def _route_one(
        self,
        src: GridPos,
        dst: GridPos,
        gw: int,
        gh: int,
        gh_edges: int,
        total_h: Sequence[int],
        total_v: Sequence[int],
        cap_h: int,
        cap_v: int,
        budget: int,
        weight: float,
        blocked_h: Optional[set] = None,
        blocked_v: Optional[set] = None,
    ) -> Optional[List[GridPos]]:
        if src == dst:
            return [src]
        blocked_h = blocked_h or set()
        blocked_v = blocked_v or set()
        gx_goal, gy_goal = dst
        pq: List[Tuple[float, float, GridPos]] = [
            (float(abs(src[0] - gx_goal) + abs(src[1] - gy_goal)), 0.0, src)
        ]
        best: Dict[GridPos, float] = {src: 0.0}
        prev: Dict[GridPos, Optional[GridPos]] = {src: None}
        steps = 0
        push = heapq.heappush
        pop = heapq.heappop
        while pq:
            _f, cost, cur = pop(pq)
            if cost > best.get(cur, 1e30):
                continue
            if cur == dst:
                break
            steps += 1
            if steps > budget:
                return None
            x, y = cur
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if nx < 0 or nx >= gw or ny < 0 or ny >= gh:
                    continue
                if dy == 0:
                    eid = min(x, nx) * gh + y
                    if eid in blocked_h:
                        continue
                    used = total_h[eid]
                    cap = cap_h
                else:
                    eid = x * gh_edges + min(y, ny)
                    if eid in blocked_v:
                        continue
                    used = total_v[eid]
                    cap = cap_v
                over = used - cap + 1
                # Soft PDN proximity: edges adjacent to blocked keep higher cost
                soft = 0.0
                if dy == 0 and (
                    (eid - 1) in blocked_h or (eid + 1) in blocked_h or eid in blocked_h
                ):
                    soft = weight * 0.35
                elif dy != 0 and (
                    (eid - 1) in blocked_v or (eid + 1) in blocked_v or eid in blocked_v
                ):
                    soft = weight * 0.35
                ncost = cost + 1.0 + 0.08 * used + soft + (weight * over if over > 0 else 0.0)
                nb = (nx, ny)
                if ncost < best.get(nb, 1e30):
                    best[nb] = ncost
                    prev[nb] = cur
                    push(pq, (ncost + abs(nx - gx_goal) + abs(ny - gy_goal), ncost, nb))
        if dst not in prev:
            return None
        path = [dst]
        while path[-1] != src:
            path.append(prev[path[-1]])  # type: ignore[arg-type]
        path.reverse()
        return path

    @staticmethod
    def _manhattan(src: GridPos, dst: GridPos) -> List[GridPos]:
        path = [src]
        x, y = src
        tx, ty = dst
        while x != tx:
            x += 1 if tx > x else -1
            path.append((x, y))
        while y != ty:
            y += 1 if ty > y else -1
            path.append((x, y))
        return path


def _l_path(src: GridPos, dst: GridPos, bend_h_first: bool) -> List[GridPos]:
    path = [src]
    x, y = src
    tx, ty = dst
    if bend_h_first:
        while x != tx:
            x += 1 if tx > x else -1
            path.append((x, y))
        while y != ty:
            y += 1 if ty > y else -1
            path.append((x, y))
    else:
        while y != ty:
            y += 1 if ty > y else -1
            path.append((x, y))
        while x != tx:
            x += 1 if tx > x else -1
            path.append((x, y))
    return path


def _via_point_path(a: GridPos, b: GridPos, c: GridPos, d: GridPos) -> List[GridPos]:
    path = _l_path(a, b, bend_h_first=True)
    path.extend(_l_path(b, c, bend_h_first=False)[1:])
    path.extend(_l_path(c, d, bend_h_first=True)[1:])
    return path


def _path_clear(
    path: List[GridPos],
    blocked_h: Set[int],
    blocked_v: Set[int],
    gh: int,
    gh_edges: int,
) -> bool:
    for i in range(len(path) - 1):
        x0, y0 = path[i]
        x1, y1 = path[i + 1]
        if y0 == y1:
            eid = min(x0, x1) * gh + y0
            if eid in blocked_h:
                return False
        else:
            eid = x0 * gh_edges + min(y0, y1)
            if eid in blocked_v:
                return False
    return True


def _path_runs(path: List[GridPos]) -> List[Run]:
    if len(path) < 2:
        return []
    runs: List[Run] = []
    start = path[0]
    prev = path[0]
    direction: Optional[Tuple[int, int]] = None
    for cur in path[1:]:
        d = (cur[0] - prev[0], cur[1] - prev[1])
        if direction is None:
            direction = d
        elif d != direction:
            runs.append((start, prev, direction[1] == 0))
            start = prev
            direction = d
        prev = cur
    if direction is not None:
        runs.append((start, prev, direction[1] == 0))
    return runs


def _run_edges(start: GridPos, end: GridPos, horizontal: bool, gh: int, gh_edges: int) -> List[int]:
    if horizontal:
        y = start[1]
        lo, hi = min(start[0], end[0]), max(start[0], end[0])
        return [x * gh + y for x in range(lo, hi)]
    x = start[0]
    lo, hi = min(start[1], end[1]), max(start[1], end[1])
    return [x * gh_edges + y for y in range(lo, hi)]


def _commit_path(
    path: List[GridPos],
    to_um: Callable[[GridPos], Tuple[float, float]],
    gh: int,
    gh_edges: int,
    h_layers: List[str],
    v_layers: List[str],
    occ_h: List[List[int]],
    occ_v: List[List[int]],
    total_h: List[int],
    total_v: List[int],
) -> Tuple[List[dict], List[Tuple[bool, int]]]:
    segs: List[dict] = []
    edges_out: List[Tuple[bool, int]] = []
    for start, end, horizontal in _path_runs(path):
        layers = h_layers if horizontal else v_layers
        per_layer = occ_h if horizontal else occ_v
        totals = total_h if horizontal else total_v
        edges = _run_edges(start, end, horizontal, gh, gh_edges)
        layer_idx = _pick_free_layer(edges, per_layer)
        for e in edges:
            per_layer[layer_idx][e] += 1
            totals[e] += 1
            edges_out.append((horizontal, e))
        x1, y1 = to_um(start)
        x2, y2 = to_um(end)
        segs.append({"layer": layers[layer_idx], "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    return segs, edges_out


def _rip_edges(
    edges: List[Tuple[bool, int]],
    occ_h: List[List[int]],
    occ_v: List[List[int]],
    total_h: List[int],
    total_v: List[int],
) -> None:
    for horizontal, eid in edges:
        per_layer = occ_h if horizontal else occ_v
        totals = total_h if horizontal else total_v
        # Decrement the layer with the highest occupancy on this edge.
        best = max(range(len(per_layer)), key=lambda i: per_layer[i][eid])
        if per_layer[best][eid] > 0:
            per_layer[best][eid] -= 1
        if totals[eid] > 0:
            totals[eid] -= 1


def _pick_free_layer(edges: List[int], per_layer: List[List[int]]) -> int:
    best_idx = 0
    best_load = None
    for idx, occ in enumerate(per_layer):
        load = max((occ[e] for e in edges), default=0)
        if load == 0:
            return idx
        if best_load is None or load < best_load:
            best_load = load
            best_idx = idx
    return best_idx
