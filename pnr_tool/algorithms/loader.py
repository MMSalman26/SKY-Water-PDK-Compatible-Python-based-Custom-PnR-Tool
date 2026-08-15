"""Resolve algorithm plugins from built-in aliases or ``module.path:ClassName`` specs."""

from __future__ import annotations

import importlib
from typing import Any, Dict, Optional, Type, TypeVar, Union

from pnr_tool.algorithms.base import ClockOptAlgorithm, PlacementAlgorithm, RoutingAlgorithm
from pnr_tool.algorithms.clock_opt import HTreeClockOpt
from pnr_tool.algorithms.placement import ForceDirectedPlacement
from pnr_tool.algorithms.random_place import RandomPlacement
from pnr_tool.algorithms.routing import GlobalRouter

T = TypeVar("T")

AlgoSpec = Union[str, PlacementAlgorithm, ClockOptAlgorithm, RoutingAlgorithm, None]

_PLACEMENT_ALIASES: Dict[str, Type[PlacementAlgorithm]] = {
    "default": ForceDirectedPlacement,
    "force_directed": ForceDirectedPlacement,
    "random": RandomPlacement,
}
_CLOCK_ALIASES: Dict[str, Type[ClockOptAlgorithm]] = {
    "default": HTreeClockOpt,
    "htree": HTreeClockOpt,
}
_ROUTING_ALIASES: Dict[str, Type[RoutingAlgorithm]] = {
    "default": GlobalRouter,
    "global": GlobalRouter,
}


class PluginLoadError(ValueError):
    """Raised when an algorithm plugin cannot be resolved or is the wrong type."""


def _split_spec(spec: str) -> tuple[str, Optional[str]]:
    text = spec.strip()
    if ":" in text:
        module_path, class_name = text.rsplit(":", 1)
        return module_path.strip(), class_name.strip() or None
    return text, None


def _import_class(module_path: str, class_name: str) -> Any:
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise PluginLoadError(f"Cannot import plugin module '{module_path}': {exc}") from exc
    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        raise PluginLoadError(
            f"Module '{module_path}' has no attribute '{class_name}'"
        ) from exc
    return cls


def _resolve(
    spec: AlgoSpec,
    *,
    kind: str,
    abc: Type[T],
    aliases: Dict[str, Type[T]],
    default_cls: Type[T],
) -> tuple[T, str]:
    """Return ``(instance, resolved_id)`` for a stage algorithm."""
    if spec is None:
        return default_cls(), "default"
    if isinstance(spec, abc):
        name = f"{type(spec).__module__}:{type(spec).__name__}"
        return spec, name  # type: ignore[return-value]
    if not isinstance(spec, str):
        raise PluginLoadError(
            f"{kind} algorithm must be a string spec or {abc.__name__} instance, "
            f"got {type(spec).__name__}"
        )

    text = spec.strip()
    if not text:
        return default_cls(), "default"

    alias_key = text.lower()
    if alias_key in aliases:
        return aliases[alias_key](), alias_key

    module_path, class_name = _split_spec(text)
    if class_name is None:
        raise PluginLoadError(
            f"Unknown {kind} algorithm alias '{text}'. "
            f"Use one of {sorted(aliases)} or 'module.path:ClassName'."
        )

    cls = _import_class(module_path, class_name)
    if not isinstance(cls, type) or not issubclass(cls, abc):
        raise PluginLoadError(
            f"Plugin '{text}' must be a subclass of {abc.__name__}, got {cls!r}"
        )
    try:
        instance = cls()
    except Exception as exc:
        raise PluginLoadError(f"Failed to instantiate plugin '{text}': {exc}") from exc
    if not isinstance(instance, abc):
        raise PluginLoadError(
            f"Plugin '{text}' did not produce a {abc.__name__} instance"
        )
    return instance, f"{module_path}:{class_name}"


def load_placement(spec: AlgoSpec = None) -> tuple[PlacementAlgorithm, str]:
    return _resolve(
        spec,
        kind="placement",
        abc=PlacementAlgorithm,
        aliases=_PLACEMENT_ALIASES,
        default_cls=ForceDirectedPlacement,
    )


def load_clock_opt(spec: AlgoSpec = None) -> tuple[ClockOptAlgorithm, str]:
    return _resolve(
        spec,
        kind="clock_opt",
        abc=ClockOptAlgorithm,
        aliases=_CLOCK_ALIASES,
        default_cls=HTreeClockOpt,
    )


def load_routing(spec: AlgoSpec = None) -> tuple[RoutingAlgorithm, str]:
    return _resolve(
        spec,
        kind="routing",
        abc=RoutingAlgorithm,
        aliases=_ROUTING_ALIASES,
        default_cls=GlobalRouter,
    )


def resolve_algorithm_ids(
    placement: AlgoSpec = None,
    clock_opt: AlgoSpec = None,
    routing: AlgoSpec = None,
) -> Dict[str, str]:
    """Resolve specs to ids without instantiating (for scoreboard / docs)."""
    _, p = load_placement(placement)
    _, c = load_clock_opt(clock_opt)
    _, r = load_routing(routing)
    return {"placement": p, "clock_opt": c, "routing": r}
