"""Tests for the Editing Blueprint models.

These are not coverage theatre. Each test pins an invariant that, if broken,
produces a specific bad outcome in the product -- named in the docstring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from reelsedits_common import (
    Assignment,
    AudioSection,
    AudioTrack,
    Blueprint,
    Canvas,
    CaptionMode,
    CaptionTrack,
    Compromise,
    CompromiseKind,
    ConfidenceBreakdown,
    Cut,
    CutMode,
    Degradation,
    EffectInstance,
    EffectType,
    EnergyCurve,
    MusicBinding,
    MusicStrategy,
    PacingProfile,
    Provenance,
    SectionKind,
    ShotScale,
    Slot,
    SlotRequirements,
    StyleProfile,
    Transition,
    TransitionType,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "schemas" / "blueprint.schema.json"
EXAMPLE_PATH = REPO_ROOT / "schemas" / "examples" / "moto-sunset-128bpm.json"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _audio(bpm: float = 128.0, n_beats: int = 64) -> AudioTrack:
    ibi = 60_000 / bpm
    grid = [round(420 + i * ibi) for i in range(n_beats)]
    return AudioTrack(
        bpm=bpm,
        beat_grid_ms=grid,
        downbeats_ms=grid[::4],
        sections=[
            AudioSection(kind=SectionKind.INTRO, t_in_ms=0, t_out_ms=7500, energy=0.32),
            AudioSection(kind=SectionKind.DROP, t_in_ms=7500, t_out_ms=30000, energy=0.94),
        ],
        energy_curve=EnergyCurve(hz=20, values=[0.1, 0.5, 0.9, 0.4]),
        music_binding=MusicBinding(
            strategy=MusicStrategy.CATALOGUE_MATCH,
            track_id="es_9182773",
            licence_id="lic_2026_08_a91f",
            match_score=0.94,
        ),
    )


def _minimal(**over) -> Blueprint:
    base = dict(
        id="bp_7fK2mQx91aBc",
        provenance=Provenance(
            analyzer_version="1.4.2",
            renderer_min_version="2.0.0",
            confidence=ConfidenceBreakdown(overall=0.83, beat_grid=0.96, grade=0.58),
        ),
        canvas=Canvas(width=1080, height=1920, fps=30, duration_ms=30000),
        audio=_audio(),
        style=StyleProfile(
            summary="Fast hard-cut edit locked to a 128 BPM grid.",
            pacing=PacingProfile(
                cuts_per_second=1.87, beat_lock_ratio=0.84, offset_mean_ms=-38.0
            ),
        ),
        slots=[
            Slot(index=0, t_in_ms=0, t_out_ms=1200, importance=1.0),
            Slot(index=1, t_in_ms=1200, t_out_ms=2400, importance=0.6),
        ],
        cuts=[Cut(index=0, t_ms=1200, mode=CutMode.ON_BEAT, beat_index=2, offset_ms=-38)],
    )
    base.update(over)
    return Blueprint(**base)


# ---------------------------------------------------------------------------
# schema parity
# ---------------------------------------------------------------------------


def test_json_schema_is_valid_draft_2020_12():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)


def test_example_validates_against_both_schema_and_models():
    """The published example must be valid under the normative JSON Schema AND
    the Pydantic implementation. Divergence between the two is the most likely
    source of a blueprint that passes CI and fails in the renderer."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text())
    example = json.loads(EXAMPLE_PATH.read_text())

    jsonschema.Draft202012Validator(schema).validate(example)
    bp = Blueprint.model_validate(example)
    assert bp.ebp_version == "1.0"
    assert len(bp.slots) > 5


def test_roundtrip_is_lossless():
    bp = _minimal()
    again = Blueprint.model_validate(json.loads(bp.model_dump_json(by_alias=True)))
    assert again.model_dump(by_alias=True) == bp.model_dump(by_alias=True)


def test_blueprint_stays_small():
    """Blueprints are kept forever (docs/03). If one grows past ~200KB, something
    is smuggling media into it."""
    bp = _minimal()
    assert len(bp.model_dump_json()) < 200_000


# ---------------------------------------------------------------------------
# the no-reference-media invariant
# ---------------------------------------------------------------------------


def test_extra_fields_are_rejected_everywhere():
    """extra='forbid' is what structurally prevents someone adding a field that
    could carry reference pixels or audio into the blueprint. docs/18."""
    with pytest.raises(ValidationError):
        _minimal(reference_thumbnail_b64="iVBORw0KGgo=")

    with pytest.raises(ValidationError):
        Canvas(width=1080, height=1920, fps=30, duration_ms=1000, source_frames=[1, 2])


