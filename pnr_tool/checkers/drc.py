"""DRC: width-aware metal/via QoR ranker (Magic/KLayout-inspired, not signoff)."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from pnr_tool.design.object import DesignObject
from pnr_tool.spatial import create_spatial_index
from pnr_tool.spatial.index import boxes_overlap

Span = Tuple[str, float, float]
Point = Tuple[float, float]
BBox = Tuple[float, float, float, float]


@dataclass
class MetalRect:
    net: str
    layer: str
    bbox: BBox
    width_um: float
    kind: str = "wire"  # wire | via | obs | pin
    horizontal: Optional[bool] = None


def _hit(
    typ: str,
    rule: str,
    **kwargs: Any,
) -> dict:
    row = {"type": typ, "rule": rule}
    row.update(kwargs)
    return row


def _layer_width(tech: dict, layer: str, seg: Optional[dict] = None) -> float:
    if seg and seg.get("width_um"):
        return float(seg["width_um"])
    info = (tech.get("layers") or {}).get(layer, {})
    return float(info.get("width_um") or 0.0)


def _layer_spacing(tech: dict, layer: str, pitch: float) -> float:
    info = (tech.get("layers") or {}).get(layer, {})
    return float(info.get("min_spacing_um") or pitch * 0.5)


def _via_size(tech: dict, layer: str) -> float:
    info = (tech.get("layers") or {}).get(layer, {})
    return float(info.get("via_size_um") or tech.get("via_size_um") or 0.15)


def _enclosure(tech: dict, layer: str) -> float:
    info = (tech.get("layers") or {}).get(layer, {})
    return float(info.get("enclosure_um") or tech.get("enclosure_um") or 0.055)


def inflate_segment(seg: dict, width_um: float) -> BBox:
    """AABB of a centerline segment inflated to ``width_um`` (tiny slab if width=0)."""
    x1, y1 = float(seg["x1"]), float(seg["y1"])
    x2, y2 = float(seg["x2"]), float(seg["y2"])
    hw = max(float(width_um) * 0.5, 1e-6)
    if abs(y1 - y2) < 1e-9:
        return (min(x1, x2), y1 - hw, max(x1, x2), y1 + hw)
    if abs(x1 - x2) < 1e-9:
        return (x1 - hw, min(y1, y2), x1 + hw, max(y1, y2))
    return (min(x1, x2) - hw, min(y1, y2) - hw, max(x1, x2) + hw, max(y1, y2) + hw)


def edge_gap(a: BBox, b: BBox) -> float:
    """Signed edge-to-edge gap; negative means overlap."""
    ox = min(a[2], b[2]) - max(a[0], b[0])
    oy = min(a[3], b[3]) - max(a[1], b[1])
    if ox > 0 and oy > 0:
        return -min(ox, oy)
    if ox > 0:
        return max(a[1], b[1]) - min(a[3], b[3])
    if oy > 0:
        return max(a[0], b[0]) - min(a[2], b[2])
    dx = max(a[0], b[0]) - min(a[2], b[2])
    dy = max(a[1], b[1]) - min(a[3], b[3])
    return (dx * dx + dy * dy) ** 0.5


def _sweep_pairs(rects: Sequence[MetalRect], expand: float) -> Iterable[Tuple[int, int]]:
    """Yield index pairs whose bboxes overlap after expanding by ``expand``."""
    if len(rects) < 2:
        return
    index = create_spatial_index()
    grown: List[BBox] = []
    for i, r in enumerate(rects):
        b = r.bbox
        gb = (b[0] - expand, b[1] - expand, b[2] + expand, b[3] + expand)
        grown.append(gb)
        index.insert(i, gb)
    seen: Set[Tuple[int, int]] = set()
    for i, gb in enumerate(grown):
        for j in index.intersection(gb):
            if j <= i:
                continue
            key = (i, j)
            if key in seen:
                continue
            seen.add(key)
            yield i, j


def collect_drc_geometry(
    design: DesignObject, config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Inflated metals, inferred vias, and instance OBS (for CIF / DRC)."""
    config = config or {}
    tech = design.tech or {}
    wires: List[MetalRect] = []
    for net, segs in design.routing.items():
        for seg in segs:
            layer = str(seg.get("layer", "met2"))
            w = _layer_width(tech, layer, seg)
            bbox = inflate_segment(seg, w)
            horiz = abs(float(seg["y1"]) - float(seg["y2"])) < 1e-9
            vert = abs(float(seg["x1"]) - float(seg["x2"])) < 1e-9
            wires.append(
                MetalRect(
                    net=net,
                    layer=layer,
                    bbox=bbox,
                    width_um=w,
                    kind="wire",
                    horizontal=True if horiz else (False if vert else None),
                )
            )
    for pseg in (design.power_grid or {}).get("segments", []) or []:
        if pseg.get("role") == "follow_pin":
            continue
        net = str(pseg.get("net", "VPWR"))
        layer = str(pseg.get("layer", "met4"))
        w = _layer_width(tech, layer, pseg)
        bbox = inflate_segment(pseg, w)
        wires.append(
            MetalRect(net=net, layer=layer, bbox=bbox, width_um=w, kind="wire")
        )

    vias = _infer_vias(design, tech)
    obs = _instance_obs_rects(design)
    return {"wires": wires, "vias": vias, "obs": obs}


