#!/usr/bin/env python3
"""Generate the canonical example blueprint.

Building the example from the Pydantic models (rather than hand-writing JSON)
guarantees it stays valid as the models evolve, and it doubles as executable
documentation of how a real blueprint is shaped.

    python schemas/examples/build_example.py

Writes ``moto-sunset-128bpm.json`` next to this file and validates it against
the normative JSON Schema.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "services" / "common"))

from reelsedits_common import (  # noqa: E402
    AudioSection, AudioTrack, Blueprint, Canvas, CaptionMode, CaptionTrack,
    Canvas as _C, ConfidenceBreakdown, Constraints, Cut, CutMode, EffectInstance,
    EffectType, EnergyCurve, Grade, GradeMatchTarget, GradeParams, HslBand, Impact,
    Keyframe, MotionTrack, MusicBinding, MusicStrategy, PacingProfile, PaletteColor,
    Position, Provenance, ReframeTrack, SfxEvent, Slot, SlotRequirements, SpeedMode,
    SpeedTrack, SplitTone, StyleProfile, TextAnimation, TextAnimationKind, TextStyle,
    Transition, TransitionType,
)
from reelsedits_common.enums import (  # noqa: E402
    CameraHeight, CameraMotion, Composition, FontFamily, NarrativeRole,
    SectionKind, ShotScale, SubjectClass,
)

BPM = 128.0
IBI = 60_000 / BPM          # 468.75 ms
GRID_START = 420
DURATION = 45_000


def beat(i: int) -> int:
    return round(GRID_START + i * IBI)


# ---------------------------------------------------------------------------
# audio -- the rhythmic skeleton
# ---------------------------------------------------------------------------

N_BEATS = int((DURATION - GRID_START) / IBI)
grid = [beat(i) for i in range(N_BEATS)]

sections = [
    AudioSection(kind=SectionKind.INTRO, t_in_ms=0, t_out_ms=7_500,
                 energy=0.32, target_cut_density=0.9, label="Establishing, ambient"),
    AudioSection(kind=SectionKind.BUILD, t_in_ms=7_500, t_out_ms=22_400,
                 energy=0.61, target_cut_density=1.7, label="Accelerating toward the drop"),
    AudioSection(kind=SectionKind.DROP, t_in_ms=22_400, t_out_ms=41_000,
                 energy=0.94, target_cut_density=2.6, label="Peak energy, hardest cutting"),
    AudioSection(kind=SectionKind.OUTRO, t_in_ms=41_000, t_out_ms=45_000,
                 energy=0.40, target_cut_density=0.7, label="Resolve, long hold"),
]

energy_values = (
    [0.10 + 0.22 * (i / 150) for i in range(150)]      # intro
    + [0.32 + 0.62 * (i / 298) for i in range(298)]    # build
    + [0.94 - 0.04 * (i / 372) for i in range(372)]    # drop
    + [0.90 - 0.50 * (i / 80) for i in range(80)]      # outro
)

audio = AudioTrack(
    bpm=BPM,
    bpm_curve=[(0, 128.0), (41_000, 128.0)],
    time_signature="4/4",
    beat_grid_ms=grid,
    downbeats_ms=grid[::4],
    sections=sections,
    energy_curve=EnergyCurve(hz=20, values=[round(min(1.0, max(0.0, v)), 3) for v in energy_values]),
    impacts=[
        Impact(t_ms=7_500, strength=0.55, kind="hit"),
        Impact(t_ms=22_400, strength=1.0, kind="drop"),
        Impact(t_ms=31_700, strength=0.72, kind="hit"),
    ],
    sfx=[
        SfxEvent(t_ms=21_500, **{"class": "riser"}, duration_ms=900, gain_db=-8, bound_to_cut=14),
        SfxEvent(t_ms=22_400, **{"class": "sub_drop"}, duration_ms=1200, gain_db=-4),
        SfxEvent(t_ms=12_180, **{"class": "whoosh"}, duration_ms=280, gain_db=-11, bound_to_cut=7),
    ],
    mood=["driving", "euphoric", "nostalgic"],
    genre=["electronic", "future_bass"],
    music_binding=MusicBinding(
        strategy=MusicStrategy.CATALOGUE_MATCH,
        track_id="cat_es_9182773",
        licence_id="lic_2026_08_a91f3c",
        match_score=0.94,
        # The catalogue track is 127.4 BPM with its drop at 21.98s. We warp the
        # EDIT to the track, not the track to the edit.
        time_map=[(0, 0), (7_500, 7_460), (22_400, 21_980), (41_000, 40_310), (45_000, 44_120)],
    ),
)

# ---------------------------------------------------------------------------
# slots
# ---------------------------------------------------------------------------

SLOT_SPECS = [
    # (t_in, t_out, section, importance, scale, motion, height, subject, role, comp, energy, dir, hint)
    (0,      2_800, 0, 0.95, ShotScale.EXTREME_WIDE, [CameraMotion.AERIAL, CameraMotion.STATIC],
     CameraHeight.AERIAL, [SubjectClass.LANDSCAPE], NarrativeRole.ESTABLISH, Composition.LOW_HORIZON,
     0.18, None, "wide establishing landscape at golden hour, low horizon"),
    (2_800,  5_150, 0, 0.60, ShotScale.MEDIUM, [CameraMotion.TRACKING],
     CameraHeight.LOW, [SubjectClass.VEHICLE], NarrativeRole.ESTABLISH, Composition.THIRDS_RIGHT,
     0.44, 12.0, "vehicle moving through frame, tracked from low angle"),
    (5_150,  7_500, 0, 0.55, ShotScale.CLOSE, [CameraMotion.STATIC, CameraMotion.PAN_LEFT],
     CameraHeight.LOW, [SubjectClass.MECHANICAL_DETAIL], NarrativeRole.DETAIL, Composition.THIRDS_LEFT,
     0.51, -170.0, "low-angle mechanical detail, shallow depth of field"),
    (7_500,  9_100, 1, 0.70, ShotScale.WIDE, [CameraMotion.PAN_RIGHT],
     CameraHeight.EYE, [SubjectClass.LANDSCAPE, SubjectClass.VEHICLE], NarrativeRole.ACTION_BEAT,
     Composition.NEGATIVE_SPACE_LEFT, 0.58, 8.0, "wide action, subject entering from left"),
    (9_100, 10_400, 1, 0.50, ShotScale.MEDIUM_CLOSE, [CameraMotion.HANDHELD],
     CameraHeight.EYE, [SubjectClass.PERSON_BODY], NarrativeRole.REACTION, Composition.CENTERED,
     0.39, None, "handheld medium shot of rider"),
    (10_400, 11_500, 1, 0.45, ShotScale.EXTREME_CLOSE, [CameraMotion.STATIC],
     CameraHeight.LOW, [SubjectClass.MECHANICAL_DETAIL], NarrativeRole.DETAIL, Composition.CENTERED,
     0.28, None, "extreme close texture detail"),
    (11_500, 12_180, 1, 0.65, ShotScale.MEDIUM, [CameraMotion.TRACKING, CameraMotion.TRUCK_LEFT],
     CameraHeight.LOW, [SubjectClass.VEHICLE], NarrativeRole.ACTION_BEAT, Composition.THIRDS_LEFT,
     0.71, -175.0, "fast lateral tracking shot, strong left motion"),
    (12_180, 13_300, 1, 0.62, ShotScale.CLOSE, [CameraMotion.PAN_RIGHT],
     CameraHeight.LOW, [SubjectClass.MECHANICAL_DETAIL], NarrativeRole.DETAIL, Composition.THIRDS_RIGHT,
     0.66, 168.0, "detail shot with rightward motion, matched to previous whip"),
    (13_300, 15_650, 1, 0.55, ShotScale.WIDE, [CameraMotion.STATIC],
     CameraHeight.EYE, [SubjectClass.LANDSCAPE], NarrativeRole.TRANSITION_SHOT, Composition.SYMMETRIC,
     0.22, None, "static wide breather before the build resumes"),
    (15_650, 16_580, 1, 0.58, ShotScale.MEDIUM_CLOSE, [CameraMotion.ZOOM_IN, CameraMotion.DOLLY_IN],
     CameraHeight.EYE, [SubjectClass.PERSON_FACE], NarrativeRole.REACTION, Composition.CENTERED,
     0.35, None, "push in on face"),
    (16_580, 17_520, 1, 0.52, ShotScale.CLOSE, [CameraMotion.HANDHELD],
     CameraHeight.LOW, [SubjectClass.MECHANICAL_DETAIL], NarrativeRole.DETAIL, Composition.THIRDS_LEFT,
     0.61, None, "handheld detail"),
    (17_520, 19_400, 1, 0.68, ShotScale.MEDIUM, [CameraMotion.TRACKING],
     CameraHeight.LOW, [SubjectClass.VEHICLE], NarrativeRole.ACTION_BEAT, Composition.THIRDS_RIGHT,
     0.74, 15.0, "tracking action, accelerating"),
    (19_400, 20_800, 1, 0.60, ShotScale.WIDE, [CameraMotion.AERIAL],
     CameraHeight.AERIAL, [SubjectClass.LANDSCAPE, SubjectClass.VEHICLE], NarrativeRole.REVEAL,
     Composition.LOW_HORIZON, 0.48, None, "aerial reveal"),
    (20_800, 22_362, 1, 0.85, ShotScale.MEDIUM_CLOSE, [CameraMotion.ZOOM_IN],
     CameraHeight.EYE, [SubjectClass.PERSON_FACE, SubjectClass.VEHICLE], NarrativeRole.HOOK,
     Composition.CENTERED, 0.55, None, "tension shot immediately before the drop, pushing in"),
    (22_362, 23_580, 2, 1.00, ShotScale.WIDE, [CameraMotion.TRACKING, CameraMotion.AERIAL],
     CameraHeight.LOW, [SubjectClass.VEHICLE], NarrativeRole.PAYOFF, Composition.THIRDS_LEFT,
     0.88, None, "the drop shot -- highest energy, widest, most dynamic available"),
    (23_580, 24_520, 2, 0.72, ShotScale.CLOSE, [CameraMotion.TRACKING],
     CameraHeight.GROUND, [SubjectClass.MECHANICAL_DETAIL], NarrativeRole.DETAIL, Composition.CENTERED,
     0.82, None, "ground-level detail at speed"),
    (24_520, 25_460, 2, 0.70, ShotScale.MEDIUM, [CameraMotion.TRUCK_RIGHT],
     CameraHeight.LOW, [SubjectClass.VEHICLE], NarrativeRole.ACTION_BEAT, Composition.THIRDS_RIGHT,
     0.79, 175.0, "lateral pass, right-moving"),
    (25_460, 26_400, 2, 0.66, ShotScale.EXTREME_CLOSE, [CameraMotion.STATIC],
     CameraHeight.LOW, [SubjectClass.MECHANICAL_DETAIL], NarrativeRole.DETAIL, Composition.CENTERED,
     0.44, None, "punctuating extreme close"),
    (26_400, 28_280, 2, 0.74, ShotScale.WIDE, [CameraMotion.AERIAL],
     CameraHeight.AERIAL, [SubjectClass.LANDSCAPE], NarrativeRole.REVEAL, Composition.LOW_HORIZON,
     0.63, None, "aerial breadth at peak energy"),
    (28_280, 29_220, 2, 0.68, ShotScale.MEDIUM_CLOSE, [CameraMotion.HANDHELD],
     CameraHeight.EYE, [SubjectClass.PERSON_BODY], NarrativeRole.REACTION, Composition.CENTERED,
     0.57, None, "rider reaction"),
    (29_220, 31_700, 2, 0.71, ShotScale.MEDIUM, [CameraMotion.TRACKING],
     CameraHeight.LOW, [SubjectClass.VEHICLE], NarrativeRole.ACTION_BEAT, Composition.THIRDS_LEFT,
     0.80, None, "sustained tracking through the second half of the drop"),
    (31_700, 33_580, 2, 0.83, ShotScale.CLOSE, [CameraMotion.ZOOM_IN],
     CameraHeight.LOW, [SubjectClass.MECHANICAL_DETAIL], NarrativeRole.PAYOFF, Composition.CENTERED,
     0.69, None, "second impact -- punch in on detail"),
    (33_580, 36_400, 2, 0.64, ShotScale.WIDE, [CameraMotion.STATIC, CameraMotion.PAN_LEFT],
     CameraHeight.EYE, [SubjectClass.LANDSCAPE], NarrativeRole.TRANSITION_SHOT,
     Composition.NEGATIVE_SPACE_RIGHT, 0.41, -160.0, "wide with room to breathe"),
    (36_400, 41_000, 2, 0.60, ShotScale.MEDIUM, [CameraMotion.TRACKING, CameraMotion.AERIAL],
     CameraHeight.AERIAL, [SubjectClass.VEHICLE, SubjectClass.LANDSCAPE], NarrativeRole.ACTION_BEAT,
     Composition.THIRDS_RIGHT, 0.66, None, "long hold as energy plateaus"),
    (41_000, 45_000, 3, 0.90, ShotScale.EXTREME_WIDE, [CameraMotion.STATIC, CameraMotion.AERIAL],
     CameraHeight.AERIAL, [SubjectClass.LANDSCAPE, SubjectClass.SKY], NarrativeRole.OUTRO,
     Composition.LOW_HORIZON, 0.15, None, "final wide hold into the sunset, minimal motion"),
]

slots: list[Slot] = []
for i, (t_in, t_out, sec, imp, scale, motion, height, subj, role, comp, energy, direction, hint) in enumerate(SLOT_SPECS):
    slots.append(
        Slot(
            index=i, t_in_ms=t_in, t_out_ms=t_out, section=sec, importance=imp,
            droppable=(imp < 0.85),
            requirements=SlotRequirements(
                shot_scale=scale, shot_scale_tolerance=1,
                camera_motion=motion, camera_height=height,
                subject_class=subj, narrative_role=role, composition=comp,
                motion_energy=energy, motion_energy_tolerance=0.25,
                motion_direction_deg=direction,
                min_quality=0.6 if imp > 0.8 else 0.5,
                semantic_hint=hint,
            ),
        )
    )

# ---------------------------------------------------------------------------
# cuts -- offsets sampled from the reference's own distribution (mean -38ms)
# ---------------------------------------------------------------------------

OFFSETS = [-31, -44, -22, -39, -51, -18, -38, -47, -29, -35, -42, -26,
           -38, -55, -33, -41, -19, -36, -48, -24, -37, -45, -30]

cuts: list[Cut] = []
for i, s in enumerate(slots[1:], start=0):
    t = s.t_in_ms
    b_idx, raw_off = audio.nearest_beat(t)
    off = OFFSETS[i % len(OFFSETS)]
    is_impact = any(abs(t - imp.t_ms) < 120 for imp in audio.impacts)
    # ~15% of cuts in good edits are deliberately off-grid
    if i in (4, 9, 17):
        mode, b, sub = CutMode.FREE, None, None
    elif is_impact:
        mode, b, sub = CutMode.IMPACT, b_idx, "1"
    elif abs(raw_off) > IBI * 0.30:
        mode, b, sub = CutMode.SUBDIVIDED, b_idx, "1/2"
    else:
        mode, b, sub = CutMode.ON_BEAT, b_idx, "1"
    cuts.append(
        Cut(index=i, t_ms=t, mode=mode, beat_index=b,
            offset_ms=float(off) if b is not None else 0.0,
            subdivision=sub,
            hide_in_motion=(slots[i].requirements.motion_energy > 0.6),
            from_slot=i, to_slot=i + 1)
    )

# ---------------------------------------------------------------------------
# transitions -- 71% hard cut, 14% whip pan, 9% flash, 6% zoom
# ---------------------------------------------------------------------------

transitions = [
    Transition(at_cut=6, type=TransitionType.WHIP_PAN, secondary=[TransitionType.MOTION_BLUR],
               duration_ms=180, intensity=0.75, direction_deg=-175.0, align="centered",
               params={"blur_samples": 24, "stretch": 1.4}, confidence=0.88,
               fallback=TransitionType.FLASH),
    Transition(at_cut=11, type=TransitionType.WHIP_PAN, secondary=[TransitionType.MOTION_BLUR],
               duration_ms=160, intensity=0.68, direction_deg=15.0, confidence=0.84,
               fallback=TransitionType.FLASH),
    Transition(at_cut=13, type=TransitionType.FLASH, duration_ms=90, intensity=0.9,
               align="centered", confidence=0.93),
    Transition(at_cut=15, type=TransitionType.ZOOM_IN, duration_ms=220, intensity=0.6,
               confidence=0.71, fallback=TransitionType.HARD_CUT),
    Transition(at_cut=20, type=TransitionType.FLASH, duration_ms=80, intensity=0.85,
               confidence=0.90),
    Transition(at_cut=22, type=TransitionType.CROSS_DISSOLVE, duration_ms=480,
               intensity=0.5, confidence=0.95),
]

# ---------------------------------------------------------------------------
# motion, speed, effects
# ---------------------------------------------------------------------------

motion = [
    MotionTrack(slot=0, kind="scale", relative_to_subject=False,
                keyframes=[Keyframe(t_ms=0, value=1.0),
                           Keyframe(t_ms=2_800, value=1.08, easing="ease_in_out")]),
    MotionTrack(slot=13, kind="scale", relative_to_subject=True,
                keyframes=[Keyframe(t_ms=20_800, value=1.0),
                           Keyframe(t_ms=22_362, value=1.22, easing="expo_in")]),
    MotionTrack(slot=24, kind="scale",
                keyframes=[Keyframe(t_ms=41_000, value=1.12),
                           Keyframe(t_ms=45_000, value=1.0, easing="ease_out")]),
]

speed = [
    SpeedTrack(slot=13, mode=SpeedMode.RAMP, interpolation="optical_flow", confidence=0.64,
               keyframes=[Keyframe(t_ms=20_800, value=1.0),
                          Keyframe(t_ms=21_900, value=0.35, easing="cubic_in"),
                          Keyframe(t_ms=22_362, value=1.0, easing="expo_out")]),
    SpeedTrack(slot=17, mode=SpeedMode.CONSTANT, factor=0.5, interpolation="blend",
               confidence=0.58),
    SpeedTrack(slot=21, mode=SpeedMode.RAMP, interpolation="blend", confidence=0.61,
               keyframes=[Keyframe(t_ms=31_700, value=0.4),
                          Keyframe(t_ms=32_400, value=1.0, easing="cubic_out")]),
]

effects = [
    EffectInstance(type=EffectType.FILM_GRAIN, scope="global", intensity=0.22,
                   params={"grain_size_px": 1.4, "chroma_ratio": 0.35}, confidence=0.77),
    EffectInstance(type=EffectType.VIGNETTE, scope="global", intensity=0.28,
                   params={"falloff": 2.1, "radius": 0.82}, confidence=0.86),
    EffectInstance(type=EffectType.HALATION, scope="section", section=0, intensity=0.35,
                   blend_mode="screen", params={"threshold": 0.78, "radius_px": 22},
                   confidence=0.69),
    EffectInstance(type=EffectType.LIGHT_LEAK, scope="slot", slot=14, intensity=0.55,
                   blend_mode="screen", confidence=0.72,
                   keyframes=[Keyframe(t_ms=22_362, value=0.0),
                              Keyframe(t_ms=22_600, value=0.55, easing="expo_out"),
                              Keyframe(t_ms=23_200, value=0.0, easing="ease_out")]),
    EffectInstance(type=EffectType.LIGHT_LEAK, scope="slot", slot=21, intensity=0.42,
                   blend_mode="screen", confidence=0.66),
    EffectInstance(type=EffectType.CHROMATIC_ABERRATION, scope="slot", slot=13,
                   intensity=0.30, params={"max_shift_px": 2.4}, confidence=0.74),
]

# ---------------------------------------------------------------------------
# grade -- parametric, with measured statistics as the real target
# ---------------------------------------------------------------------------

grade = Grade(
    confidence=0.58,   # low: reference bitrate was 1.8 Mbps
    **{"global": GradeParams(
        exposure=0.12, contrast=0.18, pivot=0.435,
        saturation=1.14, vibrance=0.22,
        temperature=0.16, tint=-0.04,
        lift=(-0.012, -0.004, 0.028),
        gamma=(1.0, 0.99, 0.97),
        gain=(1.04, 1.01, 0.97),
        shadows=0.14, highlights=-0.18, whites=0.06, blacks=-0.10,
        split_tone=SplitTone(shadow_hue_deg=206.0, shadow_sat=0.24,
                             highlight_hue_deg=38.0, highlight_sat=0.31, balance=0.12),
        hsl=[
            HslBand(band="orange", hue_shift_deg=-4.0, sat=0.18, lum=0.06),
            HslBand(band="blue", hue_shift_deg=6.0, sat=0.12, lum=-0.08),
            HslBand(band="green", hue_shift_deg=8.0, sat=-0.22, lum=-0.04),
        ],
    )},
    lut_ref="lut_licensed_teal_orange_v3",
    match_target=GradeMatchTarget(
        luma_percentiles={"p1": 0.041, "p5": 0.082, "p50": 0.394, "p95": 0.871, "p99": 0.946},
        mean_lab=(46.2, 3.8, -6.1),
        sat_mean=0.412,
        shadow_hue_deg=206.0,
        highlight_hue_deg=38.0,
        split_tone_strength=0.44,
    ),
)

# ---------------------------------------------------------------------------
# captions & reframe
# ---------------------------------------------------------------------------

captions = CaptionTrack(
    enabled=True, mode=CaptionMode.WORD_BY_WORD, source="asr",
    max_words_per_chunk=1, lead_ms=-40, confidence=0.88,
    position=Position(x_pct=50.0, y_pct=74.0, anchor="center"),
    style=TextStyle(
        font_family=FontFamily.DISPLAY_HEAVY, weight=900, uppercase=True,
        size_pct=7.2, letter_spacing=-0.01, line_height=1.05,
        color="#FFFFFF", stroke_color="#000000", stroke_width_px=6,
    ),
    entry=TextAnimation(kind=TextAnimationKind.POP, duration_ms=120,
                        easing="back_out", overshoot=0.22),
    exit=TextAnimation(kind=TextAnimationKind.FADE, duration_ms=80, easing="ease_out"),
)

reframe = [
    ReframeTrack(slot=i, mode="track_subject" if s.requirements.motion_energy > 0.55 else "fill_subject",
                 subject_query=None, smoothing=0.72, max_pan_speed_pct_per_s=35.0, padding_pct=8.0)
    for i, s in enumerate(slots)
]

# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------

blueprint = Blueprint(
    id="bp_7fK2mQx91aBc",
    name="Golden-hour rolling shots, hard-cut heavy",
    created_at=datetime(2026, 8, 7, 9, 14, 22, tzinfo=timezone.utc),
    provenance=Provenance(
        analyzer_version="1.4.2",
        planner_model="gemini-2.5-pro",
        planner_tier="frontier",
        planner_seed=42,
        renderer_min_version="2.0.0",
        source_fingerprint="a91f3c7e2b8d4a16c05e9f731d2b8a4c",
        source_duration_ms=45_000,
        confidence=ConfidenceBreakdown(
            overall=0.83, beat_grid=0.96, structure=0.91,
            transitions=0.79, grade=0.58, speed=0.64, captions=0.88,
        ),
        notes=[
            "Reference bitrate low (1.8 Mbps); grade estimate is approximate.",
            "Speed ramps at slots 13, 17, 21 inferred from motion-blur ratio; medium confidence.",
            "3 cuts classified as deliberately off-grid (free mode).",
        ],
    ),
    canvas=Canvas(width=1080, height=1920, fps=30, duration_ms=DURATION, aspect="9:16",
                  color_space="bt709",
                  safe_area_inset_pct={"top": 6.0, "bottom": 14.0, "left": 4.0, "right": 18.0}),
    audio=audio,
    style=StyleProfile(
        summary=(
            "Fast, hard-cut-driven edit locked tightly to a 128 BPM four-on-the-floor grid. "
            "Cuts land a consistent ~38ms ahead of each beat, which is what makes the rhythm "
            "read as tight rather than mechanical. Pacing accelerates steadily from roughly "
            "0.9 cuts/sec in the intro to 2.6 through the drop, then releases into a single "
            "four-second hold for the outro. Shot vocabulary alternates deliberately between "
            "aerial wides and low-angle mechanical detail, rarely repeating a scale twice in "
            "a row. Transitions are overwhelmingly hard cuts, with two whip pans placed on "
            "genuinely lateral motion and flashes reserved for the two biggest impacts. "
            "Grade is a warm-highlight / teal-shadow split with lifted, slightly blue blacks "
            "and pulled highlights. Captions are single-word, heavy display type, popping in "
            "just ahead of the spoken word."
        ),
        tags=["automotive", "golden_hour", "hard_cut", "high_energy", "aerial", "teal_orange"],
        pacing=PacingProfile(
            cuts_per_second=1.87, beat_lock_ratio=0.84,
            mean_shot_ms=535, median_shot_ms=469, shot_ms_stddev=218.4,
            offset_mean_ms=-36.4, offset_stddev_ms=10.8,
            acceleration=0.42,
        ),
        shot_scale_mix={"extreme_wide": 0.08, "wide": 0.24, "medium": 0.24,
                        "medium_close": 0.16, "close": 0.20, "extreme_close": 0.08},
        transition_mix={"hard_cut": 0.74, "whip_pan": 0.09, "flash": 0.09,
                        "zoom_in": 0.04, "cross_dissolve": 0.04},
        effect_budget={"light_leak": 2, "film_grain": 1, "chromatic_aberration": 1,
                       "halation": 1, "vignette": 1},
        palette=[
            PaletteColor(hex="#1f3a4d", weight=0.31, role="shadow"),
            PaletteColor(hex="#e8a765", weight=0.24, role="highlight"),
            PaletteColor(hex="#2c4f63", weight=0.18, role="dominant"),
            PaletteColor(hex="#f2d9b8", weight=0.14, role="accent"),
            PaletteColor(hex="#0d1a22", weight=0.13, role="shadow"),
        ],
    ),
    slots=slots,
    cuts=cuts,
    transitions=transitions,
    motion=motion,
    speed=speed,
    effects=effects,
    grade=grade,
    captions=captions,
    reframe=reframe,
    constraints=Constraints(
        min_shot_ms=180, max_shot_ms=8_000,
        max_consecutive_same_scale=2, max_consecutive_same_source=1,
        max_segment_reuse=3, min_reuse_gap_ms=4_000,
        forbid_jump_cut_same_source=True, max_effect_layers=4,
    ),
)


def main() -> int:
    out = HERE / "moto-sunset-128bpm.json"
    # exclude_none: the schema models "not yet resolved" as an ABSENT key rather
    # than an explicit null (e.g. slots[].assignment on a free blueprint).
    payload = json.loads(blueprint.model_dump_json(by_alias=True, exclude_none=True))
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    try:
        import jsonschema
    except ImportError:
        print(f"wrote {out.name} ({out.stat().st_size / 1024:.1f} KB) -- jsonschema not installed, skipped validation")
        return 0

    schema = json.loads((REPO / "schemas" / "blueprint.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(payload)

    budget_problems = blueprint.check_effect_budget()
    print(f"wrote {out.name} ({out.stat().st_size / 1024:.1f} KB)")
    print(f"  valid against blueprint.schema.json : yes")
    print(f"  slots {len(blueprint.slots)} · cuts {len(blueprint.cuts)} · transitions {len(blueprint.transitions)}")
    print(f"  effect budget violations            : {budget_problems or 'none'}")
    print(f"  low-confidence subsystems           : {blueprint.low_confidence_subsystems()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
