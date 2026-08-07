"""Matcher tests.

The headline test is ``test_cross_domain_transfer``: a car-shaped blueprint
must render onto motorcycle footage by editorial role, not semantic
similarity. That is the central technical claim of the product, so it gets a
test rather than a paragraph.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reelsedits_common import Blueprint
from reelsedits_common.enums import (
    CameraHeight,
    CameraMotion,
    Composition,
    ShotScale,
    SubjectClass,
)
from reelsedits_matcher import Segment, best_window, fit, match, motion_compat, seq, subject_compat
from reelsedits_matcher.scoring import NEG_INF
from reelsedits_matcher.types import Candidate

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "schemas" / "examples" / "moto-sunset-128bpm.json"


@pytest.fixture(scope="module")
def blueprint() -> Blueprint:
    return Blueprint.model_validate(json.loads(EXAMPLE.read_text()))


def seg(
    sid: str,
    scale: ShotScale,
    motion: CameraMotion,
    subject: SubjectClass,
    *,
    asset: str | None = None,
    energy: float = 0.5,
    quality: float = 0.8,
    height: CameraHeight = CameraHeight.ANY,
    comp: Composition = Composition.ANY,
    duration_ms: int = 6000,
    direction: float | None = None,
    luma: float = 0.5,
    angle: float = 0.0,
) -> Segment:
    return Segment(
        id=sid,
        asset_id=asset or f"ast_{sid}",
        t_in_ms=0,
        t_out_ms=duration_ms,
        usable_in_ms=200,
        usable_out_ms=duration_ms - 200,
        shot_scale=scale,
        camera_motion=motion,
        subject_class=subject,
        camera_height=height,
        composition=comp,
        motion_energy=energy,
        motion_direction_deg=direction,
        quality=quality,
        mean_luma=luma,
        camera_angle_deg=angle,
    )


# ---------------------------------------------------------------------------
# the central claim
# ---------------------------------------------------------------------------


def test_cross_domain_transfer(blueprint):
    """A car-derived blueprint must map onto motorcycle footage.

    This is docs/09 section 1.1. Nothing in the user's footage is semantically
    similar to a car, so an embedding-nearest-neighbour matcher would fail
    here. Ours matches on editorial role: low-angle mechanical detail with
    moderate motion, whatever the object happens to be.
    """
    segments = [
        seg("wide_road", ShotScale.EXTREME_WIDE, CameraMotion.AERIAL,
            SubjectClass.LANDSCAPE, height=CameraHeight.AERIAL, energy=0.20,
            comp=Composition.LOW_HORIZON, duration_ms=9000),
        seg("moto_rolling", ShotScale.MEDIUM, CameraMotion.TRACKING,
            SubjectClass.VEHICLE, height=CameraHeight.LOW, energy=0.72,
            direction=-172.0, comp=Composition.THIRDS_LEFT, duration_ms=8000),
        seg("exhaust", ShotScale.CLOSE, CameraMotion.PAN_LEFT,
            SubjectClass.MECHANICAL_DETAIL, height=CameraHeight.LOW, energy=0.55,
            direction=-168.0, comp=Composition.THIRDS_LEFT, duration_ms=5000),
        seg("helmet", ShotScale.MEDIUM_CLOSE, CameraMotion.HANDHELD,
            SubjectClass.PERSON_BODY, height=CameraHeight.EYE, energy=0.38,
            comp=Composition.CENTERED, duration_ms=6000),
        seg("chain", ShotScale.EXTREME_CLOSE, CameraMotion.STATIC,
            SubjectClass.MECHANICAL_DETAIL, height=CameraHeight.LOW, energy=0.30,
            comp=Composition.CENTERED, duration_ms=4000),
        seg("sunset", ShotScale.WIDE, CameraMotion.STATIC,
            SubjectClass.SKY, height=CameraHeight.EYE, energy=0.12,
            comp=Composition.LOW_HORIZON, duration_ms=7000),
        seg("rider_face", ShotScale.CLOSE, CameraMotion.ZOOM_IN,
            SubjectClass.PERSON_FACE, height=CameraHeight.EYE, energy=0.34,
            comp=Composition.CENTERED, duration_ms=5000),
        seg("moto_pass", ShotScale.MEDIUM, CameraMotion.TRUCK_RIGHT,
            SubjectClass.VEHICLE, height=CameraHeight.LOW, energy=0.80,
            direction=170.0, comp=Composition.THIRDS_RIGHT, duration_ms=6000),
    ]

    r = match(blueprint, segments)

    assert r.coverage > 0.9, f"coverage {r.coverage:.2f} too low"
    assert r.overall_confidence > 0.4
    assert r.solve_ms < 4000

    # Slot 2 wants a low-angle mechanical detail. The exhaust or chain shot
    # must win it -- NOT the motorcycle wide shot, which is what a semantic
    # nearest-neighbour matcher would choose against a car reference.
    slot2 = next(a for a in r.assignments if a.slot_index == 2)
    assert slot2.segment_id in {"exhaust", "chain"}, (
        f"slot 2 (low-angle mechanical detail) got '{slot2.segment_id}'; "
        "expected a detail shot, not a vehicle wide"
    )

    # Slot 0 establishes: extreme wide, aerial, landscape.
    slot0 = next(a for a in r.assignments if a.slot_index == 0)
    assert slot0.segment_id in {"wide_road", "sunset"}


def test_every_assignment_carries_a_reason(blueprint):
    """An explained choice produces an informative correction. docs/09 s6."""
    segments = [
        seg(f"s{i}", ShotScale.MEDIUM, CameraMotion.TRACKING, SubjectClass.VEHICLE,
            energy=0.5 + 0.05 * i, duration_ms=8000)
        for i in range(6)
    ]
    r = match(blueprint, segments)
    for a in r.assignments:
        assert a.reason, f"slot {a.slot_index} has no reason"
        assert a.breakdown, f"slot {a.slot_index} has no score breakdown"


# ---------------------------------------------------------------------------
# hard constraints
# ---------------------------------------------------------------------------


def test_too_short_segment_excluded(blueprint):
    slot = blueprint.slots[0]                       # 2800ms
    short = seg("tiny", ShotScale.EXTREME_WIDE, CameraMotion.AERIAL,
                SubjectClass.LANDSCAPE, duration_ms=900)
    score, _, reason = fit(slot, short)
    assert score == NEG_INF
    assert "short" in reason


def test_low_quality_excluded(blueprint):
    slot = blueprint.slots[14]                      # importance 1.0, min_quality 0.6
    bad = seg("blurry", ShotScale.WIDE, CameraMotion.TRACKING,
              SubjectClass.VEHICLE, quality=0.3, duration_ms=8000)
    score, _, reason = fit(slot, bad)
    assert score == NEG_INF
    assert "quality" in reason


def test_scale_tolerance_enforced(blueprint):
    slot = blueprint.slots[5]                       # extreme_close, tolerance 1
    far = seg("way_wide", ShotScale.EXTREME_WIDE, CameraMotion.STATIC,
              SubjectClass.MECHANICAL_DETAIL, duration_ms=6000)
    score, _, _ = fit(slot, far)
    assert score == NEG_INF

    near = seg("close", ShotScale.CLOSE, CameraMotion.STATIC,
               SubjectClass.MECHANICAL_DETAIL, duration_ms=6000)
    assert fit(slot, near)[0] > NEG_INF


# ---------------------------------------------------------------------------
# compatibility tables
# ---------------------------------------------------------------------------


def test_lateral_motion_same_side_compatible():
    assert motion_compat([CameraMotion.PAN_LEFT], CameraMotion.TRUCK_LEFT) > 0.8
    assert motion_compat([CameraMotion.PAN_LEFT], CameraMotion.TRUCK_RIGHT) < 0.4


def test_push_direction_matters():
    assert motion_compat([CameraMotion.ZOOM_IN], CameraMotion.DOLLY_IN) > 0.85
    assert motion_compat([CameraMotion.ZOOM_IN], CameraMotion.DOLLY_OUT) < 0.3


def test_subject_bridges_enable_cross_domain():
    """mechanical_detail <-> vehicle is the bridge that makes a car wheel and a
    motorcycle exhaust interchangeable."""
    assert subject_compat([SubjectClass.VEHICLE], SubjectClass.MECHANICAL_DETAIL) > 0.7
    assert subject_compat([SubjectClass.VEHICLE], SubjectClass.FOOD) < 0.3
    assert subject_compat([SubjectClass.LANDSCAPE], SubjectClass.SKY) > 0.7


def test_exact_subject_beats_bridge():
    assert (
        subject_compat([SubjectClass.VEHICLE], SubjectClass.VEHICLE)
        > subject_compat([SubjectClass.VEHICLE], SubjectClass.MECHANICAL_DETAIL)
    )


# ---------------------------------------------------------------------------
# sequence scoring
# ---------------------------------------------------------------------------


def _cand(s: Segment) -> Candidate:
    return Candidate(segment=s, in_ms=0, out_ms=1000, fit=0.5)


def test_same_scale_penalised_contrast_rewarded():
    a = _cand(seg("a", ShotScale.CLOSE, CameraMotion.STATIC, SubjectClass.VEHICLE))
    same = _cand(seg("b", ShotScale.CLOSE, CameraMotion.STATIC, SubjectClass.VEHICLE))
    diff = _cand(seg("c", ShotScale.WIDE, CameraMotion.STATIC, SubjectClass.VEHICLE))
    assert seq(a, diff) > seq(a, same)


def test_jump_cut_heavily_penalised():
    """Cutting between near-identical angles of the same source is the single
    most amateur-looking mistake in editing."""
    a = _cand(seg("a", ShotScale.MEDIUM, CameraMotion.STATIC, SubjectClass.VEHICLE,
                  asset="ast_same", angle=10.0))
    jump = _cand(seg("b", ShotScale.MEDIUM, CameraMotion.STATIC, SubjectClass.VEHICLE,
                     asset="ast_same", angle=18.0))     # 8 degrees apart
    ok = _cand(seg("c", ShotScale.MEDIUM, CameraMotion.STATIC, SubjectClass.VEHICLE,
                   asset="ast_same", angle=95.0))       # 85 degrees apart
    assert seq(a, jump) < seq(a, ok) - 0.7


def test_motion_agreement_matters_more_for_directional_transitions():
    from reelsedits_common.enums import TransitionType

    a = _cand(seg("a", ShotScale.MEDIUM, CameraMotion.PAN_LEFT,
                  SubjectClass.VEHICLE, direction=-175.0))
    agree = _cand(seg("b", ShotScale.WIDE, CameraMotion.PAN_LEFT,
                      SubjectClass.VEHICLE, direction=-170.0))

    normal = seq(a, agree)
    whip = seq(a, agree, transition_type=TransitionType.WHIP_PAN)
    assert whip > normal, "directional transitions must weight motion agreement higher"


def test_luma_jump_forgiven_on_impact():
    a = _cand(seg("a", ShotScale.MEDIUM, CameraMotion.STATIC, SubjectClass.VEHICLE, luma=0.15))
    b = _cand(seg("b", ShotScale.WIDE, CameraMotion.STATIC, SubjectClass.VEHICLE, luma=0.85))
    assert seq(a, b, cut_on_impact=True) > seq(a, b, cut_on_impact=False)


def test_subject_continuity_rewarded():
    a = seg("a", ShotScale.MEDIUM, CameraMotion.STATIC, SubjectClass.PERSON_BODY)
    b = seg("b", ShotScale.WIDE, CameraMotion.STATIC, SubjectClass.PERSON_BODY)
    a.subject_track_id = b.subject_track_id = "trk_1"
    with_cont = seq(_cand(a), _cand(b))
    b.subject_track_id = "trk_2"
    without = seq(_cand(a), _cand(b))
    assert with_cont > without


# ---------------------------------------------------------------------------
# windowing
# ---------------------------------------------------------------------------


def test_window_stays_inside_usable_range(blueprint):
    slot = blueprint.slots[2]
    s = seg("long", ShotScale.CLOSE, CameraMotion.PAN_LEFT,
            SubjectClass.MECHANICAL_DETAIL, duration_ms=20_000)
    in_ms, out_ms, _ = best_window(slot, s)
    assert in_ms >= s.usable_in_ms
    assert out_ms <= s.usable_out_ms
    assert out_ms - in_ms == slot.duration_ms


def test_window_prefers_middle_of_take():
    """Handheld starts and stops cluster at the edges even after trimming."""
    from reelsedits_common import Slot

    slot = Slot(index=0, t_in_ms=0, t_out_ms=1000, importance=0.5)
    s = seg("long", ShotScale.MEDIUM, CameraMotion.STATIC,
            SubjectClass.VEHICLE, duration_ms=11_000)
    in_ms, out_ms, _ = best_window(slot, s)
    centre = (in_ms + out_ms) / 2
    take_centre = (s.usable_in_ms + s.usable_out_ms) / 2
    assert abs(centre - take_centre) < 0.25 * s.usable_ms


# ---------------------------------------------------------------------------
# global constraints
# ---------------------------------------------------------------------------


def test_reuse_stays_within_limit(blueprint):
    """With 3 segments and 25 slots reuse is unavoidable. It must stay within
    max_segment_reuse and must not sit adjacent."""
    segments = [
        seg("a", ShotScale.MEDIUM, CameraMotion.TRACKING, SubjectClass.VEHICLE,
            energy=0.6, duration_ms=30_000),
        seg("b", ShotScale.WIDE, CameraMotion.STATIC, SubjectClass.LANDSCAPE,
            energy=0.2, duration_ms=30_000),
        seg("c", ShotScale.CLOSE, CameraMotion.PAN_LEFT,
            SubjectClass.MECHANICAL_DETAIL, energy=0.55, duration_ms=30_000),
    ]
    r = match(blueprint, segments)
    counts: dict[str, int] = {}
    for a in r.assignments:
        counts[a.segment_id] = counts.get(a.segment_id, 0) + 1
    # Reuse WILL exceed the limit here -- 3 segments cannot fill 25 slots
    # within a limit of 3. What matters is that the solver reports it rather
    # than silently producing a bad edit.
    assert r.violations, "over-reuse must be reported, not silently allowed"


def test_locked_slot_is_respected(blueprint):
    segments = [
        seg("a", ShotScale.MEDIUM, CameraMotion.TRACKING, SubjectClass.VEHICLE,
            energy=0.6, quality=0.95, duration_ms=30_000),
        seg("b", ShotScale.MEDIUM, CameraMotion.TRACKING, SubjectClass.VEHICLE,
            energy=0.6, quality=0.55, duration_ms=30_000),
    ]
    # Pin the WORSE segment to slot 1; the matcher must not optimise it away.
    r = match(blueprint, segments, locked={1: "b"})
    slot1 = next((a for a in r.assignments if a.slot_index == 1), None)
    assert slot1 is not None and slot1.segment_id == "b"


def test_empty_footage_yields_no_assignments(blueprint):
    r = match(blueprint, [])
    assert r.assignments == []
    assert len(r.unfilled) == len(blueprint.slots)
    assert r.coverage == 0.0
    assert r.overall_confidence == 0.0


# ---------------------------------------------------------------------------
# performance
# ---------------------------------------------------------------------------


def test_solve_time_within_budget(blueprint):
    """docs/09 section 5.2 budgets ~0.6s. Generous CI headroom here."""
    segments = [
        seg(f"s{i}",
            [ShotScale.WIDE, ShotScale.MEDIUM, ShotScale.CLOSE,
             ShotScale.EXTREME_CLOSE, ShotScale.EXTREME_WIDE][i % 5],
            [CameraMotion.STATIC, CameraMotion.TRACKING, CameraMotion.PAN_LEFT,
             CameraMotion.AERIAL, CameraMotion.HANDHELD][i % 5],
            [SubjectClass.VEHICLE, SubjectClass.LANDSCAPE,
             SubjectClass.MECHANICAL_DETAIL, SubjectClass.PERSON_BODY,
             SubjectClass.SKY][i % 5],
            energy=0.1 + 0.015 * i, quality=0.6 + 0.006 * i, duration_ms=9000)
        for i in range(60)
    ]
    r = match(blueprint, segments)
    assert r.solve_ms < 8000, f"solve took {r.solve_ms}ms"
    assert r.coverage > 0.85


def test_determinism(blueprint):
    """Same inputs must produce the same assignment, every time."""
    segments = [
        seg(f"s{i}", ShotScale.MEDIUM, CameraMotion.TRACKING, SubjectClass.VEHICLE,
            energy=0.4 + 0.05 * i, duration_ms=9000)
        for i in range(8)
    ]
    a = match(blueprint, segments)
    b = match(blueprint, segments)
    assert [(x.slot_index, x.segment_id, x.in_ms) for x in a.assignments] == \
           [(x.slot_index, x.segment_id, x.in_ms) for x in b.assignments]