def _infer_vias(design: DesignObject, tech: dict) -> List[MetalRect]:
    vias: List[MetalRect] = []
    default_sz = float(tech.get("via_size_um") or 0.15)
    for net, segs in design.routing.items():
        layers_at: Dict[Point, Set[str]] = defaultdict(set)
        for seg in segs:
            layer = str(seg.get("layer", "met2"))
            layers_at[(float(seg["x1"]), float(seg["y1"]))].add(layer)
            layers_at[(float(seg["x2"]), float(seg["y2"]))].add(layer)
        for (x, y), layers in layers_at.items():
            if len(layers) < 2:
                continue
            ordered = sorted(layers)
            for i, la in enumerate(ordered):
                for lb in ordered[i + 1 :]:
                    sz = min(_via_size(tech, la), _via_size(tech, lb), default_sz)
                    w_lim = min(_layer_width(tech, la) or sz, _layer_width(tech, lb) or sz)
                    if w_lim > 0:
                        sz = min(sz, w_lim)
                    hw = max(sz * 0.5, 1e-6)
                    vias.append(
                        MetalRect(
                            net=net,
                            layer=f"via_{la}_{lb}",
                            bbox=(x - hw, y - hw, x + hw, y + hw),
                            width_um=sz,
                            kind="via",
                        )
                    )
    return vias


def _orient_rect(x1: float, y1: float, x2: float, y2: float, w: float, h: float, ori: str) -> BBox:
    ori = (ori or "N").upper()
    pts = [(x1, y1), (x2, y2)]

    def xf(x: float, y: float) -> Tuple[float, float]:
        if ori in ("S", "FS"):
            x, y = w - x, h - y
        elif ori == "FN":
            x = w - x
        elif ori in ("W", "E", "FW", "FE"):
            x, y = y, x
            if ori in ("E", "FE"):
                x, y = h - x, w - y
        return x, y

    a = xf(pts[0][0], pts[0][1])
    b = xf(pts[1][0], pts[1][1])
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def _instance_obs_rects(design: DesignObject) -> List[MetalRect]:
    out: List[MetalRect] = []
    lib = design.library.get("cells") or {}
    for name, inst in design.instances.items():
        ctype = design.cells.get(name, {}).get("cell_type")
        cell = lib.get(ctype or "", {})
        ox = float(inst["x"])
        oy = float(inst["y"])
        ori = str(inst.get("orientation") or "N")
        cw = float(inst.get("width") or cell.get("width") or 0.0)
        ch = float(inst.get("height") or cell.get("height") or 0.0)
        for r in cell.get("obs") or []:
            bb = _orient_rect(
                float(r["x1"]), float(r["y1"]), float(r["x2"]), float(r["y2"]), cw, ch, ori
            )
            out.append(
                MetalRect(
                    net=f"OBS:{name}",
                    layer=str(r.get("layer") or "li1"),
                    bbox=(ox + bb[0], oy + bb[1], ox + bb[2], oy + bb[3]),
                    width_um=0.0,
                    kind="obs",
                )
            )
    return out


