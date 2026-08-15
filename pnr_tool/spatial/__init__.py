"""Spatial indexing with rtree + fallback."""

from .index import SpatialIndex, create_spatial_index

__all__ = ["SpatialIndex", "create_spatial_index"]
