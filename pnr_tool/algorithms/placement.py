"""RePlAce-like global placement + OpenDP/Abacus detailed legalization."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

from pnr_tool.algorithms.base import PlacementAlgorithm
from pnr_tool.algorithms.floorplan import die_is_placeholder, estimate_die_area
from pnr_tool.design.object import DesignObject


class ForceDirectedPlacement(PlacementAlgorithm):
    """Default placer: density+WL global place (continuous), then Abacus DP."""

    def execute(self, design: DesignObject, config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        place_cfg = config.get("placement", {})
        iterations = int(place_cfg.get("iterations", 60))
        row_h = float(place_cfg.get("row_height_um", design.tech.get("site_height_um", 2.72)))
        spacing = float(place_cfg.get("cell_spacing_um", 0.01))
        seed = int(place_cfg.get("seed", 42))
        density_bins = max(4, int(place_cfg.get("density_bins", 24)))
        wl_w = float(place_cfg.get("wirelength_weight", 1.0))
        dens_w = float(place_cfg.get("density_weight", 0.8))
        overflow_pow = float(place_cfg.get("density_overflow_power", 2.0))
        nesterov = float(place_cfg.get("nesterov_decay", 0.85))
        max_step = float(place_cfg.get("max_step_um", 2.0))
        io_pull = float(place_cfg.get("io_pull", 0.01))

        cells = list(design.cells.keys())
        n = len(cells)
        if n == 0:
            return {}

        if die_is_placeholder(design.die_area):
            estimate_die_area(design, config)

        lib_cells = design.library.get("cells", {})
        widths = np.empty(n)
        heights = np.empty(n)
        for i, c in enumerate(cells):
            lib = lib_cells.get(design.cells[c]["cell_type"], {})
            widths[i] = float(lib.get("width", 1.38))
            heights[i] = float(lib.get("height", row_h))

        row_h = max(row_h, float(np.max(heights)))
        minx, miny, maxx, maxy = (float(v) for v in design.die_area)
        die_w = max(maxx - minx, float(np.max(widths)) + 2 * spacing)
        die_h = max(maxy - miny, row_h)
        design.die_area = (minx, miny, minx + die_w, miny + die_h)
        rows = max(1, int(round(die_h / row_h)))

        # Uniform rectangular seed + soft targets (not center blob).
        rng = np.random.default_rng(seed)
        cols = max(1, int(np.ceil(np.sqrt(n * die_w / max(die_h, 1e-9)))))
        grid_rows = max(1, int(np.ceil(n / cols)))
        xs = np.empty(n)
        ys = np.empty(n)
        slot_x = np.empty(n)
        slot_y = np.empty(n)
        for i in range(n):
            gx = i % cols
            gy = i // cols
            slot_x[i] = minx + (gx + 0.5) * (die_w / cols)
            slot_y[i] = miny + (gy + 0.5) * (die_h / grid_rows)
            xs[i] = slot_x[i] + rng.normal(0, die_w * 0.01)
            ys[i] = slot_y[i] + rng.normal(0, die_h * 0.01)
        xs = np.clip(xs, minx, minx + die_w)
        ys = np.clip(ys, miny, miny + die_h)
        rect_pull = float(place_cfg.get("rect_pull", 0.04))

        vx = np.zeros(n)
        vy = np.zeros(n)

        member_idx, net_id, num_nets = _net_incidence(design, cells)
        if num_nets:
            net_counts = np.bincount(net_id, minlength=num_nets).astype(float)
            net_counts[net_counts == 0] = 1.0

        io_targets = _io_targets(design, cells, minx, miny, die_w, die_h) if io_pull > 0 else None
        areas = widths * heights
        target_dens = float(np.sum(areas)) / max(die_w * die_h, 1e-9)

        for it in range(iterations):
            # Wirelength (WA/HPWL centroid proxy) — damp late so density can fill box.
            wl_scale = wl_w * (1.0 - 0.55 * (it / max(iterations - 1, 1)))
            if num_nets:
                mx = xs[member_idx]
                my = ys[member_idx]
                cx = np.bincount(net_id, weights=mx, minlength=num_nets) / net_counts
                cy = np.bincount(net_id, weights=my, minlength=num_nets) / net_counts
                fx = np.bincount(member_idx, weights=0.2 * (cx[net_id] - mx), minlength=n)
                fy = np.bincount(member_idx, weights=0.2 * (cy[net_id] - my), minlength=n)
            else:
                fx = np.zeros(n)
                fy = np.zeros(n)
            fx *= wl_scale
            fy *= wl_scale

            # Density: overflow repulsion + underfill attraction (rectangular fill).
            dens_scale = dens_w * (0.4 + 0.6 * (it + 1) / max(iterations, 1))
            dfx, dfy = _density_forces(
                xs, ys, areas, minx, miny, die_w, die_h, density_bins, target_dens, overflow_pow
            )
            fx += dens_scale * dfx
            fy += dens_scale * dfy

            if io_targets is not None:
                mask = np.isfinite(io_targets[:, 0])
                fx[mask] += io_pull * (io_targets[mask, 0] - xs[mask])
                fy[mask] += io_pull * (io_targets[mask, 1] - ys[mask])

            # Soft pull toward uniform rectangular slots — kills diamond/side clump.
            if rect_pull > 0:
                fx += rect_pull * (slot_x - xs)
                fy += rect_pull * (slot_y - ys)

            # Nesterov update with step cap (continuous — no row snap here).
            vx = nesterov * vx + fx
            vy = nesterov * vy + fy
            step_x = np.clip(vx, -max_step, max_step)
            step_y = np.clip(vy, -max_step, max_step)
            xs = np.clip(xs + step_x, minx, minx + die_w - widths * 0.5)
            ys = np.clip(ys + step_y, miny, miny + die_h - row_h * 0.5)

        # Detailed: snap Y once, then Abacus-style pack per row.
        ys = np.clip(np.round((ys - miny) / row_h) * row_h + miny, miny, miny + (rows - 1) * row_h)
        xs, ys = _abacus_legalize(xs, ys, widths, minx, miny, die_w, die_h, row_h, spacing, rows)

        design.die_area = (minx, miny, minx + die_w, miny + die_h)
        design.meta["die_expanded_by_legalizer"] = False

        instances: Dict[str, Dict[str, Any]] = {}
        for i, c in enumerate(cells):
            instances[c] = {
                "x": float(xs[i]),
                "y": float(ys[i]),
                "orientation": "N",
                "is_fixed": False,
                "width": float(widths[i]),
                "height": float(heights[i]),
            }
        return instances


def _abacus_legalize(
    xs: np.ndarray,
    ys: np.ndarray,
    widths: np.ndarray,
    minx: float,
    miny: float,
    die_w: float,
    die_h: float,
    row_h: float,
    spacing: float,
    rows: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """OpenDP/Abacus-inspired: balance rows, then left-to-right pack by global-x."""
    n = len(xs)
    out_x = xs.copy()
    out_y = ys.copy()
    row_of = np.clip(np.round((ys - miny) / row_h).astype(int), 0, rows - 1)
    maxx = minx + die_w

    # Bucket cells per row (preserve global-x order inside each row)
    buckets: List[List[int]] = [[] for _ in range(rows)]
    for i in np.argsort(xs):
        buckets[int(row_of[i])].append(int(i))

    def row_width(idxs: List[int]) -> float:
        return float(sum(float(widths[j]) + spacing for j in idxs))

    # Spill overflow from full rows to nearest neighbor with capacity
    for _pass in range(rows * 2):
        moved = False
        for r in range(rows):
            while row_width(buckets[r]) > die_w + 1e-9 and len(buckets[r]) > 1:
                # Move the rightmost cell to the nearest row with space
                victim = buckets[r].pop()  # rightmost (list is x-sorted)
                placed = False
                for delta in range(1, rows):
                    for cand in (r - delta, r + delta):
                        if cand < 0 or cand >= rows:
                            continue
                        if row_width(buckets[cand]) + float(widths[victim]) + spacing <= die_w + 1e-9:
                            # insert keeping x order
                            bx = float(xs[victim])
                            ins = 0
                            while ins < len(buckets[cand]) and float(xs[buckets[cand][ins]]) <= bx:
                                ins += 1
                            buckets[cand].insert(ins, victim)
                            placed = True
                            moved = True
                            break
                    if placed:
                        break
                if not placed:
                    buckets[r].append(victim)
                    break
        if not moved:
            break

    # Abacus pack left→right, then stretch free space so the row fills the die
    # width (rectangular core / corner occupancy instead of a side clump).
    for r, idxs in enumerate(buckets):
        y = miny + r * row_h
        if not idxs:
            continue
        total = sum(float(widths[i]) for i in idxs) + spacing * max(0, len(idxs) - 1)
        slack = max(0.0, die_w - total)
        gap = spacing + (slack / max(len(idxs) - 1, 1) if len(idxs) > 1 else 0.0)
        cursor = minx
        for k, i in enumerate(idxs):
            w = float(widths[i])
            place_x = cursor
            if place_x + w > maxx + 1e-9:
                place_x = max(minx, maxx - w)
            out_x[i] = place_x
            out_y[i] = y
            cursor = place_x + w + (gap if k < len(idxs) - 1 else spacing)

    return out_x, out_y


def _net_incidence(design: DesignObject, cells: List[str]):
    idx = {c: i for i, c in enumerate(cells)}
    members: List[int] = []
    net_ids: List[int] = []
    num_nets = 0
    for ninfo in design.nets.values():
        seen: set[int] = set()
        for inst, _pin in ninfo.get("pins", []):
            i = idx.get(inst)
            if i is not None:
                seen.add(i)
        if len(seen) >= 2:
            members.extend(seen)
            net_ids.extend([num_nets] * len(seen))
            num_nets += 1
    if not members:
        return np.zeros(0, dtype=np.intp), np.zeros(0, dtype=np.intp), 0
    return (
        np.asarray(members, dtype=np.intp),
        np.asarray(net_ids, dtype=np.intp),
        num_nets,
    )


def _density_forces(
    xs: np.ndarray,
    ys: np.ndarray,
    areas: np.ndarray,
    minx: float,
    miny: float,
    die_w: float,
    die_h: float,
    bins: int,
    target_dens: float,
    overflow_pow: float = 2.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Overflow repulsion + underfill attraction for rectangular core fill."""
    n = len(xs)
    fx = np.zeros(n)
    fy = np.zeros(n)
    if n == 0 or die_w <= 0 or die_h <= 0:
        return fx, fy

    bx = np.clip(((xs - minx) / die_w * bins).astype(int), 0, bins - 1)
    by = np.clip(((ys - miny) / die_h * bins).astype(int), 0, bins - 1)
    bin_area = (die_w / bins) * (die_h / bins)
    dens = np.zeros((bins, bins))
    np.add.at(dens, (bx, by), areas)

    target = max(bin_area * max(target_dens, 1e-9), 1e-12)
    ratio = dens / target
    overflow = np.clip(ratio - 1.0, 0.0, 5.0) ** overflow_pow
    underfill = np.clip(1.0 - ratio, 0.0, 1.0)

    # Repel from own overcrowded bin center
    cx = minx + (bx + 0.5) * (die_w / bins)
    cy = miny + (by + 0.5) * (die_h / bins)
    dx = xs - cx
    dy = ys - cy
    mag = np.maximum(np.hypot(dx, dy), 1e-6)
    scale = overflow[bx, by]
    fx += scale * dx / mag
    fy += scale * dy / mag

    # Neighbor overflow push
    for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nbx = np.clip(bx + ox, 0, bins - 1)
        nby = np.clip(by + oy, 0, bins - 1)
        n_ov = overflow[nbx, nby]
        fx += n_ov * (-ox) * 0.25
        fy += n_ov * (-oy) * 0.25

    # Attract cells in dense bins toward the most underfilled bins (rect fill).
    # Each cell gets a pull toward the nearest underfilled bin center.
    uf_idx = np.argwhere(underfill > 0.15)
    if len(uf_idx):
        uf_cx = minx + (uf_idx[:, 0] + 0.5) * (die_w / bins)
        uf_cy = miny + (uf_idx[:, 1] + 0.5) * (die_h / bins)
        # For cells in overflow bins, pull toward closest underfilled bin
        over_mask = scale > 0.05
        if np.any(over_mask):
            oxs = xs[over_mask]
            oys = ys[over_mask]
            # Broadcast distances to underfilled bins
            ddx = uf_cx[None, :] - oxs[:, None]
            ddy = uf_cy[None, :] - oys[:, None]
            dist2 = ddx * ddx + ddy * ddy
            nearest = np.argmin(dist2, axis=1)
            pull_x = uf_cx[nearest] - oxs
            pull_y = uf_cy[nearest] - oys
            pm = np.maximum(np.hypot(pull_x, pull_y), 1e-6)
            strength = 0.35 * scale[over_mask]
            fx[over_mask] += strength * pull_x / pm
            fy[over_mask] += strength * pull_y / pm

    return fx, fy


