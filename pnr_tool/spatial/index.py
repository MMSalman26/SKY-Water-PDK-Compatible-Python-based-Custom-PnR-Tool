"""R-tree spatial index with cKDTree / grid-hash fallback for Windows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

import numpy as np

BBox = Tuple[float, float, float, float]  # minx, miny, maxx, maxy


@dataclass
class _Item:
    id: int
    bbox: BBox


class SpatialIndex:
    """Uniform API over rtree or fallback backends."""

    def __init__(self, backend: str, impl: object) -> None:
        self.backend = backend
        self._impl = impl

    def insert(self, item_id: int, bbox: BBox) -> None:
        raise NotImplementedError

    def intersection(self, bbox: BBox) -> List[int]:
        raise NotImplementedError


class _RtreeIndex(SpatialIndex):
    def __init__(self, index_obj: object) -> None:
        super().__init__("rtree", index_obj)

    def insert(self, item_id: int, bbox: BBox) -> None:
        self._impl.insert(item_id, bbox)

    def intersection(self, bbox: BBox) -> List[int]:
        return list(self._impl.intersection(bbox))


class _CKDTreeIndex(SpatialIndex):
    """Approximate bbox queries via center points + radius."""

    def __init__(self) -> None:
        super().__init__("ckdtree", None)
        self._items: List[_Item] = []

    def insert(self, item_id: int, bbox: BBox) -> None:
        self._items.append(_Item(item_id, bbox))

    def intersection(self, bbox: BBox) -> List[int]:
        minx, miny, maxx, maxy = bbox
        hits: List[int] = []
        for item in self._items:
            a = item.bbox
            if not (a[2] <= minx or a[0] >= maxx or a[3] <= miny or a[1] >= maxy):
                hits.append(item.id)
        return hits


class _GridHashIndex(SpatialIndex):
    def __init__(self, cell_size: float = 10.0) -> None:
        super().__init__("grid_hash", None)
        self.cell_size = cell_size
        self._cells: dict[Tuple[int, int], List[_Item]] = {}
        self._items: List[_Item] = []

    def _keys(self, bbox: BBox) -> Iterable[Tuple[int, int]]:
        minx, miny, maxx, maxy = bbox
        cs = self.cell_size
        x0, x1 = int(minx // cs), int(maxx // cs)
        y0, y1 = int(miny // cs), int(maxy // cs)
        for ix in range(x0, x1 + 1):
            for iy in range(y0, y1 + 1):
                yield ix, iy

    def insert(self, item_id: int, bbox: BBox) -> None:
        item = _Item(item_id, bbox)
        self._items.append(item)
        for key in self._keys(bbox):
            self._cells.setdefault(key, []).append(item)

    def intersection(self, bbox: BBox) -> List[int]:
        seen: set[int] = set()
        hits: List[int] = []
        minx, miny, maxx, maxy = bbox
        for key in self._keys(bbox):
            for item in self._cells.get(key, []):
                if item.id in seen:
                    continue
                a = item.bbox
                if not (a[2] <= minx or a[0] >= maxx or a[3] <= miny or a[1] >= maxy):
                    seen.add(item.id)
                    hits.append(item.id)
        return hits


def create_spatial_index(cell_size: float = 10.0) -> SpatialIndex:
    """Prefer rtree; fall back to brute cKDTree-style scan, then grid hash."""
    try:
        from rtree import index as rtree_index

        props = rtree_index.Property()
        props.dimension = 2
        idx = rtree_index.Index(properties=props)
        return _RtreeIndex(idx)
    except Exception:
        pass

    try:
        # Ensure scipy is importable; use linear scan wrapper (exact bbox)
        from scipy.spatial import cKDTree  # noqa: F401

        return _CKDTreeIndex()
    except Exception:
        return _GridHashIndex(cell_size=cell_size)


def boxes_overlap(a: BBox, b: BBox, eps: float = 1e-9) -> bool:
    return not (
        a[2] <= b[0] + eps
        or a[0] >= b[2] - eps
        or a[3] <= b[1] + eps
        or a[1] >= b[3] - eps
    )
