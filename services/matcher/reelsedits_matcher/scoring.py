"""Unary fit and pairwise sequence scoring.

The weights here are the hand-tuned v0. They are replaced by weights learned
from user swap data once we have ~5k preference pairs -- see docs/09 section 6.
Keeping them as named module constants (rather than inline magic numbers) is
what makes that swap a config change.
"""

from __future__ import annotations

import math

from reelsedits_common import Slot, SlotRequirements
from reelsedits_common.enums import (
    CameraHeight,
    CameraMotion,
    Composition,
    ShotScale,
    SubjectClass,
    TransitionType,
)

from .types import Candidate, Segment

NEG_INF = float("-inf")

# --- unary weights (sum to 1.0) --------------------------------------------
W_SCALE = 0.18
W_MOTION_CLASS = 0.16
W_MOTION_ENERGY = 0.20      # largest single weight; see docs/09 section 3.1
W_SUBJECT = 0.14
W_COMPOSITION = 0.08
W_HEIGHT = 0.06
W_QUALITY = 0.10
W_SEMANTIC = 0.08           # soft tiebreak ONLY, never a filter

# --- pairwise weights ------------------------------------------------------
P_SAME_SCALE = -0.35
P_ADJACENT_SCALE = 0.05
P_CONTRAST_SCALE = 0.20
P_JUMP_CUT = -0.80          # near-disqualifying, and correctly so
P_MOTION_MATCH_DIRECTIONAL = 0.30
P_MOTION_MATCH_NORMAL = 0.10
P_LUMA_JUMP_NORMAL = -0.30
P_LUMA_JUMP_ON_IMPACT = -0.10
P_SUBJECT_CONTINUITY = 0.12

THIRTY_DEGREE_RULE = 30.0


# ---------------------------------------------------------------------------
# compatibility helpers
# ---------------------------------------------------------------------------


def motion_compat(required: list[CameraMotion], actual: CameraMotion) -> float:
    """Camera-motion compatibility, by class rather than equality.

    A slot asking for ``pan_left`` is well served by ``truck_left`` or
    ``tracking`` -- the frame moves the same way and the cut reads the same.
    Requiring exact equality would reject most usable footage.
    """
    if CameraMotion.ANY in required or actual is CameraMotion.ANY:
        return 0.6
    if actual in required:
        return 1.0

    for req in required:
        if req.is_lateral and actual.is_lateral:
            same_side = (
                {req, actual} <= {CameraMotion.PAN_LEFT, CameraMotion.TRUCK_LEFT}
                or {req, actual} <= {CameraMotion.PAN_RIGHT, CameraMotion.TRUCK_RIGHT}
                or CameraMotion.TRACKING in (req, actual)
            )
            return 0.85 if same_side else 0.25
        if req.is_push and actual.is_push:
            same_dir = (
                {req, actual} <= {CameraMotion.ZOOM_IN, CameraMotion.DOLLY_IN}
                or {req, actual} <= {CameraMotion.ZOOM_OUT, CameraMotion.DOLLY_OUT}
            )
            return 0.9 if same_dir else 0.2
        if req is CameraMotion.STATIC and actual is CameraMotion.HANDHELD:
            return 0.45          # handheld reads as static-ish if gentle
        if req is CameraMotion.HANDHELD and actual is CameraMotion.STATIC:
            return 0.35
    return 0.15


#: Subject classes that can stand in for one another because they play the
#: same editorial role. This table is what lets a car reference render onto
#: motorcycle footage -- see docs/09 section 1.1.
SUBJECT_BRIDGES: dict[SubjectClass, set[SubjectClass]] = {
    SubjectClass.VEHICLE: {SubjectClass.MECHANICAL_DETAIL, SubjectClass.PRODUCT},
    SubjectClass.MECHANICAL_DETAIL: {SubjectClass.VEHICLE, SubjectClass.PRODUCT,
                                     SubjectClass.ABSTRACT},
    SubjectClass.PERSON_FACE: {SubjectClass.PERSON_BODY},
    SubjectClass.PERSON_BODY: {SubjectClass.PERSON_FACE, SubjectClass.PERSON_GROUP},
    SubjectClass.PERSON_GROUP: {SubjectClass.PERSON_BODY, SubjectClass.CROWD},
    SubjectClass.CROWD: {SubjectClass.PERSON_GROUP},
    SubjectClass.LANDSCAPE: {SubjectClass.SKY, SubjectClass.WATER,
                             SubjectClass.ARCHITECTURE},
    SubjectClass.SKY: {SubjectClass.LANDSCAPE},
    SubjectClass.WATER: {SubjectClass.LANDSCAPE},
    SubjectClass.PRODUCT: {SubjectClass.MECHANICAL_DETAIL, SubjectClass.FOOD},
    SubjectClass.FOOD: {SubjectClass.PRODUCT},
}


def subject_compat(required: list[SubjectClass], actual: SubjectClass) -> float:
    if SubjectClass.ANY in required or actual is SubjectClass.ANY:
        return 0.6
    if actual in required:
        return 1.0
    for req in required:
        if actual in SUBJECT_BRIDGES.get(req, set()):
            return 0.75
    return 0.2