def run_drc(design: DesignObject, config: Dict[str, Any]) -> Dict[str, Any]:
    drc_cfg = config.get("drc", {}) or {}
    pitch = float(config.get("routing", {}).get("grid_pitch_um", 2.0))
    snap = float(drc_cfg.get("snap_um", 0.5))
    do_geom = bool(drc_cfg.get("geometric_shorts", True))
    do_gcell = bool(drc_cfg.get("gcell_shorts", True))
    do_conn = bool(drc_cfg.get("connectivity_opens", True))
    do_width = bool(drc_cfg.get("min_width", True))
    do_grid = bool(drc_cfg.get("mfg_grid", True))
    do_encl = bool(drc_cfg.get("pin_enclosure", True))
    do_via = bool(drc_cfg.get("via_checks", True))
    same_net_scale = float(drc_cfg.get("same_net_spacing_scale", 0.5))
    pass_on = list(
        drc_cfg.get("pass_on", ["overlap", "short", "open", "spacing"]) or []
    )
    max_report = int(drc_cfg.get("max_report", 200))
    tech = design.tech or {}
    grid = float(drc_cfg.get("mfg_grid_um") or tech.get("mfg_grid_um") or 0.005)

    violations: List[dict] = []

    # --- Placement overlaps (AABB) + optional OBS-OBS ---
    index = create_spatial_index()
    boxes: Dict[int, Tuple[str, BBox]] = {}
    for i, (name, inst) in enumerate(design.instances.items()):
        w = float(inst.get("width", 0.0))
        h = float(inst.get("height", 0.0))
        if w <= 0 or h <= 0:
            ctype = design.cells.get(name, {}).get("cell_type")
            lib = design.library.get("cells", {}).get(ctype or "", {})
            w = float(lib.get("width", 1.0))
            h = float(lib.get("height", 2.72))
        bbox = (float(inst["x"]), float(inst["y"]), float(inst["x"]) + w, float(inst["y"]) + h)
        boxes[i] = (name, bbox)
        index.insert(i, bbox)

    for i, (name, bbox) in boxes.items():
        for j in index.intersection(bbox):
            if j <= i:
                continue
            other_name, other_bbox = boxes[j]
            if boxes_overlap(bbox, other_bbox):
                violations.append(
                    _hit(
                        "overlap",
                        "place.overlap",
                        a=name,
                        b=other_name,
                        bbox_a=bbox,
                        bbox_b=other_bbox,
                    )
                )

    geom = collect_drc_geometry(design, config)
    wires: List[MetalRect] = geom["wires"]
    vias: List[MetalRect] = geom["vias"]
    obs: List[MetalRect] = geom["obs"]

    segs_by_layer: Dict[str, List[Tuple[str, dict]]] = defaultdict(list)
    for net, segs in design.routing.items():
        for seg in segs:
            segs_by_layer[str(seg["layer"])].append((net, seg))
    for pseg in (design.power_grid or {}).get("segments", []) or []:
        if pseg.get("role") == "follow_pin":
            continue
        segs_by_layer[str(pseg["layer"])].append((str(pseg.get("net", "VPWR")), pseg))

    if do_gcell:
        edge_owner: Dict[Tuple[str, Tuple[float, float, float, float]], str] = {}
        for layer, items in segs_by_layer.items():
            for net, seg in items:
                key = _gcell_key(seg, layer, pitch)
                owner = edge_owner.get(key)
                if owner is None:
                    edge_owner[key] = net
                elif owner != net:
                    violations.append(
                        _hit(
                            "short_gcell",
                            f"{layer}.short_gcell",
                            net_a=owner,
                            net_b=net,
                            layer=layer,
                            segment=key[1],
                        )
                    )

    by_layer: Dict[str, List[MetalRect]] = defaultdict(list)
    for r in wires:
        by_layer[r.layer].append(r)

    if do_geom:
        for layer, rects in by_layer.items():
            min_sp = _layer_spacing(tech, layer, pitch)
            violations.extend(
                _rect_short_spacing(rects, layer, min_sp, same_net_scale)
            )

    if obs:
        violations.extend(_obs_checks(obs, wires, boxes_overlap))

    if do_via and vias:
        violations.extend(_via_checks(vias, wires, tech, pitch))

    if do_width:
        violations.extend(_min_width_hits(wires, tech))

    if do_grid:
        violations.extend(_offgrid_hits(design, grid, limit=40))

    if do_encl:
        violations.extend(_pin_enclosure_hits(design, wires, snap))

    if do_conn:
        violations.extend(_connectivity_opens(design, snap, config))
    else:
        for net, segs in design.routing.items():
            pins = [
                p
                for p in design.nets.get(net, {}).get("pins", [])
                if not str(p[0]).startswith("PORT:")
            ]
            if not segs and len(pins) >= 2:
                violations.append(
                    _hit("open", "open.no_segments", net=net, reason="no segments")
                )

    counts = Counter(v["type"] for v in violations)
    pass_count = sum(counts.get(t, 0) for t in pass_on)
    return {
        "violation_count": pass_count,
        "violation_count_all": len(violations),
        "counts_by_type": dict(counts),
        "pass_on": pass_on,
        "violations": violations[:max_report],
        "spatial_backend": index.backend,
        "geometry": {
            "wires": len(wires),
            "vias": len(vias),
            "obs": len(obs),
        },
    }


