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
    assignment_extra: list[dict[str, Any]] = []

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

    # Unfilled slots: shorten before dropping.
    #
    # A slot is most often unfilled because it is LONGER than any available
    # segment -- a reference whose opening shot runs 7s against phone clips cut
    # into 2s pieces. Deleting it removed half the edit's runtime and the user
    # got a 6s video from a 14s reference with no explanation.
    #
    # Shortening keeps the shot, keeps the edit's shape, and loses only the
    # part we could not cover. The new length is snapped to the beat grid so
    # every following cut stays on the beat -- an arbitrary length would push
    # the whole remaining edit off-grid, which is worse than a shorter shot.
    if result.unfilled:
        from reelsedits_common import Compromise
        from reelsedits_common.enums import CompromiseKind

        bp.degradation.degraded = True
        bp.degradation.coverage = result.coverage

        still_unfilled: list[int] = []
        for idx in result.unfilled:
            slot = bp.slots[idx]
            rescued = _rescue_by_shortening(bp, slot, segments)
            if rescued is None:
                still_unfilled.append(idx)
                bp.degradation.compromises.append(Compromise(
                    kind=CompromiseKind.SLOT_DROPPED, slot=idx,
                    severity="major" if slot.importance > 0.8
                    else "moderate" if slot.importance > 0.6 else "minor",
                    detail=(f"Dropped a {slot.duration_ms / 1000:.1f}s shot: "
                            f"{describe_gap(slot)}"),
                ))
            else:
                new_ms, seg, in_ms, out_ms = rescued
                original_ms = slot.duration_ms
                slot.t_out_ms = slot.t_in_ms + new_ms
                slot.assignment = Assignment(
                    segment_id=seg.id, in_ms=in_ms, out_ms=out_ms,
                    score=0.4, reason=(
                        f"Shortened from {original_ms / 1000:.1f}s to "
                        f"{new_ms / 1000:.1f}s — no clip was long enough"
                    ),
                )
                bp.degradation.compromises.append(Compromise(
                    kind=CompromiseKind.QUALITY_BELOW_THRESHOLD, slot=idx,
                    severity="moderate",
                    detail=(f"Shot {idx} shortened from {original_ms / 1000:.1f}s to "
                            f"{new_ms / 1000:.1f}s: your longest usable clip for it "
                            f"was {new_ms / 1000:.1f}s."),
                ))
                assignment_extra.append({
                    "slot": idx, "segment_id": seg.id, "in_ms": in_ms,
                    "out_ms": out_ms, "score": 0.4,
                    "reason": slot.assignment.reason, "breakdown": {},
                })
        result.unfilled = still_unfilled

    assignment = sorted(
        [
            {"slot": a.slot_index, "segment_id": a.segment_id, "in_ms": a.in_ms,
             "out_ms": a.out_ms, "score": a.score, "reason": a.reason,
             "breakdown": a.breakdown}
            for a in result.assignments
        ] + assignment_extra,
        key=lambda a: a["slot"],
    )

    rendered_ms = sum(
        bp.slots[a["slot"]].duration_ms for a in assignment
    )
    summary = {
        "coverage": result.coverage,
        "confidence": result.overall_confidence,
        "solve_ms": result.solve_ms,
        "unfilled": result.unfilled,
        "violations": result.violations,
        # Surfaced so the UI can say "6s from a 14s reference, because N cuts
        # could not be filled" rather than leaving the user to notice.
        "reference_duration_ms": bp.canvas.duration_ms,
        "rendered_duration_ms": rendered_ms,
        "dropped_slots": len(result.unfilled),
        "compromises": [
            c.model_dump(mode="json") for c in bp.degradation.compromises
        ],
    }
    return bp.model_dump(mode="json", by_alias=True, exclude_none=True), assignment, summary


#: A shot below this reads as a flicker rather than a shot, so shortening past
#: it is not a rescue. Mirrors Constraints.min_shot_ms.
MIN_RESCUE_MS = 400


def _rescue_by_shortening(bp: BlueprintModel, slot, segments):
    """Find the longest segment that could fill a shortened version of this slot.

    Returns ``(new_duration_ms, segment, in_ms, out_ms)`` or None.

    The new duration is snapped DOWN to the beat grid. An arbitrary length would
    shift every subsequent cut off the beat, which is a worse outcome than a
    shorter shot -- the whole point of the blueprint is that cuts land on the
    grid (docs/06 §6.1).
    """
    original = slot.duration_ms

    # Candidates that satisfy everything except duration. Build a probe slot so
    # fit() judges them on their merits.
    probe = slot.model_copy(deep=True)
    probe.t_out_ms = probe.t_in_ms + MIN_RESCUE_MS
    viable = [
        s for s in segments
        if fit(probe, s)[0] > NEG_INF and s.usable_ms >= MIN_RESCUE_MS
    ]
    if not viable:
        return None

    best = max(viable, key=lambda s: (s.usable_ms, s.quality))
    available = min(best.usable_ms, original)
    if available < MIN_RESCUE_MS:
        return None

    # Longest beat-aligned duration that fits.
    grid = bp.audio.beat_grid_ms
    target_end = slot.t_in_ms + available
    aligned = [b for b in grid if slot.t_in_ms + MIN_RESCUE_MS <= b <= target_end]
    new_ms = (max(aligned) - slot.t_in_ms) if aligned else available

    if new_ms < MIN_RESCUE_MS or new_ms >= original:
        return None

    return new_ms, best, best.usable_in_ms, best.usable_in_ms + new_ms


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
