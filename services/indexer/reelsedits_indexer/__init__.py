"""User footage indexer.

Deliberately a SUBSET of reference analysis: we do not need to know how the
user's clip was edited, only what it contains and how it moves. Critically it
uses the IDENTICAL enum vocabulary -- that is what makes matching possible at
all. See docs/04-ai-pipeline.md part B.
"""

from .pipeline import ClipFeature, SegmentFeature, index_clip, usable_ranges

__version__ = "0.1.0"
__all__ = ["ClipFeature", "SegmentFeature", "index_clip", "usable_ranges", "__version__"]
