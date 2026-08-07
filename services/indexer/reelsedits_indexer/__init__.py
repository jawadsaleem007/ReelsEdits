"""User footage indexer.

Reuses the analyser's visual code verbatim. That reuse is the mechanism that
guarantees reference and footage speak the same enum vocabulary -- without it,
structural matching silently degrades to embedding similarity, which is the
wrong objective (docs/09 s1.1).
"""

from .index import INDEXER_VERSION, ClipIndex, index_clip, index_directory
from .pipeline import ClipFeature, SegmentFeature, usable_ranges

__version__ = "0.2.0"
__all__ = [
    "INDEXER_VERSION",
    "ClipFeature",
    "ClipIndex",
    "SegmentFeature",
    "__version__",
    "index_clip",
    "index_directory",
    "usable_ranges",
]
