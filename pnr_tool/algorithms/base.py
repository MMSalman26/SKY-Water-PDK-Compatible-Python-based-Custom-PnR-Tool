"""Abstract stage contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from pnr_tool.design.object import DesignObject


class PlacementAlgorithm(ABC):
    @abstractmethod
    def execute(self, design: DesignObject, config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Return instances: name -> {x, y, orientation, is_fixed}."""


class ClockOptAlgorithm(ABC):
    @abstractmethod
    def execute(self, design: DesignObject, config: Dict[str, Any]) -> Dict[str, Any]:
        """Return {new_buffers, clock_nets}."""


class RoutingAlgorithm(ABC):
    @abstractmethod
    def execute(self, design: DesignObject, config: Dict[str, Any]) -> Dict[str, Any]:
        """Return net -> list of {layer, x1, y1, x2, y2}."""