def _rect_short_spacing(
    rects: List[MetalRect],
    layer: str,
    min_sp: float,
    same_net_scale: float,
) -> List[dict]:
    out: List[dict] = []
    expand = max(min_sp, 0.0)
    for i, j in _sweep_pairs(rects, expand):
        a, b = rects[i], rects[j]
        gap = edge_gap(a.bbox, b.bbox)
        same = a.net == b.net
        if gap <= 1e-12:
            if same:
                continue
            ov = -gap if gap < 0 else 0.0
            out.append(
                _hit(
                    "short",
                    f"{layer}.short",
                    net_a=a.net,
                    net_b=b.net,
                    layer=layer,
                    bbox_a=a.bbox,
                    bbox_b=b.bbox,
                    overlap_um=ov,
                    segment=(
                        max(a.bbox[0], b.bbox[0]),
                        max(a.bbox[1], b.bbox[1]),
                        min(a.bbox[2], b.bbox[2]),
                        min(a.bbox[3], b.bbox[3]),
                    ),
                )
            )
            continue
        req = min_sp * same_net_scale if same else min_sp
        if gap + 1e-12 < req:
            out.append(
                _hit(
                    "spacing",
                    f"{layer}.spacing" + (".samenet" if same else ""),
                    net_a=a.net,
                    net_b=b.net,
                    layer=layer,
                    distance=gap,
                    required=req,
                    same_net=same,
                )
            )
    return out


def _obs_checks(
    obs: List[MetalRect],
    wires: List[MetalRect],
    overlap_fn,
) -> List[dict]:
    out: List[dict] = []
    obs_by: Dict[str, List[MetalRect]] = defaultdict(list)
    for o in obs:
        obs_by[o.layer].append(o)
    for layer, orects in obs_by.items():
        for i, j in _sweep_pairs(orects, 0.0):
            a, b = orects[i], orects[j]
            if a.net == b.net:
                continue
            if overlap_fn(a.bbox, b.bbox):
                out.append(
                    _hit(
                        "overlap",
                        f"{layer}.obs.overlap",
                        a=a.net,
                        b=b.net,
                        layer=layer,
                        bbox_a=a.bbox,
                        bbox_b=b.bbox,
                    )
                )
        wlayer = [w for w in wires if w.layer == layer]
        if not wlayer or not orects:
            continue
        idx = create_spatial_index()
        for i, o in enumerate(orects):
            idx.insert(i, o.bbox)
        for w in wlayer:
            for i in idx.intersection(w.bbox):
                o = orects[i]
                if overlap_fn(w.bbox, o.bbox):
                    out.append(
                        _hit(
                            "obs_short",
                            f"{layer}.obs.short",
                            net_a=w.net,
                            net_b=o.net,
                            layer=layer,
                            bbox_a=w.bbox,
                            bbox_b=o.bbox,
                        )
                    )
    return out


