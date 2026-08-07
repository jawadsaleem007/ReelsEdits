"""Segment and assignment types for the matcher.

A ``Segment`` is a *usable range within a user clip*, not a whole file. Users
upload long takes and the good three seconds are in the middle; matching on
whole files throws that away.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reelsedits_common.enums import (
    CameraHeight,
    CameraMotion,
    Composition,
    ShotScale,
    SubjectClass,
)


@dataclass(slots=True)
class Segment:
    """A usable range within a user clip, with its editorial features."""

    id: str
    asset_id: str

    # Full extent of the sub-shot
    t_in_ms: int
    t_out_ms: int
    # Trimmed of shake, focus hunt and record-button boundaries. The matcher
    # may only select windows inside this range -- see docs/04 part B.
    usable_in_ms: int
    usable_out_ms: int

    shot_scale: ShotScale
    camera_motion: CameraMotion
    subject_class: SubjectClass
    camera_height: CameraHeight = CameraHeight.ANY
    composition: Composition = Composition.ANY

    motion_energy: float = 0.5
    motion_direction_deg: float | None = None
    quality: float = 0.7
    mean_luma: float = 0.5
    subject_track_id: str | None = None
    camera_angle_deg: float = 0.0

    has_face: bool = False
    has_speech: bool = False

    semantic_vec: list[float] = field(default_factory=list)

    @property
    def usable_ms(self) -> int:
        return self.usable_out_ms - self.usable_in_ms


@dataclass(slots=True)
class Candidate:
    """A (segment, window) pair scored against one slot."""

    segment: Segment
    in_ms: int
    out_ms: int
    fit: float
    reason: str = ""

    @property
    def duration_ms(self) -> int:
        return self.out_ms - self.in_ms


@dataclass(slots=True)
class SlotAssignment:
    slot_index: int
    segment_id: str
    in_ms: int
    out_ms: int
    score: float
    reason: str
    breakdown: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class MatchResult:
    assignments: list[SlotAssignment]
    unfilled: list[int]
    overall_confidence: float
    objective: float
    solve_ms: float
    violations: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        total = len(self.assignments) + len(self.unfilled)
        return len(self.assignments) / total if total else 0.0
