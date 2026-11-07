"""Opt-in Clean-based chunking pilot, separate from deployed Retrieval Units."""

from .core import ChunkProfile, chunk_parent
from .structure import adapt_record
from .reader import read_natural_parent

__all__ = ["ChunkProfile", "chunk_parent", "adapt_record", "read_natural_parent"]