def _via_checks(
    vias: List[MetalRect],
    wires: List[MetalRect],
    tech: dict,
    pitch: float,
) -> List[dict]:
    out: List[dict] = []
    by_cut: Dict[str, List[MetalRect]] = defaultdict(list)
    for v in vias:
        by_cut[v.layer].append(v)
    for cut, rects in by_cut.items():
        min_sp = float(tech.get("via_size_um") or 0.15)
        parts = cut.split("_")
        if len(parts) >= 3:
            min_sp = min(_layer_spacing(tech, parts[1], pitch), min_sp + 0.05)
        for i, j in _sweep_pairs(rects, min_sp):
            a, b = rects[i], rects[j]
            if a.net == b.net:
                continue
            gap = edge_gap(a.bbox, b.bbox)
            if gap < min_sp - 1e-12:
                typ = "short" if gap <= 1e-12 else "via"
                out.append(
                    _hit(
                        typ,
                        f"{cut}.spacing",
                        net_a=a.net,
                        net_b=b.net,
                        layer=cut,
                        distance=max(gap, 0.0),
                        required=min_sp,
                    )
                )

    wires_by: Dict[str, List[MetalRect]] = defaultdict(list)
    for w in wires:
        wires_by[w.layer].append(w)
    for v in vias:
        parts = v.layer.split("_")
        adj = parts[1:] if len(parts) >= 3 else []
        for layer in adj:
            enc = _enclosure(tech, layer)
            need = (
                v.bbox[0] - enc,
                v.bbox[1] - enc,
                v.bbox[2] + enc,
                v.bbox[3] + enc,
            )
            covered = False
            for w in wires_by.get(layer, []):
                if w.net != v.net:
                    continue
                b = w.bbox
                if (
                    b[0] <= need[0] + 1e-9
                    and b[1] <= need[1] + 1e-9
                    and b[2] >= need[2] - 1e-9
                    and b[3] >= need[3] - 1e-9
                ):
                    covered = True
                    break
            if not covered:
                flush = False
                for w in wires_by.get(layer, []):
                    if w.net != v.net:
                        continue
                    if boxes_overlap(w.bbox, v.bbox):
                        flush = True
                        break
                out.append(
                    _hit(
                        "enclosure",
                        f"{v.layer}.enclosure",
                        net=v.net,
                        layer=layer,
                        via=v.layer,
                        bbox=v.bbox,
                        reason="via_uncovered" if not flush else "via_enclosure",
                    )
                )
    return out


def _min_width_hits(wires: List[MetalRect], tech: dict) -> List[dict]:
    out: List[dict] = []
    for w in wires:
        req = _layer_width(tech, w.layer)
        if req <= 0:
            continue
        b = w.bbox
        drawn = min(b[2] - b[0], b[3] - b[1])
        if drawn + 1e-9 < req:
            out.append(
                _hit(
                    "min_width",
                    f"{w.layer}.width",
                    net=w.net,
                    layer=w.layer,
                    width=drawn,
                    required=req,
                    bbox=b,
                )
            )
    return out


def _on_grid(val: float, grid: float) -> bool:
    if grid <= 0:
        return True
    q = round(val / grid)
    return abs(val - q * grid) <= max(1e-6, grid * 1e-4)


def _offgrid_hits(design: DesignObject, grid: float, limit: int) -> List[dict]:
    out: List[dict] = []
    if grid <= 0:
        return out
    seen: Set[Tuple[str, float, float]] = set()
    for net, segs in design.routing.items():
        for seg in segs:
            for x, y in ((float(seg["x1"]), float(seg["y1"])), (float(seg["x2"]), float(seg["y2"]))):
                if _on_grid(x, grid) and _on_grid(y, grid):
                    continue
                key = (net, round(x, 6), round(y, 6))
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    _hit(
                        "offgrid",
                        "mfg.grid",
                        net=net,
                        layer=str(seg.get("layer")),
                        x=x,
                        y=y,
                        grid_um=grid,
                    )
                )
                if len(out) >= limit:
                    return out
    return out