def test_music_strategy_has_no_reference_reuse_option():
    """The absence of a 'reuse the reference track' strategy is deliberate."""
    values = {m.value for m in MusicStrategy}
    assert values == {"catalogue_match", "user_supplied", "silent", "generated"}


def test_catalogue_music_requires_a_licence():
    with pytest.raises(ValidationError, match="requires a licence_id"):
        MusicBinding(strategy=MusicStrategy.CATALOGUE_MATCH, track_id="t1")


def test_licensed_audio_constraint_cannot_be_disabled():
    """A tenant, a config file, or an API caller must not be able to turn this off."""
    with pytest.raises(ValidationError):
        _minimal(constraints={"require_licensed_audio": False})


# ---------------------------------------------------------------------------
# timeline invariants
# ---------------------------------------------------------------------------


def test_overlapping_slots_rejected():
    """Overlapping slots would make the renderer composite two clips it was
    never told to blend."""
    with pytest.raises(ValidationError, match="overlap"):
        _minimal(
            slots=[
                Slot(index=0, t_in_ms=0, t_out_ms=2000, importance=1.0),
                Slot(index=1, t_in_ms=1500, t_out_ms=3000, importance=0.6),
            ]
        )


def test_slot_index_must_match_position():
    with pytest.raises(ValidationError, match="must equal position"):
        _minimal(
            slots=[
                Slot(index=0, t_in_ms=0, t_out_ms=1200, importance=1.0),
                Slot(index=5, t_in_ms=1200, t_out_ms=2400, importance=0.6),
            ]
        )


def test_cuts_must_increase_in_time():
    with pytest.raises(ValidationError, match="not increasing"):
        _minimal(
            cuts=[
                Cut(index=0, t_ms=2000, mode=CutMode.FREE),
                Cut(index=1, t_ms=1000, mode=CutMode.FREE),
            ]
        )


def test_slot_shorter_than_min_shot_rejected():
    """Below ~180ms a shot reads as a flicker, not a shot."""
    with pytest.raises(ValidationError, match="below min_shot_ms"):
        _minimal(
            slots=[
                Slot(index=0, t_in_ms=0, t_out_ms=90, importance=1.0),
                Slot(index=1, t_in_ms=200, t_out_ms=2400, importance=0.6),
            ]
        )


def test_slots_cannot_exceed_canvas_duration():
    with pytest.raises(ValidationError, match="beyond canvas"):
        _minimal(canvas=Canvas(width=1080, height=1920, fps=30, duration_ms=2000),
                 slots=[
                     Slot(index=0, t_in_ms=0, t_out_ms=1200, importance=1.0),
                     Slot(index=1, t_in_ms=1200, t_out_ms=5000, importance=0.6),
                 ])


def test_audio_sections_cannot_overlap():
    with pytest.raises(ValidationError, match="overlap"):
        AudioTrack(
            bpm=120,
            beat_grid_ms=[0, 500, 1000],
            sections=[
                AudioSection(kind=SectionKind.INTRO, t_in_ms=0, t_out_ms=5000),
                AudioSection(kind=SectionKind.DROP, t_in_ms=3000, t_out_ms=9000),
            ],
            energy_curve=EnergyCurve(values=[0.1, 0.9]),
        )


def test_beat_grid_must_be_strictly_increasing():
    with pytest.raises(ValidationError, match="strictly increasing"):
        AudioTrack(
            bpm=120,
            beat_grid_ms=[0, 500, 500, 1000],
            sections=[AudioSection(kind=SectionKind.INTRO, t_in_ms=0, t_out_ms=5000)],
            energy_curve=EnergyCurve(values=[0.1, 0.9]),
        )


# ---------------------------------------------------------------------------
# dangling reference detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,payload,match",
    [
        ("transitions", [Transition(at_cut=99, type=TransitionType.FLASH, duration_ms=120)], "cut 99"),
        ("effects", [EffectInstance(type=EffectType.GLOW, scope="slot", slot=42)], "slot 42"),
        ("effects", [EffectInstance(type=EffectType.GLOW, scope="section", section=9)], "section 9"),
    ],
)
def test_dangling_references_rejected(field, payload, match):
    """A transition pointing at a cut that does not exist is silently ignored by
    a naive renderer -- producing output that is wrong in a way nobody notices."""
    with pytest.raises(ValidationError, match=match):
        _minimal(**{field: payload})


