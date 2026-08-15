"""Stage-boundary schema validation."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


class ContractError(ValueError):
    """Raised when a stage output violates its fixed schema."""


def _require_keys(obj: Mapping[str, Any], keys: Sequence[str], ctx: str) -> None:
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ContractError(f"{ctx}: missing keys {missing}")


def validate_instances(
    instances: Mapping[str, Any],
    die_area: tuple[float, float, float, float],
) -> None:
    if not isinstance(instances, Mapping) or not instances:
        raise ContractError("Placement: instances must be a non-empty mapping")
    minx, miny, maxx, maxy = die_area
    for name, inst in instances.items():
        if not isinstance(inst, Mapping):
            raise ContractError(f"Placement: instance '{name}' must be a mapping")
        _require_keys(inst, ("x", "y", "orientation", "is_fixed"), f"Placement '{name}'")
        for key in ("x", "y"):
            if not isinstance(inst[key], (int, float)):
                raise ContractError(f"Placement '{name}': {key} must be numeric")
        if inst["orientation"] not in ("N", "S", "E", "W", "FN", "FS", "FE", "FW"):
            raise ContractError(f"Placement '{name}': invalid orientation")
        if not isinstance(inst["is_fixed"], bool):
            raise ContractError(f"Placement '{name}': is_fixed must be bool")
        x, y = float(inst["x"]), float(inst["y"])
        if x < minx or y < miny or x > maxx or y > maxy:
            raise ContractError(
                f"Placement '{name}': coordinate ({x}, {y}) outside die_area {die_area}"
            )


def validate_clock_tree(clock_tree: Mapping[str, Any]) -> None:
    if not isinstance(clock_tree, Mapping):
        raise ContractError("ClockOpt: clock_tree must be a mapping")
    _require_keys(clock_tree, ("new_buffers", "clock_nets"), "ClockOpt")
    if not isinstance(clock_tree["new_buffers"], Mapping):
        raise ContractError("ClockOpt: new_buffers must be a mapping")
    if not isinstance(clock_tree["clock_nets"], Mapping):
        raise ContractError("ClockOpt: clock_nets must be a mapping")
    for buf_name, buf in clock_tree["new_buffers"].items():
        _require_keys(buf, ("cell_type", "x", "y", "orientation"), f"ClockOpt buffer '{buf_name}'")


def validate_routing(routing: Mapping[str, Any]) -> None:
    if not isinstance(routing, Mapping):
        raise ContractError("Routing: routing must be a mapping of net -> segments")
    for net, segs in routing.items():
        if not isinstance(segs, list):
            raise ContractError(f"Routing: net '{net}' segments must be a list")
        for i, seg in enumerate(segs):
            if not isinstance(seg, Mapping):
                raise ContractError(f"Routing: net '{net}' seg[{i}] must be a mapping")
            _require_keys(seg, ("layer", "x1", "y1", "x2", "y2"), f"Routing '{net}'[{i}]")
            for key in ("x1", "y1", "x2", "y2"):
                if not isinstance(seg[key], (int, float)):
                    raise ContractError(f"Routing '{net}'[{i}]: {key} must be numeric")
