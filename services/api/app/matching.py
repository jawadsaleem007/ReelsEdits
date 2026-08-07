"""Coverage and matching against stored data.

Bridges the database rows to the pure matcher in `reelsedits_matcher`, which
knows nothing about persistence. Keeping that boundary sharp is what lets the
matcher be tested against synthetic segments and lets this layer be tested
against the database, without either needing the other.
"""

from __future__ import annotations

from typing import Any

from reelsedits_common import Assignment
from reelsedits_common import Blueprint as BlueprintModel
from reelsedits_common.enums import (
    CameraHeight,
    CameraMotion,
    Composition,
    ShotScale,
    SubjectClass,
)
from reelsedits_matcher import Segment as MatcherSegment
from reelsedits_matcher import fit, match
from reelsedits_matcher.scoring import NEG_INF

from .db import Blueprint, Project, ProjectAsset, Segment

COVERAGE_FLOOR = 0.55
COVERAGE_GOOD = 0.85


def _to_matcher(row: Segment) -> MatcherSegment:
    return MatcherSegment(
        id=row.id,
        asset_id=row.asset_id,
        t_in_ms=row.t_in_ms,
        t_out_ms=row.t_out_ms,
        usable_in_ms=row.usable_in_ms,
        usable_out_ms=row.usable_out_ms,
        shot_scale=ShotScale(row.shot_scale),
        camera_motion=CameraMotion(row.camera_motion),
        subject_class=SubjectClass(row.subject_class),
        camera_height=CameraHeight(row.camera_height),
        composition=Composition(row.composition),
        motion_energy=row.motion_energy,
        motion_direction_deg=row.motion_direction_deg,
        quality=row.quality,
        mean_luma=row.mean_luma,
        camera_angle_deg=row.camera_angle_deg,
        has_face=row.has_face,
        has_speech=row.has_speech,
    )


def project_segments(db, project: Project) -> list[MatcherSegment]:
    asset_ids = [
        r.asset_id for r in db.query(ProjectAsset)
        .filter(ProjectAsset.project_id == project.id).all()
    ]
    if not asset_ids:
        return []
    rows = db.query(Segment).filter(Segment.asset_id.in_(asset_ids)).all()
    return [_to_matcher(r) for r in rows]


def load_blueprint(db, project: Project) -> BlueprintModel:
    row = db.get(Blueprint, project.blueprint_id)
    if row is None:
        raise ValueError("project has no blueprint attached")
    return BlueprintModel.model_validate(row.doc)


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------


def describe_gap(slot) -> str:
    """A specific, actionable sentence — never a generic warning.

    "You need a shot with strong left-to-right motion" sends a user out to shoot
    for ten minutes. "Insufficient footage" churns them. Same information,
    entirely different outcome (docs/08 §9).
    """
    r = slot.requirements
    parts: list[str] = []

    if r.shot_scale is not ShotScale.ANY:
        parts.append(f"a {r.shot_scale.value.replace('_', ' ')} shot")
    else:
        parts.append("a shot")

    if r.motion_direction_deg is not None:
        direction = "left-to-right" if r.motion_direction_deg > 0 else "right-to-left"
        parts.append(f"with strong {direction} motion")
    elif r.motion_energy > 0.6:
        parts.append("with a lot of movement")
    elif r.motion_energy < 0.25:
        parts.append("that is fairly still")

    if r.camera_height is not CameraHeight.ANY:
        parts.append(f"from a {r.camera_height.value} angle")

    return f"You need {' '.join(parts)} — the style uses one at {slot.t_in_ms / 1000:.1f}s."


def compute_coverage(db, project: Project) -> dict[str, Any]:
    """How well the user's footage can satisfy the blueprint.

    Runs BEFORE any render is committed. A user who waits 90 seconds for a bad
    render has been robbed twice.
    """
    bp = load_blueprint(db, project)
    segments = project_segments(db, project)

    per_slot: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    weights, scores = [], []

    for slot in bp.slots:
        candidates = [s for s in segments if fit(slot, s)[0] > NEG_INF]
        # Two candidates is the floor for a slot to be considered covered: with
        # only one, any global constraint (reuse spacing, contrast) that rejects
        # it leaves the slot empty.
        cov = min(1.0, len(candidates) / 2.0)
        per_slot.append({"slot": slot.index, "coverage": round(cov, 3),
                         "candidates": len(candidates)})
        weights.append(slot.importance)
        scores.append(cov)
        if cov < 0.5:
            gaps.append({"slot": slot.index, "importance": slot.importance,
                         "message": describe_gap(slot)})

    overall = (
        sum(s * w for s, w in zip(scores, weights)) / sum(weights) if weights else 0.0
    )

    # Collapse gaps that ask for the same thing, so the user gets a shoot list
    # rather than twenty near-identical lines.
    merged: dict[str, dict[str, Any]] = {}
    for g in gaps:
        key = g["message"]
        entry = merged.setdefault(key, {"slots": [], "message": key, "severity": "minor",
                                        "suggested_action": "shoot"})
        entry["slots"].append(g["slot"])
        if g["importance"] > 0.8:
            entry["severity"] = "major"
        elif g["importance"] > 0.5 and entry["severity"] != "major":
            entry["severity"] = "moderate"

    verdict = ("good" if overall >= COVERAGE_GOOD
               else "degraded" if overall >= COVERAGE_FLOOR
               else "insufficient")

    return {
        "overall": round(overall, 3),
        "verdict": verdict,
        "per_slot": per_slot,
        "gaps": sorted(merged.values(), key=lambda g: -len(g["slots"])),
        "can_render": bool(segments),
        "requires_acknowledgement": overall < COVERAGE_FLOOR,
        "segment_count": len(segments),
    }