# ---------------------------------------------------------------------------
# transition semantics
# ---------------------------------------------------------------------------


def test_directional_transition_requires_direction():
    """A whip pan without a direction renders as an undirected smear, which reads
    as a bug rather than a transition."""
    with pytest.raises(ValidationError, match="requires direction_deg"):
        Transition(at_cut=0, type=TransitionType.WHIP_PAN, duration_ms=180)

    ok = Transition(at_cut=0, type=TransitionType.WHIP_PAN, duration_ms=180, direction_deg=-180)
    assert ok.direction_deg == -180


def test_non_hard_cut_requires_duration():
    with pytest.raises(ValidationError, match="requires duration_ms > 0"):
        Transition(at_cut=0, type=TransitionType.CROSS_DISSOLVE, duration_ms=0)

    assert Transition(at_cut=0, type=TransitionType.HARD_CUT, duration_ms=0).duration_ms == 0


def test_transition_type_capability_flags():
    assert TransitionType.WHIP_PAN.needs_direction
    assert TransitionType.WHIP_PAN.needs_motion
    assert not TransitionType.CROSS_DISSOLVE.needs_direction


# ---------------------------------------------------------------------------
# cut anchoring
# ---------------------------------------------------------------------------


def test_on_beat_cut_requires_beat_index():
    with pytest.raises(ValidationError, match="requires beat_index"):
        Cut(index=0, t_ms=1000, mode=CutMode.ON_BEAT)


def test_subdivided_cut_requires_subdivision():
    with pytest.raises(ValidationError, match="requires subdivision"):
        Cut(index=0, t_ms=1000, mode=CutMode.SUBDIVIDED, beat_index=2)


def test_free_cuts_need_no_anchor():
    assert Cut(index=0, t_ms=1000, mode=CutMode.FREE).beat_index is None


def test_negative_offset_is_permitted_and_expected():
    """Expert editors cut 20-60ms BEFORE the transient. If the schema rejected
    negative offsets we would quantise away the thing that makes cuts feel right."""
    c = Cut(index=0, t_ms=1162, mode=CutMode.ON_BEAT, beat_index=2, offset_ms=-38)
    assert c.offset_ms < 0


def test_nearest_beat_returns_signed_offset():
    a = _audio(bpm=120.0)          # ibi = 500ms, grid starts at 420
    idx, off = a.nearest_beat(1400)  # nearest beat is 1420
    assert a.beat_grid_ms[idx] == 1420
    assert off == -20


# ---------------------------------------------------------------------------
# degradation honesty
# ---------------------------------------------------------------------------


def test_compromises_without_degraded_flag_rejected():
    """A degraded render that does not say it is degraded is the worst output
    this system can produce -- the user ships it and blames their own eye."""
    with pytest.raises(ValidationError, match="does not say so"):
        Degradation(
            degraded=False,
            compromises=[
                Compromise(kind=CompromiseKind.SLOT_DROPPED, detail="no wide shot available")
            ],
        )


def test_degradation_with_flag_is_accepted():
    d = Degradation(
        degraded=True,
        coverage=0.78,
        compromises=[
            Compromise(
                kind=CompromiseKind.TRANSITION_SUBSTITUTED,
                detail="No lateral-motion segment for whip pan; used flash.",
                slot=14,
                severity="minor",
            )
        ],
    )
    assert d.compromises[0].severity == "minor"


# ---------------------------------------------------------------------------
# confidence handling
# ---------------------------------------------------------------------------


def test_low_confidence_subsystems_are_surfaced():
    """Grade at 0.58 must be labelled 'approximate' in the UI rather than
    presented as a measurement."""
    bp = _minimal()
    assert "grade" in bp.low_confidence_subsystems()
    assert "beat_grid" not in bp.low_confidence_subsystems()


def test_weakest_subsystem_identified():
    cb = ConfidenceBreakdown(overall=0.8, beat_grid=0.96, grade=0.41, speed=0.7)
    assert cb.weakest() == ("grade", 0.41)


# ---------------------------------------------------------------------------
# effect budget -- reproducing restraint
# ---------------------------------------------------------------------------


def test_effect_budget_violation_reported():
    """A style that uses two light leaks in 90s is characterised as much by the
    88 seconds without them."""
    bp = _minimal(
        style=StyleProfile(
            summary="restrained",
            pacing=PacingProfile(cuts_per_second=1.0, beat_lock_ratio=0.8),
            effect_budget={"light_leak": 1},
        ),
        effects=[
            EffectInstance(type=EffectType.LIGHT_LEAK, scope="section", section=0),
            EffectInstance(type=EffectType.LIGHT_LEAK, scope="section", section=0),
        ],
    )
    problems = bp.check_effect_budget()
    assert len(problems) == 1
    assert "exceeds budget" in problems[0]