def composition_compat(required: Composition, actual: Composition) -> float:
    if required is Composition.ANY or actual is Composition.ANY:
        return 0.6
    if required is actual:
        return 1.0
    mirrors = [
        {Composition.THIRDS_LEFT, Composition.THIRDS_RIGHT},
        {Composition.NEGATIVE_SPACE_LEFT, Composition.NEGATIVE_SPACE_RIGHT},
    ]
    if any({required, actual} <= m for m in mirrors):
        return 0.55          # mirrored composition still reads as deliberate
    return 0.3


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def angular_distance(a: float, b: float) -> float:
    """Smallest absolute angle between two bearings, in degrees."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


# ---------------------------------------------------------------------------
# unary fit
# ---------------------------------------------------------------------------


def fit(slot: Slot, seg: Segment) -> tuple[float, dict[str, float], str]:
    """Score one segment against one slot, ignoring sequence context.

    Returns ``(score, breakdown, reason)``. ``-inf`` means the segment fails a
    hard constraint and is excluded from candidacy entirely.
    """
    r: SlotRequirements = slot.requirements

    # --- hard constraints ---------------------------------------------------
    if seg.usable_ms < slot.duration_ms:
        return NEG_INF, {}, "too short"
    if seg.quality < r.min_quality:
        return NEG_INF, {}, f"quality {seg.quality:.2f} below {r.min_quality:.2f}"
    if r.requires_face and not seg.has_face:
        return NEG_INF, {}, "no face"
    if r.requires_speech and not seg.has_speech:
        return NEG_INF, {}, "no speech"
    if (
        r.shot_scale is not ShotScale.ANY
        and r.shot_scale.distance(seg.shot_scale) > r.shot_scale_tolerance
    ):
        return NEG_INF, {}, f"scale {seg.shot_scale.value} too far from {r.shot_scale.value}"

    # --- soft score ---------------------------------------------------------
    b: dict[str, float] = {}

    b["scale"] = 1.0 - (r.shot_scale.distance(seg.shot_scale) / 3.0)
    b["motion_class"] = motion_compat(r.camera_motion, seg.camera_motion)

    tol = max(r.motion_energy_tolerance, 0.05)
    b["motion_energy"] = max(0.0, 1.0 - abs(r.motion_energy - seg.motion_energy) / tol)

    b["subject"] = subject_compat(r.subject_class, seg.subject_class)
    b["composition"] = composition_compat(r.composition, seg.composition)
    b["height"] = (
        1.0
        if r.camera_height in (CameraHeight.ANY, seg.camera_height)
        else 0.3
    )
    b["quality"] = seg.quality
    b["semantic"] = (
        max(0.0, cosine(r.semantic_vec, seg.semantic_vec)) if r.semantic_vec else 0.5
    )

    score = (
        W_SCALE * b["scale"]
        + W_MOTION_CLASS * b["motion_class"]
        + W_MOTION_ENERGY * b["motion_energy"]
        + W_SUBJECT * b["subject"]
        + W_COMPOSITION * b["composition"]
        + W_HEIGHT * b["height"]
        + W_QUALITY * b["quality"]
        + W_SEMANTIC * b["semantic"]
    )
    return score, b, _explain(b, seg)


def _explain(b: dict[str, float], seg: Segment) -> str:
    """A one-line human reason, surfaced in the swap UI.

    An explained choice produces an informative correction; an unexplained one
    produces a random click. docs/09 section 6.
    """
    strengths = sorted(b.items(), key=lambda kv: -kv[1])[:2]
    weakest = min(b.items(), key=lambda kv: kv[1])
    label = {
        "scale": f"{seg.shot_scale.value} framing",
        "motion_class": f"{seg.camera_motion.value} camera",
        "motion_energy": "matching motion energy",
        "subject": f"{seg.subject_class.value} subject",
        "composition": f"{seg.composition.value} composition",
        "height": f"{seg.camera_height.value} angle",
        "quality": "good image quality",
        "semantic": "similar content",
    }
    good = " and ".join(label[k] for k, _ in strengths)
    if weakest[1] < 0.4:
        return f"{good}; weaker on {label[weakest[0]]}".capitalize()
    return good.capitalize()


# ---------------------------------------------------------------------------
# pairwise sequence score
# ---------------------------------------------------------------------------


def seq(
    prev: Candidate,
    curr: Candidate,
    *,
    transition_type: TransitionType | None = None,
    cut_on_impact: bool = False,
) -> float:
    """How well two consecutive assignments cut together."""
    s = 0.0
    a, c = prev.segment, curr.segment

    # Contrast: consecutive shots should differ in scale.
    delta = a.shot_scale.distance(c.shot_scale)
    if delta == 0:
        s += P_SAME_SCALE
    elif delta == 1:
        s += P_ADJACENT_SCALE
    else:
        s += P_CONTRAST_SCALE

    # 30-degree rule: cutting between near-identical angles of the same source
    # is the definition of a jump cut, and the commonest amateur mistake.
    if a.asset_id == c.asset_id:
        if angular_distance(a.camera_angle_deg, c.camera_angle_deg) < THIRTY_DEGREE_RULE:
            s += P_JUMP_CUT

    # Motion continuity. Directional transitions REQUIRE agreement; elsewhere
    # it is merely preferable.
    if a.motion_direction_deg is not None and c.motion_direction_deg is not None:
        agreement = math.cos(
            math.radians(a.motion_direction_deg - c.motion_direction_deg)
        )
        needs_dir = transition_type is not None and transition_type.needs_direction
        s += (P_MOTION_MATCH_DIRECTIONAL if needs_dir else P_MOTION_MATCH_NORMAL) * agreement

    # Exposure continuity. A big luma jump reads as an error mid-phrase and as
    # intent on a hard beat.
    luma_jump = abs(a.mean_luma - c.mean_luma)
    s += (P_LUMA_JUMP_ON_IMPACT if cut_on_impact else P_LUMA_JUMP_NORMAL) * luma_jump

    # Following one subject across a cut reads as narrative rather than collage.
    if a.subject_track_id and a.subject_track_id == c.subject_track_id:
        s += P_SUBJECT_CONTINUITY

    return s