# ---------------------------------------------------------------------------
# binding
# ---------------------------------------------------------------------------


def bind_project(db, project: Project, force: bool = False) -> tuple[dict, list, dict]:
    """Assign segments to slots and return ``(bound_doc, assignment, summary)``.

    Honours slots the user has locked via a swap: those are pinned and the
    matcher works around them rather than optimising them away.
    """
    bp = load_blueprint(db, project)
    segments = project_segments(db, project)
    if not segments:
        raise ValueError("no indexed footage in this project")

    locked: dict[int, str] = {}
    if project.bound_doc:
        for slot in project.bound_doc.get("slots", []):
            a = slot.get("assignment")
            if a and a.get("locked"):
                locked[slot["index"]] = a["segment_id"]

    result = match(bp, segments, locked=locked or None)

    if result.coverage < COVERAGE_FLOOR and not force:
        raise ValueError(
            f"coverage {result.coverage:.0%} is below the {COVERAGE_FLOOR:.0%} floor; "
            "add footage or acknowledge degradation"
        )

    for a in result.assignments:
        bp.slots[a.slot_index].assignment = Assignment(
            segment_id=a.segment_id, in_ms=a.in_ms, out_ms=a.out_ms,
            score=a.score, reason=a.reason,
            locked=a.slot_index in locked,
        )

    # Drop unfilled slots so the renderer never sees a hole. The blueprint
    # records this as degradation rather than silently shortening the edit.
    if result.unfilled:
        from reelsedits_common import Compromise
        from reelsedits_common.enums import CompromiseKind

        bp.degradation.degraded = True
        bp.degradation.coverage = result.coverage
        for idx in result.unfilled:
            bp.degradation.compromises.append(Compromise(
                kind=CompromiseKind.SLOT_DROPPED, slot=idx,
                severity="moderate" if bp.slots[idx].importance > 0.6 else "minor",
                detail="No candidate met this slot's requirements.",
            ))

    assignment = [
        {"slot": a.slot_index, "segment_id": a.segment_id, "in_ms": a.in_ms,
         "out_ms": a.out_ms, "score": a.score, "reason": a.reason,
         "breakdown": a.breakdown}
        for a in result.assignments
    ]
    summary = {
        "coverage": result.coverage,
        "confidence": result.overall_confidence,
        "solve_ms": result.solve_ms,
        "unfilled": result.unfilled,
        "violations": result.violations,
    }
    return bp.model_dump(mode="json", by_alias=True, exclude_none=True), assignment, summary


def alternatives_for_slot(db, project: Project, slot_index: int, limit: int = 6):
    """Ranked alternatives with the reason each was ranked.

    The reason and breakdown are exposed deliberately: a user who can see *why*
    we ranked something makes a better-informed correction, and a better-informed
    correction is a better training label (docs/09 §6).
    """
    bp = load_blueprint(db, project)
    if slot_index >= len(bp.slots):
        raise ValueError(f"slot {slot_index} does not exist")

    slot = bp.slots[slot_index]
    scored = []
    for seg in project_segments(db, project):
        score, breakdown, reason = fit(slot, seg)
        if score == NEG_INF:
            continue
        scored.append({
            "segment_id": seg.id, "asset_id": seg.asset_id,
            "score": round(score, 4), "reason": reason,
            "breakdown": {k: round(v, 3) for k, v in breakdown.items()},
            "in_ms": seg.usable_in_ms,
            "out_ms": min(seg.usable_out_ms, seg.usable_in_ms + slot.duration_ms),
        })

    scored.sort(key=lambda x: -x["score"])
    for i, s in enumerate(scored, start=1):
        s["rank"] = i

    current = None
    if project.bound_doc:
        a = project.bound_doc["slots"][slot_index].get("assignment")
        current = a["segment_id"] if a else None

    return {
        "slot": {
            "index": slot_index,
            "t_in_ms": slot.t_in_ms,
            "t_out_ms": slot.t_out_ms,
            "importance": slot.importance,
            "requirements": slot.requirements.model_dump(mode="json", exclude_none=True),
            "current": current,
        },
        "alternatives": scored[:limit],
    }
