"""Fusion — turn measurements into an Editing Blueprint.

This is where per-shot measurements become *editorial* structure: cuts mapped to
the beat grid with signed offsets, slot requirements abstracted away from the
reference's specific shots, pacing expressed as rules rather than timestamps.

No LLM in this path. The planner (docs/04 stage 8) improves the intent
explanations and importance weights; it is not required to produce a valid,
renderable blueprint. Keeping fusion LLM-free means the pipeline is testable and
deterministic today.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import datetime, timezone

import numpy as np
from reelsedits_common import (
    AudioSection,
    AudioTrack,
    Blueprint,
    Canvas,
    ConfidenceBreakdown,
    Constraints,
    Cut,
    EnergyCurve,
    Grade,
    GradeMatchTarget,
    Impact,
    MusicBinding,
    PacingProfile,
    Provenance,
    ReframeTrack,
    Slot,
    SlotRequirements,
    StyleProfile,
    Transition,
)
from reelsedits_common.blueprint import PlatformAttachCard
from reelsedits_common.enums import (
    CameraHeight,
    CameraMotion,
    Composition,
    CutMode,
    MusicStrategy,
    NarrativeRole,
    SectionKind,
    ShotScale,
    SubjectClass,
    TransitionType,
)

from .audio import AudioAnalysis
from .visual import Shot, VisualAnalysis

log = logging.getLogger("reelsedits.analyzer.fusion")

ANALYZER_VERSION = "0.2.0-v0"
RENDERER_MIN_VERSION = "0.2.0"

ON_BEAT_TOLERANCE_MS = 45
IMPACT_TOLERANCE_MS = 140

#: Expert editors cut 20-60ms AHEAD of the transient, because visual perception
#: lags auditory perception and a cut exactly on the beat reads as late. When we
#: cannot measure the reference's own offset distribution (too few on-grid cuts),
#: we fall back to this rather than to zero. Snapping to zero is what makes
#: beat-synced edits feel mechanical. docs/08 §2.3.
DEFAULT_OFFSET_MEAN_MS = -36.0
DEFAULT_OFFSET_STDDEV_MS = 12.0


# ---------------------------------------------------------------------------
# cut → beat mapping
# ---------------------------------------------------------------------------


def map_cut_to_grid(
    t_ms: int, grid: list[int], impacts: list[dict], ibi_ms: float
) -> tuple[CutMode, int | None, float, str | None]:
    """Anchor a cut to the beat grid.

    Returns ``(mode, beat_index, signed_offset_ms, subdivision)``.

    The offset sign is preserved and is the point: negative means the cut
    precedes the beat, which is what good edits do.
    """
    for imp in impacts:
        if abs(imp["t_ms"] - t_ms) <= IMPACT_TOLERANCE_MS:
            idx = int(np.argmin([abs(b - imp["t_ms"]) for b in grid]))
            return CutMode.IMPACT, idx, float(t_ms - grid[idx]), "1"

    if not grid:
        return CutMode.FREE, None, 0.0, None

    idx = int(np.argmin([abs(b - t_ms) for b in grid]))
    offset = float(t_ms - grid[idx])

    if abs(offset) <= ON_BEAT_TOLERANCE_MS:
        return CutMode.ON_BEAT, idx, offset, "1"

    # abs(offset) already covers both directions, so a cut a quarter-beat early
    # and one a quarter-beat late both match the same subdivision.
    for sub, label in ((0.5, "1/2"), (0.25, "1/4"), (1 / 3, "1/3"), (0.125, "1/8")):
        if abs(abs(offset) - sub * ibi_ms) <= ON_BEAT_TOLERANCE_MS:
            return CutMode.SUBDIVIDED, idx, offset, label

    # Genuinely off-grid. Roughly 15% of cuts in good edits are, and a blueprint
    # with beat_lock_ratio 1.0 describes a robot.
    return CutMode.FREE, None, 0.0, None


# ---------------------------------------------------------------------------
# shot → slot requirements
# ---------------------------------------------------------------------------

_MOTION_TO_ENUM = {
    "static": CameraMotion.STATIC, "pan_left": CameraMotion.PAN_LEFT,
    "pan_right": CameraMotion.PAN_RIGHT, "tilt_up": CameraMotion.TILT_UP,
    "tilt_down": CameraMotion.TILT_DOWN, "zoom_in": CameraMotion.ZOOM_IN,
    "zoom_out": CameraMotion.ZOOM_OUT, "handheld": CameraMotion.HANDHELD,
    "tracking": CameraMotion.TRACKING,
}

_COMPOSITION_TO_ENUM = {
    "centered": Composition.CENTERED, "thirds_left": Composition.THIRDS_LEFT,
    "thirds_right": Composition.THIRDS_RIGHT, "symmetric": Composition.SYMMETRIC,
}

_TRANSITION_TO_ENUM = {
    "hard_cut": TransitionType.HARD_CUT,
    "cross_dissolve": TransitionType.CROSS_DISSOLVE,
    "flash": TransitionType.FLASH,
    "whip_pan": TransitionType.WHIP_PAN,
}


def _compatible_motions(motion: CameraMotion) -> list[CameraMotion]:
    """Widen a measured motion into an acceptable set.

    A slot derived from a `pan_left` shot should accept `truck_left` and
    `tracking` too — the frame moves the same way and the cut reads the same.
    Requiring exact equality would reject most usable user footage.
    """
    if motion.is_lateral:
        same_side = (
            [CameraMotion.PAN_LEFT, CameraMotion.TRUCK_LEFT]
            if "left" in motion.value
            else [CameraMotion.PAN_RIGHT, CameraMotion.TRUCK_RIGHT]
        )
        return [*same_side, CameraMotion.TRACKING]
    if motion.is_push:
        return [CameraMotion.ZOOM_IN, CameraMotion.DOLLY_IN] if "in" in motion.value \
            else [CameraMotion.ZOOM_OUT, CameraMotion.DOLLY_OUT]
    if motion is CameraMotion.STATIC:
        return [CameraMotion.STATIC, CameraMotion.HANDHELD]
    if motion is CameraMotion.HANDHELD:
        return [CameraMotion.HANDHELD, CameraMotion.TRACKING, CameraMotion.STATIC]
    return [motion, CameraMotion.ANY]


def _narrative_role(index: int, total: int, scale: ShotScale, energy: float) -> NarrativeRole:
    if index == 0:
        return NarrativeRole.ESTABLISH
    if index == total - 1:
        return NarrativeRole.OUTRO
    if scale in (ShotScale.EXTREME_CLOSE, ShotScale.CLOSE):
        return NarrativeRole.DETAIL
    if energy > 0.6:
        return NarrativeRole.ACTION_BEAT
    if scale in (ShotScale.WIDE, ShotScale.EXTREME_WIDE):
        return NarrativeRole.ESTABLISH
    return NarrativeRole.ANY


def _importance(index: int, total: int, shot: Shot, impacts: list[dict]) -> float:
    """How load-bearing this slot is; drives what survives degradation."""
    score = 0.45
    if index == 0:
        score += 0.35                      # the hook
    if index == total - 1:
        score += 0.30                      # the resolve
    for imp in impacts:
        if shot.t_in_ms <= imp["t_ms"] <= shot.t_out_ms:
            score += 0.30 * imp["strength"]
            break
    score += 0.20 * shot.motion_energy
    return float(np.clip(score, 0.1, 1.0))


def shot_to_requirements(shot: Shot, index: int, total: int) -> SlotRequirements:
    """Abstract a measured shot into a requirement other footage can satisfy.

    This is the step that makes cross-domain transfer possible: we never store
    "the wheel close-up from the reference", only "close, low-ish, moderate
    motion, subject on the left third". docs/06 §5.1.
    """
    scale = ShotScale(shot.shot_scale)
    motion = _MOTION_TO_ENUM.get(shot.camera_motion, CameraMotion.ANY)

    return SlotRequirements(
        shot_scale=scale,
        shot_scale_tolerance=1,
        camera_motion=_compatible_motions(motion),
        camera_height=CameraHeight.ANY,
        # Subject class stays ANY in v0: without a VLM we have no honest way to
        # name the subject, and a wrong hard constraint is worse than an absent
        # one. The semantic stage fills this in.
        subject_class=[SubjectClass.ANY],
        narrative_role=_narrative_role(index, total, scale, shot.motion_energy),
        composition=_COMPOSITION_TO_ENUM.get(shot.composition, Composition.ANY),
        motion_energy=shot.motion_energy,
        motion_energy_tolerance=0.30,
        motion_direction_deg=shot.motion_direction_deg,
        min_quality=0.45,
        semantic_hint=(
            f"{scale.value.replace('_', ' ')} shot, {shot.camera_motion.replace('_', ' ')} "
            f"camera, motion energy {shot.motion_energy:.2f}, "
            f"{shot.composition.replace('_', ' ')} composition"
        ),
    )


# ---------------------------------------------------------------------------
# blueprint assembly
# ---------------------------------------------------------------------------


def build_blueprint(
    audio: AudioAnalysis,
    visual: VisualAnalysis,
    *,
    name: str | None = None,
    music_strategy: MusicStrategy = MusicStrategy.PLATFORM_ATTACH,
    sound_name: str | None = None,
    platform: str = "unknown",
    target_width: int = 1080,
    target_height: int = 1920,
    fps: float = 30.0,
) -> Blueprint:
    """Assemble a schema-valid blueprint from audio and visual analysis."""
    shots = [s for s in visual.shots if s.duration_ms >= 180]
    if not shots:
        raise ValueError("no shots long enough to build slots from")

    duration_ms = max(shots[-1].t_out_ms, audio.duration_ms)
    ibi = 60_000 / audio.bpm

    # ---- audio track ------------------------------------------------------
    grid = [b for b in audio.beat_grid_ms if b <= duration_ms] or audio.beat_grid_ms[:2]
    if len(grid) < 2:
        grid = [0, int(ibi)]

    sections = [
        AudioSection(
            kind=SectionKind(s["kind"]),
            t_in_ms=s["t_in_ms"],
            t_out_ms=min(s["t_out_ms"], duration_ms),
            energy=s["energy"],
            target_cut_density=s["target_cut_density"],
        )
        for s in audio.sections
        if s["t_in_ms"] < duration_ms and s["t_out_ms"] > s["t_in_ms"]
    ] or [AudioSection(kind=SectionKind.VERSE, t_in_ms=0, t_out_ms=duration_ms)]

    binding = _music_binding(music_strategy, audio, sound_name, platform)

    audio_track = AudioTrack(
        bpm=audio.bpm,
        beat_grid_ms=grid,
        downbeats_ms=[d for d in audio.downbeats_ms if d <= duration_ms],
        sections=sections,
        energy_curve=EnergyCurve(hz=audio.energy_hz, values=audio.energy_curve or [0.5, 0.5]),
        impacts=[Impact(t_ms=i["t_ms"], strength=i["strength"], kind=i["kind"])
                 for i in audio.impacts if i["t_ms"] <= duration_ms],
        time_signature=audio.time_signature,
        music_binding=binding,
    )

    # ---- slots ------------------------------------------------------------
    slots: list[Slot] = []
    for i, shot in enumerate(shots):
        t_out = min(shot.t_out_ms, duration_ms)
        if t_out - shot.t_in_ms < 180:
            continue
        section_idx = next(
            (j for j, s in enumerate(sections) if s.t_in_ms <= shot.t_in_ms < s.t_out_ms),
            None,
        )
        imp = _importance(i, len(shots), shot, audio.impacts)
        slots.append(Slot(
            index=len(slots),
            t_in_ms=shot.t_in_ms,
            t_out_ms=t_out,
            section=section_idx,
            importance=round(imp, 3),
            droppable=imp < 0.85,
            requirements=shot_to_requirements(shot, i, len(shots)),
        ))

    if not slots:
        raise ValueError("no valid slots after filtering")

    # ---- cuts -------------------------------------------------------------
    cuts: list[Cut] = []
    offsets: list[float] = []
    for i, slot in enumerate(slots[1:], start=0):
        mode, beat_idx, offset, sub = map_cut_to_grid(
            slot.t_in_ms, grid, audio.impacts, ibi
        )
        if mode in (CutMode.ON_BEAT, CutMode.SUBDIVIDED, CutMode.IMPACT):
            offsets.append(offset)
        cuts.append(Cut(
            index=i,
            t_ms=slot.t_in_ms,
            mode=mode,
            beat_index=beat_idx,
            offset_ms=offset,
            subdivision=sub,
            hide_in_motion=slots[i].requirements.motion_energy > 0.55,
            from_slot=i,
            to_slot=i + 1,
        ))

    if not cuts:
        # Single-shot reference. Synthesise one cut so the blueprint is valid;
        # the renderer will treat it as a single sustained slot.
        cuts.append(Cut(index=0, t_ms=slots[0].t_out_ms, mode=CutMode.FREE))

    # ---- transitions ------------------------------------------------------
    transitions: list[Transition] = []
    for cut in cuts:
        if cut.to_slot is None or cut.to_slot >= len(shots):
            continue
        shot = shots[cut.to_slot]
        ttype = _TRANSITION_TO_ENUM.get(shot.transition_type, TransitionType.HARD_CUT)
        if ttype is TransitionType.HARD_CUT or shot.transition_duration_ms <= 0:
            continue
        transitions.append(Transition(
            at_cut=cut.index,
            type=ttype,
            duration_ms=int(np.clip(shot.transition_duration_ms, 40, 1200)),
            intensity=0.5,
            direction_deg=shot.transition_direction_deg if ttype.needs_direction else None,
            confidence=shot.transition_confidence,
            fallback=TransitionType.HARD_CUT,
        ))

    # ---- pacing -----------------------------------------------------------
    durations = [s.duration_ms for s in slots]
    on_grid = sum(1 for c in cuts if c.mode is not CutMode.FREE)

    if len(offsets) >= 3:
        offset_mean, offset_std = float(np.mean(offsets)), float(np.std(offsets))
    else:
        offset_mean, offset_std = DEFAULT_OFFSET_MEAN_MS, DEFAULT_OFFSET_STDDEV_MS

    first_half = [d for s, d in zip(slots, durations) if s.t_in_ms < duration_ms / 2]
    second_half = [d for s, d in zip(slots, durations) if s.t_in_ms >= duration_ms / 2]
    accel = 0.0
    if first_half and second_half:
        accel = float(np.clip(
            (np.mean(first_half) - np.mean(second_half)) / max(np.mean(first_half), 1), -1, 1
        ))

    scale_counts = Counter(s.requirements.shot_scale.value for s in slots)
    trans_counts = Counter([t.type.value for t in transitions])
    trans_counts["hard_cut"] = len(cuts) - len(transitions)
    n_cuts = max(len(cuts), 1)

    style = StyleProfile(
        summary=_summarise(slots, cuts, transitions, audio, visual, offset_mean),
        tags=_tags(audio, visual, slots),
        pacing=PacingProfile(
            cuts_per_second=round(len(cuts) / max(duration_ms / 1000, 0.1), 3),
            beat_lock_ratio=round(on_grid / n_cuts, 3),
            mean_shot_ms=int(np.mean(durations)),
            median_shot_ms=int(np.median(durations)),
            shot_ms_stddev=round(float(np.std(durations)), 1),
            offset_mean_ms=round(offset_mean, 1),
            offset_stddev_ms=round(offset_std, 1),
            acceleration=round(accel, 3),
        ),
        shot_scale_mix={k: round(v / len(slots), 3) for k, v in scale_counts.items()},
        transition_mix={k: round(v / n_cuts, 3) for k, v in trans_counts.items() if v},
    )

    # ---- grade ------------------------------------------------------------
    g = visual.grade
    grade = Grade(
        confidence=g.get("confidence", 0.5),
        match_target=GradeMatchTarget(
            luma_percentiles=g.get("luma_percentiles", {}),
            sat_mean=g.get("sat_mean"),
            shadow_hue_deg=g.get("shadow_hue_deg"),
            highlight_hue_deg=g.get("highlight_hue_deg"),
            split_tone_strength=g.get("split_tone_strength"),
        ),
    )

    notes = [*audio.notes, *visual.notes]
    if audio.confidence < 0.6:
        notes.append("Beat grid confidence is low; prefer content-driven cuts.")
    notes.append("v0 analyser: histogram SBD and Farneback flow; no VLM or semantic labels.")

    return Blueprint(
        id=f"bp_{uuid.uuid4().hex[:16]}",
        name=name,
        created_at=datetime.now(timezone.utc),
        provenance=Provenance(
            analyzer_version=ANALYZER_VERSION,
            renderer_min_version=RENDERER_MIN_VERSION,
            planner_model=None,
            planner_tier="none",
            source_duration_ms=duration_ms,
            confidence=ConfidenceBreakdown(
                overall=round(float(np.mean([audio.confidence, visual.sbd_confidence, 0.55])), 3),
                beat_grid=audio.confidence,
                structure=visual.sbd_confidence,
                transitions=round(visual.sbd_confidence * 0.85, 3),
                grade=g.get("confidence", 0.5),
                speed=0.30,          # no speed inference in v0; stated honestly
                captions=0.0,
            ),
            notes=notes,
        ),
        canvas=Canvas(
            width=target_width, height=target_height, fps=fps,
            duration_ms=duration_ms,
            aspect="9:16" if target_height > target_width else "16:9",
            safe_area_inset_pct={"top": 6.0, "bottom": 14.0, "left": 4.0, "right": 18.0},
        ),
        audio=audio_track,
        style=style,
        slots=slots,
        cuts=cuts,
        transitions=transitions,
        grade=grade,
        reframe=[
            ReframeTrack(
                slot=s.index,
                mode="fill_center",       # v0: no subject masks yet
                smoothing=0.72,
            )
            for s in slots
        ],
        constraints=Constraints(min_shot_ms=180, max_shot_ms=8000),
    )


def _music_binding(
    strategy: MusicStrategy, audio: AudioAnalysis, sound_name: str | None, platform: str
) -> MusicBinding:
    """Build the binding.

    Default is PLATFORM_ATTACH: we render a silent master and hand the creator
    the trim offset so they can attach the original sound in-app, under the
    platform's own licence. We never touch the recording. docs/18 §3.
    """
    if strategy is MusicStrategy.PLATFORM_ATTACH:
        first_db = audio.downbeats_ms[0] if audio.downbeats_ms else 0
        return MusicBinding(
            strategy=strategy,
            platform_attach=PlatformAttachCard(
                sound_name=sound_name,
                platform=platform if platform in
                         ("tiktok", "instagram", "youtube") else "unknown",
                # We cut starting at the reference's own timeline origin, so the
                # sound must start there too for the beats to line up.
                trim_start_ms=0,
                first_downbeat_ms=first_db,
                bpm=audio.bpm,
                instructions=(
                    f"Export this file (it has no music). In the app, add the original "
                    f"sound and set its start to 0:00. The first downbeat lands at "
                    f"{first_db / 1000:.2f}s and every cut is aligned to the "
                    f"{audio.bpm:.0f} BPM grid, so it will sync on the first try."
                ),
            ),
        )
    if strategy is MusicStrategy.SILENT:
        return MusicBinding(strategy=strategy)
    raise ValueError(
        f"strategy {strategy.value} needs a resolved track and licence; "
        "the v0 analyser only emits platform_attach or silent"
    )


def _summarise(slots, cuts, transitions, audio, visual, offset_mean) -> str:
    cps = len(cuts) / max(slots[-1].t_out_ms / 1000, 0.1)
    pace = "Fast" if cps > 1.5 else "Moderate" if cps > 0.7 else "Slow"
    lock = sum(1 for c in cuts if c.mode is not CutMode.FREE) / max(len(cuts), 1)
    scales = Counter(s.requirements.shot_scale.value for s in slots)
    top = ", ".join(f"{k.replace('_', ' ')}" for k, _ in scales.most_common(2))
    tmix = Counter(t.type.value for t in transitions)
    tdesc = (
        f"{len(cuts) - len(transitions)} hard cuts"
        + (f" and {sum(tmix.values())} {'/'.join(tmix)} transitions" if tmix else "")
    )
    return (
        f"{pace}, {cps:.2f} cuts per second at {audio.bpm:.0f} BPM, with "
        f"{lock:.0%} of cuts locked to the beat grid landing on average "
        f"{abs(offset_mean):.0f}ms {'ahead of' if offset_mean < 0 else 'after'} the beat. "
        f"Shot vocabulary is mostly {top}. Transitions are {tdesc}. "
        f"Grade sits at {visual.grade.get('sat_mean', 0):.2f} mean saturation with "
        f"shadows near {visual.grade.get('shadow_hue_deg', 0):.0f}° and highlights near "
        f"{visual.grade.get('highlight_hue_deg', 0):.0f}°."
    )


def _tags(audio: AudioAnalysis, visual: VisualAnalysis, slots) -> list[str]:
    tags: list[str] = []
    cps = len(slots) / max(audio.duration_ms / 1000, 0.1)
    tags.append("fast_cut" if cps > 1.5 else "slow_cut" if cps < 0.6 else "medium_cut")
    if audio.bpm > 140:
        tags.append("high_tempo")
    elif audio.bpm < 90:
        tags.append("low_tempo")
    if any(i["kind"] == "drop" for i in audio.impacts):
        tags.append("has_drop")
    if visual.grade.get("split_tone_strength", 0) > 0.5:
        tags.append("split_tone")
    energies = [s.requirements.motion_energy for s in slots]
    if energies and float(np.mean(energies)) > 0.55:
        tags.append("high_motion")
    return tags
