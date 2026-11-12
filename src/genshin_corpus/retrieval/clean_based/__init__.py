"""Opt-in Clean/Chunk retrieval; independent of the legacy RU Serving path."""
from .core import QuerySpec, fuse_candidates

__all__ = ["QuerySpec", "fuse_candidates"]