def _pin_enclosure_hits(
    design: DesignObject, wires: List[MetalRect], snap: float
) -> List[dict]:
    out: List[dict] = []
    lib = design.library.get("cells") or {}
    wires_by_net: Dict[str, List[MetalRect]] = defaultdict(list)
    for w in wires:
        wires_by_net[w.net].append(w)
    for inst, info in design.cells.items():
        inst_d = design.instances.get(inst)
        if inst_d is None:
            continue
        cell = lib.get(info.get("cell_type") or "", {})
        ox, oy = float(inst_d["x"]), float(inst_d["y"])
        ori = str(inst_d.get("orientation") or "N")
        cw = float(inst_d.get("width") or cell.get("width") or 0.0)
        ch = float(inst_d.get("height") or cell.get("height") or 0.0)
        pins_map = info.get("pins") or {}
        for pname, pinfo in (cell.get("pins") or {}).items():
            net = pins_map.get(pname)
            if not net:
                continue
            rects = pinfo.get("rects") or []
            if not rects:
                continue
            metal_layers = {
                str(r.get("layer"))
                for r in rects
                if str(r.get("layer", "")).startswith("met") or str(r.get("layer")) == "li1"
            }
            if not metal_layers:
                continue
            covered = False
            pin_bb: Optional[BBox] = None
            for r in rects:
                layer = str(r.get("layer") or "")
                if layer not in metal_layers:
                    continue
                loc = _orient_rect(
                    float(r["x1"]), float(r["y1"]), float(r["x2"]), float(r["y2"]), cw, ch, ori
                )
                bb = (ox + loc[0], oy + loc[1], ox + loc[2], oy + loc[3])
                pin_bb = bb
                for w in wires_by_net.get(net, []):
                    if w.layer != layer:
                        continue
                    if boxes_overlap(w.bbox, bb):
                        covered = True
                        break
                if covered:
                    break
            if not covered:
                out.append(
                    _hit(
                        "enclosure",
                        "pin.enclosure",
                        net=net,
                        endpoint=f"{inst}/{pname}",
                        bbox=pin_bb,
                        reason="pin_not_covered",
                    )
                )
    return out


def _gcell_key(seg: dict, layer: str, pitch: float) -> Tuple[str, Tuple[float, float, float, float]]:
    x1 = round(float(seg["x1"]) / pitch) * pitch
    y1 = round(float(seg["y1"]) / pitch) * pitch
    x2 = round(float(seg["x2"]) / pitch) * pitch
    y2 = round(float(seg["y2"]) / pitch) * pitch
    if (x1, y1) > (x2, y2):
        x1, y1, x2, y2 = x2, y2, x1, y1
    return (layer, (x1, y1, x2, y2))


def _seg_aabb(seg: dict) -> BBox:
    x1, y1 = float(seg["x1"]), float(seg["y1"])
    x2, y2 = float(seg["x2"]), float(seg["y2"])
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def _geometric_shorts(
    items: List[Tuple[str, dict]], layer: str, min_overlap: float = 0.5
) -> List[dict]:
    """Legacy collinear short helper (tests / fallback)."""
    tech = {"layers": {layer: {"width_um": 0.0, "min_spacing_um": min_overlap}}}
    rects = []
    for net, seg in items:
        bbox = inflate_segment(seg, 0.0)
        rects.append(MetalRect(net=net, layer=layer, bbox=bbox, width_um=0.0))
    hits = _rect_short_spacing(rects, layer, min_overlap, same_net_scale=1.0)
    return [h for h in hits if h["type"] == "short"]


def _snap_pt(x: float, y: float, snap: float) -> Point:
    s = max(snap, 1e-9)
    return (round(x / s) * s, round(y / s) * s)


def _pin_xy(design: DesignObject, inst: str, pin: str) -> Optional[Point]:
    from pnr_tool.algorithms.pins import instance_pin_xy

    xy = instance_pin_xy(design, inst, pin)
    if xy is not None:
        return (float(xy[0]), float(xy[1]))
    if str(inst).startswith("PORT:"):
        pos = (design.meta.get("port_positions") or {}).get(inst.split(":", 1)[1])
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


def _point_on_seg(px: float, py: float, seg: dict, tol: float) -> bool:
    x1, y1 = float(seg["x1"]), float(seg["y1"])
    x2, y2 = float(seg["x2"]), float(seg["y2"])
    if abs(y1 - y2) <= tol:
        return abs(py - y1) <= tol and min(x1, x2) - tol <= px <= max(x1, x2) + tol
    if abs(x1 - x2) <= tol:
        return abs(px - x1) <= tol and min(y1, y2) - tol <= py <= max(y1, y2) + tol
    # Manhattan projection onto bbox
    return (
        min(x1, x2) - tol <= px <= max(x1, x2) + tol
        and min(y1, y2) - tol <= py <= max(y1, y2) + tol
    )


