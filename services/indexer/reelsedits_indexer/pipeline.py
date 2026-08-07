"""User footage indexing.

Budget: 6s per clip, heavily batched. 24 clips index in ~6s wall-clock across
a 6-worker pool at batch 4, not 144s -- the work is embarrassingly parallel and
the user is watching.

The output vocabulary is IDENTICAL to the reference analyser's. If the analyser
emits ``motorbike`` and the indexer emits ``motorcycle``, structural matching
silently stops working and degrades to embedding similarity, which is the wrong
objective (docs/09 section 1.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reelsedits_common.enums import (
    CameraHeight,
    CameraMotion,
    Composition,
    NarrativeRole,
    ShotScale,
    SubjectClass,
)

#: Usability floor for a 250ms window. Contiguous windows above this become
#: usable_ranges, and the matcher may only cut inside them.
USABILITY_THRESHOLD = 0.55
WINDOW_MS = 250


@dataclass(slots=True)
class SegmentFeature:
    """A usable sub-shot within a clip -- the matcher's real unit of work.

    Users upload long takes and the good three seconds are in the middle.
    Matching on whole files throws that away.
    """

    id: str
    asset_id: str
    t_in_ms: int
    t_out_ms: int
    usable_in_ms: int
    usable_out_ms: int

    shot_scale: ShotScale
    camera_motion: CameraMotion
    camera_height: CameraHeight
    subject_class: SubjectClass
    composition: Composition
    narrative_role: NarrativeRole = NarrativeRole.ANY

    motion_energy: float = 0.5
    motion_direction_deg: float | None = None
    quality: float = 0.7
    subject_area_ratio: float = 0.1
    mean_luma: float = 0.5
    subject_track_id: str | None = None
    camera_angle_deg: float = 0.0

    has_face: bool = False
    has_speech: bool = False

    semantic_vec: list[float] = field(default_factory=list)
    motion_vec: list[float] = field(default_factory=list)


@dataclass(slots=True)
class ClipFeature:
    asset_id: str
    indexer_version: str
    quality_overall: float
    sharpness: float
    exposure_score: float
    shake_severity: float
    noise_level: float
    is_indoor: bool | None
    scene_category: str | None
    time_of_day: str | None
    weather: str | None
    has_face: bool
    has_speech: bool
    transcript: list[dict] = field(default_factory=list)
    segments: list[SegmentFeature] = field(default_factory=list)
    gpu_seconds: float = 0.0


def usable_ranges(
    windows: list[dict[str, float]], threshold: float = USABILITY_THRESHOLD
) -> list[tuple[int, int]]:
    """Collapse per-window usability scores into contiguous usable intervals.

    This single mechanism removes most of the amateur feel from output: the
    shaky half-second where someone presses record, the focus hunt at the start
    of a take, the frame where a hand crosses the lens.

    ``windows`` items carry ``t_ms`` and the component scores below.
    """
    ranges: list[tuple[int, int]] = []
    start: int | None = None

    for w in windows:
        score = (
            0.28 * w["sharpness"]
            + 0.20 * w["exposure_ok"]
            + 0.22 * (1.0 - w["shake_severity"])
            + 0.18 * w["subject_present"]
            + 0.12 * (1.0 - w["occlusion"])
            - 0.30 * w.get("near_clip_boundary", 0.0)   # record-button wobble
            - 0.25 * w.get("focus_hunting", 0.0)
            - 0.10 * w.get("mic_handling_noise", 0.0)
        )
        if score >= threshold:
            if start is None:
                start = int(w["t_ms"])
        elif start is not None:
            ranges.append((start, int(w["t_ms"])))
            start = None

    if start is not None and windows:
        ranges.append((start, int(windows[-1]["t_ms"]) + WINDOW_MS))

    # Discard slivers -- a 200ms usable range is not worth cutting to.
    return [(a, b) for a, b in ranges if b - a >= 400]


async def index_clip(asset_id: str, path: str, indexer_version: str) -> ClipFeature:
    """Index one user clip into ClipFeature plus its usable segments."""
    # TODO: probe -- reject unsupported codecs BEFORE any GPU spend
    # TODO: quality -- Laplacian variance, exposure histogram, noise,
    #       blockiness, shake severity, focus consistency -> usable_ranges()
    # TODO: sub-shot segmentation with the SAME SBD models as the analyser;
    #       a 40s "clip" is often five usable shots
    # TODO: VLM semantics under constrained decoding against the shared enums
    # TODO: SAM 3 concept segmentation -> masks, subject_area_ratio -> ShotScale
    # TODO: faces -- detection, expression, gaze; identity ephemeral only
    # TODO: SEA-RAFT motion -> camera motion class, energy curve, direction
    # TODO: composition -- thirds, symmetry, negative space, horizon
    # TODO: native colour statistics PRE-grade (needed to compute the delta to
    #       reach the reference look)
    # TODO: ASR -> word-level transcript; ambient class; loudness
    # TODO: embeddings -> Qdrant with filterable payload
    raise NotImplementedError
