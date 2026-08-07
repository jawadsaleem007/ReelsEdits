"""ReelsEdits matcher -- assign user footage segments to blueprint slots.

The component with the least prior art and the largest user-visible impact.
See docs/09-clip-matching.md.
"""

from __future__ import annotations

from .scoring import fit, motion_compat, seq, subject_compat
from .solver import best_window, build_candidates, chain_dp, global_score, match, repair
from .types import Candidate, MatchResult, Segment, SlotAssignment

__version__ = "0.1.0"

__all__ = [
    "Candidate",
    "MatchResult",
    "Segment",
    "SlotAssignment",
    "best_window",
    "build_candidates",
    "chain_dp",
    "fit",
    "global_score",
    "match",
    "motion_compat",
    "repair",
    "seq",
    "subject_compat",
    "__version__",
]