def _connectivity_opens(
    design: DesignObject, snap: float, config: Dict[str, Any]
) -> List[dict]:
    out: List[dict] = []
    tol = max(snap, 1e-6)
    for net, ninfo in design.nets.items():
        pins = list(ninfo.get("pins", []) or [])
        if len(pins) < 2:
            continue
        segs = list(design.routing.get(net, ()) or [])
        if not segs:
            out.append(_hit("open", "open.no_segments", net=net, reason="no segments"))
            continue

        adj: Dict[Point, Set[Point]] = defaultdict(set)
        nodes: Set[Point] = set()

        def link(a: Point, b: Point) -> None:
            if a == b:
                nodes.add(a)
                return
            adj[a].add(b)
            adj[b].add(a)
            nodes.add(a)
            nodes.add(b)

        endpoints: List[Point] = []
        for seg in segs:
            a = _snap_pt(float(seg["x1"]), float(seg["y1"]), snap)
            b = _snap_pt(float(seg["x2"]), float(seg["y2"]), snap)
            link(a, b)
            endpoints.extend((a, b))

        # T-junctions: extra endpoints that lie on another segment
        uniq_eps = list(dict.fromkeys(endpoints))
        for seg in segs:
            a = _snap_pt(float(seg["x1"]), float(seg["y1"]), snap)
            b = _snap_pt(float(seg["x2"]), float(seg["y2"]), snap)
            for p in uniq_eps:
                if p in (a, b):
                    continue
                if _point_on_seg(p[0], p[1], seg, tol):
                    link(a, p)
                    link(p, b)

        pin_nodes: Dict[Tuple[str, str], Point] = {}
        for inst, pin in pins:
            xy = _pin_xy(design, inst, pin)
            if xy is None:
                continue
            p = _snap_pt(xy[0], xy[1], snap)
            pin_nodes[(inst, pin)] = p
            nodes.add(p)
            on_wire = False
            for seg in segs:
                if _point_on_seg(p[0], p[1], seg, max(2.0 * snap, 1.0)):
                    sa = _snap_pt(float(seg["x1"]), float(seg["y1"]), snap)
                    sb = _snap_pt(float(seg["x2"]), float(seg["y2"]), snap)
                    link(p, sa)
                    link(p, sb)
                    on_wire = True
                    break
            if on_wire:
                continue
            best = None
            best_d = 1e30
            for n in list(nodes):
                if n == p:
                    continue
                d = abs(n[0] - p[0]) + abs(n[1] - p[1])
                if d < best_d:
                    best_d = d
                    best = n
            if best is not None and best_d <= max(2.0 * snap, 1.0):
                link(p, best)

        driver = ninfo.get("driver")
        if driver is None:
            continue
        dkey = (driver[0], driver[1])
        if dkey not in pin_nodes:
            out.append(_hit("open", "open.driver_unplaced", net=net, reason="driver_pin_unplaced"))
            continue

        start = pin_nodes[dkey]
        seen: Set[Point] = set()
        q: deque[Point] = deque([start])
        seen.add(start)
        while q:
            cur = q.popleft()
            for nxt in adj.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)

        for (inst, pin), p in pin_nodes.items():
            if (inst, pin) == dkey:
                continue
            if p not in seen:
                out.append(
                    _hit(
                        "open",
                        "open.disconnected_sink",
                        net=net,
                        reason="disconnected_sink",
                        sink=f"{inst}/{pin}",
                    )
                )
    return out


def _spacing_violations(
    items: List[Tuple[str, dict]], layer: str, min_sp: float
) -> List[dict]:
    rects = [
        MetalRect(net=net, layer=layer, bbox=inflate_segment(seg, 0.0), width_um=0.0)
        for net, seg in items
    ]
    return [h for h in _rect_short_spacing(rects, layer, min_sp, 1.0) if h["type"] == "spacing"]


def _overlapping_pairs(a: List[Span], b: List[Span]) -> Iterable[Tuple[str, str]]:
    b_sorted = sorted(b, key=lambda s: s[1])
    for net_a, start_a, end_a in a:
        for net_b, start_b, end_b in b_sorted:
            if start_b > end_a:
                break
            if end_b < start_a or net_a == net_b:
                continue
            yield net_a, net_b