def test_effect_budget_satisfied_is_silent():
    bp = _minimal(
        style=StyleProfile(
            summary="ok",
            pacing=PacingProfile(cuts_per_second=1.0, beat_lock_ratio=0.8),
            effect_budget={"light_leak": 2},
        ),
        effects=[EffectInstance(type=EffectType.LIGHT_LEAK, scope="section", section=0)],
    )
    assert bp.check_effect_budget() == []


# ---------------------------------------------------------------------------
# caption semantics
# ---------------------------------------------------------------------------


def test_karaoke_requires_active_word_style():
    with pytest.raises(ValidationError, match="requires active_word_style"):
        CaptionTrack(enabled=True, mode=CaptionMode.KARAOKE)


def test_text_object_content_defaults_to_none():
    """We transfer the style of a title, never its words."""
    from reelsedits_common import TextObject, TextStyle

    t = TextObject(id="t1", t_in_ms=0, t_out_ms=1000, style=TextStyle())
    assert t.content is None


# ---------------------------------------------------------------------------
# shot scale arithmetic
# ---------------------------------------------------------------------------


def test_shot_scale_distance():
    assert ShotScale.CLOSE.distance(ShotScale.MEDIUM) == 2
    assert ShotScale.CLOSE.distance(ShotScale.CLOSE) == 0
    assert ShotScale.CLOSE.distance(ShotScale.ANY) == 0
    assert ShotScale.EXTREME_CLOSE.distance(ShotScale.EXTREME_WIDE) == 5


def test_any_has_no_ordinal():
    with pytest.raises(ValueError, match="no ordinal"):
        _ = ShotScale.ANY.ordinal


# ---------------------------------------------------------------------------
# binding state
# ---------------------------------------------------------------------------


def test_free_blueprint_is_not_bound():
    assert _minimal().is_bound is False


def test_bound_blueprint_reports_bound():
    bp = _minimal()
    for s in bp.slots:
        s.assignment = Assignment(
            segment_id="seg_a1", in_ms=0, out_ms=1200, score=0.87,
            reason="wide static outdoor, matches golden-hour tone",
        )
    assert bp.is_bound is True


def test_assignment_requires_positive_duration():
    with pytest.raises(ValidationError, match="must exceed in_ms"):
        Assignment(segment_id="s1", in_ms=1000, out_ms=1000)


# ---------------------------------------------------------------------------
# music time mapping
# ---------------------------------------------------------------------------


def test_time_map_interpolates_between_anchors():
    """We warp the EDIT to the track, never the track to the edit."""
    mb = MusicBinding(
        strategy=MusicStrategy.CATALOGUE_MATCH,
        track_id="t1",
        licence_id="l1",
        time_map=[(0, 0), (22400, 21980), (45000, 44120)],
    )
    assert mb.map_time(0) == 0
    assert mb.map_time(22400) == 21980
    assert mb.map_time(11200) == 10990          # midpoint of first span
    assert mb.map_time(99999) == 44120          # clamped


def test_no_time_map_is_identity():
    mb = MusicBinding(strategy=MusicStrategy.SILENT)
    assert mb.map_time(1234) == 1234


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_energy_curve_interpolation():
    ec = EnergyCurve(hz=1.0, values=[0.0, 1.0, 0.0])
    assert ec.at(0) == pytest.approx(0.0)
    assert ec.at(1000) == pytest.approx(1.0)
    assert ec.at(500) == pytest.approx(0.5)
    assert ec.at(999999) == pytest.approx(0.0)


def test_canvas_frame_snapping():
    c = Canvas(width=1080, height=1920, fps=30, duration_ms=10000)
    assert c.snap_to_frame(0) == 0
    assert c.snap_to_frame(33) == 33      # 1 frame = 33.33ms
    assert c.snap_to_frame(50) == 67      # nearer frame 2


def test_section_lookup():
    a = _audio()
    assert a.section_at(1000).kind is SectionKind.INTRO
    assert a.section_at(20000).kind is SectionKind.DROP
    assert a.section_at(999999) is None


def test_slot_requirements_default_to_permissive():
    r = SlotRequirements()
    assert r.shot_scale is ShotScale.ANY
    assert r.min_quality == 0.5