def _io_targets(
    design: DesignObject,
    cells: List[str],
    minx: float,
    miny: float,
    die_w: float,
    die_h: float,
) -> np.ndarray:
    idx = {c: i for i, c in enumerate(cells)}
    targets = np.full((len(cells), 2), np.nan)
    sums = np.zeros((len(cells), 2))
    counts = np.zeros(len(cells))
    ports = list(design.ports.keys())
    if not ports:
        return targets

    for pi, pname in enumerate(ports):
        pinfo = design.ports.get(pname, {})
        if "x" in pinfo and "y" in pinfo:
            tx, ty = float(pinfo["x"]), float(pinfo["y"])
        else:
            edge = pi % 4
            t = ((pi // 4) % max(len(ports), 1)) / max(len(ports), 1)
            if edge == 0:
                tx, ty = minx + die_w * t, miny
            elif edge == 1:
                tx, ty = minx + die_w, miny + die_h * t
            elif edge == 2:
                tx, ty = minx + die_w * t, miny + die_h
            else:
                tx, ty = minx, miny + die_h * t
        ninfo = design.nets.get(pname)
        if not ninfo:
            continue
        for inst, _pin in ninfo.get("pins", []):
            if str(inst).startswith("PORT:"):
                continue
            i = idx.get(inst)
            if i is None:
                continue
            sums[i, 0] += tx
            sums[i, 1] += ty
            counts[i] += 1.0

    mask = counts > 0
    targets[mask, 0] = sums[mask, 0] / counts[mask]
    targets[mask, 1] = sums[mask, 1] / counts[mask]
    return targets
