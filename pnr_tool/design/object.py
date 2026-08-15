"""In-memory Design Object and checkpoints."""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import networkx as nx


DieArea = Tuple[float, float, float, float]


@dataclass
class DesignObject:
    """Single design threaded through Placement → Clock Opt → Routing → checkers."""

    name: str = "design"
    die_area: DieArea = (0.0, 0.0, 100.0, 100.0)
    graph: nx.DiGraph = field(default_factory=nx.DiGraph)
    # instance_name -> {cell_type, pins: {pin: net}, ...}
    cells: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # net -> {pins: [(inst, pin)], driver: (inst, pin)|None}
    nets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    ports: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    instances: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    clock_tree: Dict[str, Any] = field(default_factory=lambda: {"new_buffers": {}, "clock_nets": {}})
    routing: Dict[str, Any] = field(default_factory=dict)
    # Physical VDD/VSS grid from power planning (not from logic netlist)
    power_grid: Dict[str, Any] = field(default_factory=dict)
    library: Dict[str, Any] = field(default_factory=dict)
    tech: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def checkpoint(self, path: Path, stage: str) -> Path:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        out = path / f"{self.name}_{stage}.pkl"
        with out.open("wb") as fh:
            pickle.dump(self, fh, protocol=pickle.HIGHEST_PROTOCOL)
        # Also write a lightweight JSON summary
        summary = {
            "name": self.name,
            "stage": stage,
            "die_area": list(self.die_area),
            "num_cells": len(self.cells),
            "num_nets": len(self.nets),
            "num_placed": len(self.instances),
            "num_routed_nets": len(self.routing),
        }
        with (path / f"{self.name}_{stage}.json").open("w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
        return out

    @staticmethod
    def load_checkpoint(path: Path) -> "DesignObject":
        with Path(path).open("rb") as fh:
            obj = pickle.load(fh)
        if not isinstance(obj, DesignObject):
            raise TypeError(f"Checkpoint is not a DesignObject: {type(obj)}")
        return obj
