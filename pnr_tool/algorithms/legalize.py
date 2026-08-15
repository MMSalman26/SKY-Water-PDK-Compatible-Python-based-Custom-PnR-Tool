"""Standard-cell row legalizer used by placement and post-CTS buffer insertion."""

from __future__ import annotations

import bisect
from typing import Any, Dict, List, Mapping, Optional, Tuple

DieArea = Tuple[float, float, float, float]


class RowLegalizer:
    """Tracks occupied x-intervals per placement row and hands out free slots.

    Rows are anchored at ``miny + i * row_height``. ``reserve`` returns the legal
    lower-left corner closest to the requested target, never overlapping an
    interval that was previously occupied or reserved.
    """

    def __init__(self, die_area: DieArea, row_height: float, spacing: float = 0.01) -> None:
        minx, miny, maxx, maxy = (float(v) for v in die_area)
        self.minx = minx
        self.miny = miny
        self.maxx = maxx
        self.maxy = maxy
        self.row_height = float(row_height)
        self.spacing = float(spacing)
        self.num_rows = max(1, int(round((maxy - miny) / self.row_height)))
        self._starts: Dict[int, List[float]] = {}
        self._ends: Dict[int, List[float]] = {}
        self._tail: Dict[int, float] = {}

    @property
    def die_area(self) -> DieArea:
        return (self.minx, self.miny, self.maxx, self.maxy)

    def row_of(self, y: float) -> int:
        idx = int(round((float(y) - self.miny) / self.row_height))
        return max(0, min(self.num_rows - 1, idx))

    def row_y(self, row: int) -> float:
        return self.miny + row * self.row_height

    @classmethod
    def from_instances(
        cls,
        instances: Mapping[str, Mapping[str, Any]],
        die_area: DieArea,
        row_height: float,
        spacing: float = 0.01,
    ) -> "RowLegalizer":
        legalizer = cls(die_area, row_height, spacing)
        for inst in instances.values():
            width = float(inst.get("width", 0.0))
            if width <= 0:
                continue
            legalizer.occupy(float(inst["x"]), float(inst["y"]), width)
        return legalizer

    def occupy(self, x: float, y: float, width: float) -> None:
        row = self.row_of(y)
        starts = self._starts.setdefault(row, [])
        ends = self._ends.setdefault(row, [])
        pos = bisect.bisect_left(starts, x)
        starts.insert(pos, x)
        ends.insert(pos, x + width)
        self._tail[row] = max(self._tail.get(row, self.minx), x + width)

    def reserve(self, width: float, target_x: float, target_y: float) -> Tuple[float, float]:
        """Reserve ``width`` near (target_x, target_y); returns the legal corner."""
        width = float(width)
        target_row = self.row_of(target_y)
        best: Optional[Tuple[float, int, float]] = None
        for row in self._rows_by_distance(target_row):
            slot = self._best_slot_in_row(row, width, target_x)
            if slot is None:
                continue
            cost = abs(slot - target_x) + abs(row - target_row) * self.row_height
            if best is None or cost < best[0]:
                best = (cost, row, slot)
            # Rows further away cannot beat a candidate already closer than the
            # vertical distance alone.
            if best[0] <= abs(row - target_row) * self.row_height:
                break
        if best is None:
            row = target_row
            slot = self._append_to_row(row, width)
        else:
            _cost, row, slot = best
        self.occupy(slot, self.row_y(row), width + self.spacing)
        return slot, self.row_y(row)

    def _rows_by_distance(self, target_row: int) -> List[int]:
        order = [target_row]
        for delta in range(1, self.num_rows):
            below = target_row - delta
            above = target_row + delta
            if below >= 0:
                order.append(below)
            if above < self.num_rows:
                order.append(above)
            if below < 0 and above >= self.num_rows:
                break
        return order

    def _best_slot_in_row(self, row: int, width: float, target_x: float) -> Optional[float]:
        need = width + self.spacing
        tail = self._tail.get(row, self.minx)
        if tail > self.minx:
            tail += self.spacing
        target_x = min(max(target_x, self.minx), max(self.maxx - need, self.minx))
        if target_x >= tail and target_x + need <= self.maxx:
            return target_x

        starts = self._starts.get(row, [])
        ends = self._ends.get(row, [])
        best: Optional[float] = None
        cursor = self.minx
        for i in range(len(starts) + 1):
            gap_end = starts[i] if i < len(starts) else self.maxx
            if gap_end - cursor >= need:
                candidate = min(max(target_x, cursor), gap_end - need)
                if best is None or abs(candidate - target_x) < abs(best - target_x):
                    best = candidate
            if best is not None and cursor - target_x >= abs(best - target_x):
                break
            if i < len(starts):
                cursor = max(cursor, ends[i] + self.spacing)
        return best

    def _append_to_row(self, row: int, width: float) -> float:
        tail = self._tail.get(row, self.minx)
        slot = tail + self.spacing if tail > self.minx else self.minx
        self.maxx = max(self.maxx, slot + width + self.spacing)
        return slot
