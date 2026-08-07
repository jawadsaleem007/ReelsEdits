"""Editing Blueprint (EBP) v1 -- Pydantic v2 reference implementation.

Mirrors ``schemas/blueprint.schema.json``. The JSON Schema is normative for
external consumers; this module is the in-process representation and carries
the cross-field invariants that JSON Schema cannot express (monotonic
timelines, resolvable indices, licence presence).

Design notes worth reading before changing anything here:

* ``model_config`` sets ``extra="forbid"`` everywhere. This is load-bearing,
  not fastidiousness -- it is what structurally prevents anyone adding a field
  capable of carrying reference media into the blueprint. See docs/18.
* All times are integer milliseconds from the timeline origin. Floats caused
  drift bugs in an earlier iteration; frames are resolution-dependent.
* Confidence is per-subsystem, not global, because it genuinely varies: beat
  grid is measured (~0.95), colour grade is inferred (~0.6).

See docs/06-blueprint-spec.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .enums import (
    CameraHeight,
    CameraMotion,
    CaptionMode,
    Composition,
    CompromiseKind,
    CutMode,
    Easing,
    EffectType,
    FontFamily,
    MusicStrategy,
    NarrativeRole,
    SectionKind,
    ShotScale,
    SpeedMode,
    SubjectClass,
    TextAnimationKind,
    TransitionType,
)

EBP_VERSION = "1.0"

Ms = Annotated[int, Field(ge=0, description="Milliseconds from timeline origin")]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
Unit = Annotated[float, Field(ge=0.0, le=1.0)]
Signed = Annotated[float, Field(ge=-1.0, le=1.0)]
HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]

#: Below this confidence, consumers must degrade rather than trust the value.
CONFIDENCE_FLOOR = 0.6


class _Base(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=False,
        validate_assignment=True,
        ser_json_timedelta="float",
    )


# ==========================================================================
# Provenance & canvas
# ==========================================================================


class ConfidenceBreakdown(_Base):
    overall: Confidence
    beat_grid: Confidence | None = None
    structure: Confidence | None = None
    transitions: Confidence | None = None
    grade: Confidence | None = None
    speed: Confidence | None = None
    captions: Confidence | None = None

    def weakest(self) -> tuple[str, float]:
        """The subsystem the UI should caveat. Drives 'grade: approximate' labels."""
        scored = {
            k: v
            for k, v in self.model_dump(exclude_none=True).items()
            if k != "overall"
        }
        if not scored:
            return ("overall", self.overall)
        key = min(scored, key=lambda k: scored[k])
        return (key, scored[key])


class Provenance(_Base):
    analyzer_version: str
    renderer_min_version: str
    confidence: ConfidenceBreakdown
    planner_model: str | None = None
    planner_tier: Literal["frontier", "fallback", "none"] = "frontier"
    planner_seed: int | None = None
    source_fingerprint: str | None = Field(
        default=None,
        description="Perceptual hash of the reference. Not a copy of it, and not reversible.",
    )
    source_duration_ms: Ms = 0
    notes: list[str] = Field(default_factory=list)


class SafeAreaInset(_Base):
    top: float = Field(default=0, ge=0, le=40)
    bottom: float = Field(default=0, ge=0, le=40)
    left: float = Field(default=0, ge=0, le=40)
    right: float = Field(default=0, ge=0, le=40)


class Canvas(_Base):
    width: int = Field(ge=16, le=7680)
    height: int = Field(ge=16, le=7680)
    fps: float = Field(gt=0, le=240)
    duration_ms: Ms
    aspect: Literal["9:16", "16:9", "1:1", "4:5", "4:3", "2.39:1", "custom"] = "9:16"
    color_space: Literal["bt709", "bt2020_pq", "bt2020_hlg", "srgb"] = "bt709"
    safe_area_inset_pct: SafeAreaInset = Field(default_factory=SafeAreaInset)

    @property
    def frame_ms(self) -> float:
        return 1000.0 / self.fps

    def snap_to_frame(self, t_ms: int) -> int:
        """Quantise a time to the nearest frame boundary.

        The renderer must do this consistently or transitions land half a frame
        off and dissolve edges shimmer.
        """
        return round(round(t_ms / self.frame_ms) * self.frame_ms)


# ==========================================================================
# Audio -- the rhythmic skeleton, never audio data
# ==========================================================================


class AudioSection(_Base):
    kind: SectionKind
    t_in_ms: Ms
    t_out_ms: Ms
    energy: Unit = 0.5
    target_cut_density: float = Field(
        default=1.0,
        ge=0,
        le=12,
        description="Cuts/second to aim for here when adapting to a different-length track.",
    )
    label: str | None = None

    @model_validator(mode="after")
    def _ordered(self) -> AudioSection:
        if self.t_out_ms <= self.t_in_ms:
            raise ValueError(
                f"section {self.kind}: t_out_ms ({self.t_out_ms}) must exceed "
                f"t_in_ms ({self.t_in_ms})"
            )
        return self

    @property
    def duration_ms(self) -> int:
        return self.t_out_ms - self.t_in_ms


class EnergyCurve(_Base):
    hz: float = Field(default=20.0, gt=0, le=100)
    values: list[Unit] = Field(min_length=2)

    def at(self, t_ms: int) -> float:
        """Linearly interpolated energy at a time. Clamped at both ends."""
        idx = t_ms / 1000.0 * self.hz
        if idx <= 0:
            return self.values[0]
        if idx >= len(self.values) - 1:
            return self.values[-1]
        lo = int(idx)
        frac = idx - lo
        return self.values[lo] * (1 - frac) + self.values[lo + 1] * frac


class Impact(_Base):
    t_ms: Ms
    strength: Unit
    kind: Literal["drop", "hit", "riser_peak", "silence_break", "vocal_entry"] = "hit"


class SfxEvent(_Base):
    t_ms: Ms
    sfx_class: Literal[
        "whoosh", "impact", "riser", "sub_drop", "click", "glitch", "reverse",
        "ambience", "other",
    ] = Field(alias="class")
    duration_ms: Ms = 0
    gain_db: float = Field(default=-6.0, ge=-60, le=12)
    bound_to_cut: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PlatformAttachCard(_Base):
    """Instructions for re-attaching the original sound inside the platform.

    The edit was cut to the reference track's real beat grid, so when the
    creator selects that sound in TikTok/Instagram it re-syncs exactly -- but
    only if they trim the sound to the right start point. These fields are what
    make that a one-tap operation instead of a fiddly one.

    Note what is NOT here: no audio, no download URL, no track file. Only the
    identifiers a human would read off the screen themselves, and the arithmetic
    for lining it up.
    """

    #: Platform-native sound identifier, as displayed to the user. We surface
    #: what the reference itself credits; we do not fetch or host the audio.
    sound_name: str | None = Field(default=None, max_length=300)
    platform: Literal["tiktok", "instagram", "youtube", "unknown"] = "unknown"

    #: Offset into the platform track where our first frame sits. This is the
    #: number the creator types into the platform's sound-trim control.
    trim_start_ms: int = Field(default=0, ge=0)
    #: Where the first downbeat lands in OUR output, so the creator can verify
    #: alignment visually rather than by ear.
    first_downbeat_ms: int = Field(default=0, ge=0)
    bpm: float | None = Field(default=None, gt=20, le=300)

    instructions: str = Field(
        default=(
            "Export, upload to the app, then add the original sound and set its "
            "start point to the trim offset shown. The cuts will land on the beat."
        ),
        max_length=600,
    )


class MusicBinding(_Base):
    """How the rhythmic skeleton becomes real audio.

    There is no strategy that muxes the reference's master recording into the
    export. Adding one would be a breaking schema change requiring deliberate
    action, and that friction is intentional.

    ``platform_attach`` is how a user gets the original track: we render a
    silent master and they attach the sound in-app, under the platform's own
    licence. We never redistribute the recording.
    """

    strategy: MusicStrategy
    track_id: str | None = None
    licence_id: str | None = None
    match_score: float | None = Field(default=None, ge=0, le=1)
    platform_attach: PlatformAttachCard | None = None
    time_map: list[tuple[int, int]] = Field(
        default_factory=list,
        description="(blueprint_ms, track_ms) anchors. Warps the EDIT to the track, "
        "never the track to the edit -- stretching licensed audio degrades it.",
    )

    @model_validator(mode="after")
    def _strategy_consistent(self) -> MusicBinding:
        if self.strategy.requires_licence and not self.licence_id:
            raise ValueError(
                f"music strategy '{self.strategy.value}' requires a licence_id; "
                "the renderer will not run without one"
            )
        if self.strategy is MusicStrategy.CATALOGUE_MATCH and not self.track_id:
            raise ValueError("catalogue_match requires a track_id")
        if self.strategy is MusicStrategy.PLATFORM_ATTACH:
            if self.platform_attach is None:
                raise ValueError(
                    "platform_attach requires a PlatformAttachCard; without the trim "
                    "offset the creator cannot re-sync the sound and the edit lands "
                    "off the beat"
                )
            if self.licence_id is not None:
                raise ValueError(
                    "platform_attach must not carry a licence_id: we are not "
                    "licensing anything, the platform is"
                )
        return self

    def map_time(self, bp_ms: int) -> int:
        """Blueprint time -> bound-track time, piecewise linear between anchors."""
        if not self.time_map:
            return bp_ms
        pts = sorted(self.time_map)
        if bp_ms <= pts[0][0]:
            return pts[0][1]
        if bp_ms >= pts[-1][0]:
            return pts[-1][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= bp_ms <= x1:
                if x1 == x0:
                    return y0
                frac = (bp_ms - x0) / (x1 - x0)
                return round(y0 + frac * (y1 - y0))
        return bp_ms


class AudioTrack(_Base):
    bpm: float = Field(gt=20, le=300)
    beat_grid_ms: list[Ms] = Field(min_length=2)
    sections: list[AudioSection] = Field(min_length=1)
    energy_curve: EnergyCurve
    bpm_curve: list[tuple[int, float]] = Field(default_factory=list)
    time_signature: str = Field(default="4/4", pattern=r"^[0-9]{1,2}/[0-9]{1,2}$")
    downbeats_ms: list[Ms] = Field(default_factory=list)
    impacts: list[Impact] = Field(default_factory=list)
    sfx: list[SfxEvent] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    genre: list[str] = Field(default_factory=list)
    music_binding: MusicBinding | None = None

    @field_validator("beat_grid_ms")
    @classmethod
    def _monotonic(cls, v: list[int]) -> list[int]:
        if any(b <= a for a, b in zip(v, v[1:])):
            raise ValueError("beat_grid_ms must be strictly increasing")
        return v

    @model_validator(mode="after")
    def _sections_contiguous(self) -> AudioTrack:
        ordered = sorted(self.sections, key=lambda s: s.t_in_ms)
        for a, b in zip(ordered, ordered[1:]):
            if b.t_in_ms < a.t_out_ms:
                raise ValueError(
                    f"audio sections overlap: {a.kind} ends {a.t_out_ms}, "
                    f"{b.kind} starts {b.t_in_ms}"
                )
        return self

    @property
    def mean_ibi_ms(self) -> float:
        """Mean inter-beat interval. Used to express cut offsets in beat fractions."""
        return (self.beat_grid_ms[-1] - self.beat_grid_ms[0]) / (
            len(self.beat_grid_ms) - 1
        )

    def nearest_beat(self, t_ms: int) -> tuple[int, int]:
        """Return ``(beat_index, signed_offset_ms)`` for a time.

        Offset is negative when the time precedes the beat -- which is the
        normal case for expert cuts. See docs/06 section 6.1.
        """
        best_i, best_d = 0, abs(t_ms - self.beat_grid_ms[0])
        for i, b in enumerate(self.beat_grid_ms):
            d = abs(t_ms - b)
            if d < best_d:
                best_i, best_d = i, d
        return best_i, t_ms - self.beat_grid_ms[best_i]

    def section_at(self, t_ms: int) -> AudioSection | None:
        for s in self.sections:
            if s.t_in_ms <= t_ms < s.t_out_ms:
                return s
        return None


# ==========================================================================
# Style profile
# ==========================================================================


class PacingProfile(_Base):
    cuts_per_second: float = Field(ge=0, le=12)
    beat_lock_ratio: Unit
    mean_shot_ms: Ms = 0
    median_shot_ms: Ms = 0
    shot_ms_stddev: float = Field(default=0.0, ge=0)
    offset_mean_ms: float = Field(
        default=0.0,
        ge=-400,
        le=400,
        description="Mean signed cut offset from beat. Typically NEGATIVE (-20 to -60ms) "
        "in expert edits, because visual perception lags auditory perception.",
    )
    offset_stddev_ms: float = Field(default=0.0, ge=0)
    acceleration: Signed = 0.0


class PaletteColor(_Base):
    hex: HexColor
    weight: Unit
    role: Literal["dominant", "accent", "shadow", "highlight", "skin"] = "dominant"


class StyleProfile(_Base):
    summary: str = Field(max_length=2000)
    pacing: PacingProfile
    tags: list[str] = Field(default_factory=list)
    shot_scale_mix: dict[str, float] = Field(default_factory=dict)
    transition_mix: dict[str, float] = Field(default_factory=dict)
    effect_budget: dict[str, int] = Field(
        default_factory=dict,
        description="Max instances per effect type per section. Reproducing restraint "
        "matters as much as reproducing vocabulary.",
    )
    palette: list[PaletteColor] = Field(default_factory=list, max_length=8)
    embedding: list[float] | None = None


# ==========================================================================
# Slots
# ==========================================================================


class SlotRequirements(_Base):
    """What a segment must satisfy, expressed independently of the reference shot.

    This abstraction is what lets a car reference render onto motorcycle
    footage. Matching against the *reference shot* would fail; matching against
    ``{close, low, mechanical_detail, motion 0.62}`` succeeds.
    """

    shot_scale: ShotScale = ShotScale.ANY
    shot_scale_tolerance: int = Field(default=1, ge=0, le=3)
    camera_motion: list[CameraMotion] = Field(
        default_factory=lambda: [CameraMotion.ANY]
    )
    camera_height: CameraHeight = CameraHeight.ANY
    subject_class: list[SubjectClass] = Field(
        default_factory=lambda: [SubjectClass.ANY]
    )
    narrative_role: NarrativeRole = NarrativeRole.ANY
    composition: Composition = Composition.ANY
    motion_energy: Unit = 0.5
    motion_energy_tolerance: Unit = 0.25
    motion_direction_deg: float | None = Field(default=None, ge=-180, le=180)
    min_quality: Unit = 0.5
    requires_face: bool = False
    requires_speech: bool = False
    semantic_vec: list[float] | None = None
    semantic_hint: str | None = Field(default=None, max_length=300)


class Assignment(_Base):
    segment_id: str
    in_ms: Ms
    out_ms: Ms
    score: Unit = 0.0
    reason: str | None = Field(
        default=None,
        max_length=300,
        description="Surfaced in the swap UI. Explaining the choice is what makes "
        "the user's correction an informative training signal.",
    )
    locked: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> Assignment:
        if self.out_ms <= self.in_ms:
            raise ValueError(f"assignment out_ms ({self.out_ms}) must exceed in_ms ({self.in_ms})")
        return self

    @property
    def duration_ms(self) -> int:
        return self.out_ms - self.in_ms


class Slot(_Base):
    index: int = Field(ge=0)
    t_in_ms: Ms
    t_out_ms: Ms
    importance: Unit
    requirements: SlotRequirements = Field(default_factory=SlotRequirements)
    section: int | None = Field(default=None, ge=0)
    allow_reuse: bool = True
    reuse_penalty: Unit = 0.35
    droppable: bool = True
    assignment: Assignment | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Slot:
        if self.t_out_ms <= self.t_in_ms:
            raise ValueError(f"slot {self.index}: t_out_ms must exceed t_in_ms")
        return self

    @property
    def duration_ms(self) -> int:
        return self.t_out_ms - self.t_in_ms

    @property
    def is_bound(self) -> bool:
        return self.assignment is not None


# ==========================================================================
# Cuts & transitions
# ==========================================================================


class Cut(_Base):
    index: int = Field(ge=0)
    t_ms: Ms
    mode: CutMode
    beat_index: int | None = Field(default=None, ge=0)
    offset_ms: float = Field(
        default=0.0,
        ge=-500,
        le=500,
        description="Signed offset from the anchor beat. Negative = early, which is "
        "what expert edits do. Preserving the reference's own offset distribution "
        "is a large part of why output reads as professionally cut.",
    )
    subdivision: Literal["1", "1/2", "1/3", "1/4", "1/8", "2/3", "3/4"] | None = None
    hide_in_motion: bool = False
    from_slot: int | None = Field(default=None, ge=0)
    to_slot: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _anchor_present(self) -> Cut:
        if self.mode in (CutMode.ON_BEAT, CutMode.SUBDIVIDED) and self.beat_index is None:
            raise ValueError(f"cut {self.index}: mode '{self.mode}' requires beat_index")
        if self.mode is CutMode.SUBDIVIDED and self.subdivision is None:
            raise ValueError(f"cut {self.index}: mode 'subdivided' requires subdivision")
        return self


class Transition(_Base):
    at_cut: int = Field(ge=0)
    type: TransitionType
    duration_ms: int = Field(ge=0, le=4000)
    secondary: list[TransitionType] = Field(
        default_factory=list,
        description="Real transitions are composites -- a whip pan is also a motion blur.",
    )
    intensity: Unit = 0.5
    direction_deg: float | None = Field(default=None, ge=-180, le=180)
    easing: Easing = Easing.EASE_IN_OUT
    align: Literal["centered", "outgoing", "incoming"] = "centered"
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: Confidence = 1.0
    fallback: TransitionType | None = None

    @model_validator(mode="after")
    def _direction_required(self) -> Transition:
        if self.type.needs_direction and self.direction_deg is None:
            raise ValueError(
                f"transition '{self.type.value}' requires direction_deg; without it "
                "the renderer cannot orient the effect and it reads as a bug"
            )
        if self.type is not TransitionType.HARD_CUT and self.duration_ms == 0:
            raise ValueError(f"transition '{self.type.value}' requires duration_ms > 0")
        return self


# ==========================================================================
# Motion / speed / effects
# ==========================================================================


class Keyframe(_Base):
    t_ms: Ms
    value: float
    easing: Easing = Easing.LINEAR


class MotionTrack(_Base):
    slot: int = Field(ge=0)
    kind: Literal["scale", "pos_x", "pos_y", "rotation", "opacity", "anchor_x", "anchor_y"]
    keyframes: list[Keyframe] = Field(min_length=2)
    relative_to_subject: bool = Field(
        default=False,
        description="Anchor on the tracked subject centroid rather than frame centre, "
        "so a push-in stays on the subject instead of drifting off them.",
    )

    @field_validator("keyframes")
    @classmethod
    def _monotonic(cls, v: list[Keyframe]) -> list[Keyframe]:
        if any(b.t_ms <= a.t_ms for a, b in zip(v, v[1:])):
            raise ValueError("keyframes must be strictly increasing in t_ms")
        return v


class SpeedTrack(_Base):
    slot: int = Field(ge=0)
    mode: SpeedMode
    factor: float = Field(default=1.0, gt=0, le=100)
    keyframes: list[Keyframe] = Field(default_factory=list)
    freeze_at_ms: int | None = Field(default=None, ge=0)
    freeze_hold_ms: int | None = Field(default=None, ge=0)
    interpolation: Literal["nearest", "blend", "optical_flow"] = "blend"
    preserve_audio_pitch: bool = True
    confidence: Confidence = 1.0

    @model_validator(mode="after")
    def _mode_consistent(self) -> SpeedTrack:
        if self.mode is SpeedMode.RAMP and len(self.keyframes) < 2:
            raise ValueError("mode 'ramp' requires at least 2 keyframes")
        if self.mode is SpeedMode.FREEZE and self.freeze_at_ms is None:
            raise ValueError("mode 'freeze' requires freeze_at_ms")
        return self


class EffectInstance(_Base):
    type: EffectType
    scope: Literal["global", "section", "slot", "range"]
    slot: int | None = Field(default=None, ge=0)
    section: int | None = Field(default=None, ge=0)
    t_in_ms: int | None = Field(default=None, ge=0)
    t_out_ms: int | None = Field(default=None, ge=0)
    intensity: Unit = 0.5
    keyframes: list[Keyframe] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    blend_mode: Literal[
        "normal", "screen", "add", "overlay", "soft_light", "multiply"
    ] = "normal"
    confidence: Confidence = 1.0

    @model_validator(mode="after")
    def _scope_target(self) -> EffectInstance:
        need = {"slot": "slot", "section": "section"}.get(self.scope)
        if need and getattr(self, need) is None:
            raise ValueError(f"effect scope '{self.scope}' requires field '{need}'")
        if self.scope == "range" and (self.t_in_ms is None or self.t_out_ms is None):
            raise ValueError("effect scope 'range' requires t_in_ms and t_out_ms")
        return self


# ==========================================================================
# Grade
# ==========================================================================


class SplitTone(_Base):
    shadow_hue_deg: float = Field(default=210.0, ge=0, le=360)
    shadow_sat: Unit = 0.0
    highlight_hue_deg: float = Field(default=40.0, ge=0, le=360)
    highlight_sat: Unit = 0.0
    balance: Signed = 0.0


class HslBand(_Base):
    band: Literal["red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"]
    hue_shift_deg: float = Field(default=0.0, ge=-60, le=60)
    sat: Signed = 0.0
    lum: Signed = 0.0


class GradeParams(_Base):
    exposure: float = Field(default=0.0, ge=-4, le=4, description="Stops")
    contrast: Signed = 0.0
    pivot: Unit = 0.435
    saturation: float = Field(default=1.0, ge=0, le=3)
    vibrance: Signed = 0.0
    temperature: Signed = 0.0
    tint: Signed = 0.0
    lift: tuple[float, float, float] = (0.0, 0.0, 0.0)
    gamma: tuple[float, float, float] = (1.0, 1.0, 1.0)
    gain: tuple[float, float, float] = (1.0, 1.0, 1.0)
    shadows: Signed = 0.0
    highlights: Signed = 0.0
    whites: Signed = 0.0
    blacks: Signed = 0.0
    split_tone: SplitTone = Field(default_factory=SplitTone)
    hsl: list[HslBand] = Field(default_factory=list)


class GradeMatchTarget(_Base):
    """Measured colour statistics of the reference.

    The renderer optimises the user's footage *toward these statistics* rather
    than blindly applying GradeParams -- because the user's footage starts from
    a different exposure, and applying a grade authored for someone else's
    footage is how you get crushed blacks.
    """

    luma_percentiles: dict[str, float] = Field(default_factory=dict)
    mean_lab: tuple[float, float, float] | None = None
    sat_mean: float | None = Field(default=None, ge=0, le=2)
    shadow_hue_deg: float | None = Field(default=None, ge=0, le=360)
    highlight_hue_deg: float | None = Field(default=None, ge=0, le=360)
    split_tone_strength: Unit | None = None


class SlotGrade(_Base):
    slot: int = Field(ge=0)
    params: GradeParams


class Grade(_Base):
    """Parametric, not a recovered LUT.

    Exact LUT recovery from a delivered, lossily-compressed video is an
    under-determined inverse problem: the camera-native 'before' does not exist
    and 4:2:0 chroma subsampling already destroyed the fine colour detail the
    inversion would need. See docs/08 section 5.
    """

    confidence: Confidence = 1.0
    global_: GradeParams = Field(default_factory=GradeParams, alias="global")
    per_slot: list[SlotGrade] = Field(default_factory=list)
    lut_ref: str | None = Field(
        default=None,
        description="ID of a LICENSED LUT approximating the measured look. Never a "
        "LUT extracted from the reference.",
    )
    match_target: GradeMatchTarget | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ==========================================================================
# Text & captions
# ==========================================================================


class Position(_Base):
    x_pct: float = Field(default=50.0, ge=-50, le=150)
    y_pct: float = Field(default=78.0, ge=-50, le=150)
    anchor: Literal[
        "center", "top_left", "top_center", "top_right", "center_left",
        "center_right", "bottom_left", "bottom_center", "bottom_right",
    ] = "center"


class TextShadow(_Base):
    enabled: bool = False
    color: HexColor = "#000000"
    opacity: Unit = 0.5
    blur_px: float = Field(default=8.0, ge=0, le=60)
    offset_x: float = 0.0
    offset_y: float = 4.0


class TextBackground(_Base):
    enabled: bool = False
    color: HexColor = "#000000"
    opacity: Unit = 0.6
    padding_px: float = Field(default=12.0, ge=0, le=80)
    radius_px: float = Field(default=8.0, ge=0, le=80)


class TextStyle(_Base):
    font_family: FontFamily = FontFamily.SANS_GROTESQUE
    font_id: str | None = None
    weight: Literal[100, 200, 300, 400, 500, 600, 700, 800, 900] = 800
    italic: bool = False
    uppercase: bool = True
    size_pct: float = Field(default=6.5, ge=0.5, le=40)
    letter_spacing: float = Field(default=0.0, ge=-0.2, le=1.0)
    line_height: float = Field(default=1.15, ge=0.6, le=3.0)
    color: HexColor = "#FFFFFF"
    stroke_color: HexColor = "#000000"
    stroke_width_px: float = Field(default=0.0, ge=0, le=40)
    shadow: TextShadow = Field(default_factory=TextShadow)
    background: TextBackground = Field(default_factory=TextBackground)


class TextAnimation(_Base):
    kind: TextAnimationKind = TextAnimationKind.NONE
    duration_ms: int = Field(default=180, ge=0, le=2000)
    easing: Easing = Easing.EASE_OUT
    overshoot: Unit = 0.0
    stagger_ms: int = Field(default=0, ge=0, le=500)


class CaptionTrack(_Base):
    enabled: bool = False
    mode: CaptionMode = CaptionMode.NONE
    source: Literal["asr", "user", "none"] = "asr"
    style: TextStyle = Field(default_factory=TextStyle)
    max_words_per_chunk: int = Field(default=3, ge=1, le=20)
    position: Position = Field(default_factory=Position)
    active_word_style: TextStyle | None = None
    entry: TextAnimation = Field(default_factory=TextAnimation)
    exit: TextAnimation = Field(default_factory=TextAnimation)
    lead_ms: int = Field(default=0, ge=-300, le=300)
    confidence: Confidence = 1.0

    @model_validator(mode="after")
    def _karaoke_needs_active_style(self) -> CaptionTrack:
        if self.mode is CaptionMode.KARAOKE and self.active_word_style is None:
            raise ValueError(
                "karaoke mode requires active_word_style; without it the highlighted "
                "word is indistinguishable and the mode is pointless"
            )
        return self


class TextObject(_Base):
    """Titles and graphics, distinct from speech captions.

    ``content`` defaults to None on purpose. We transfer the *style* of a title,
    never its words. Copying the reference's text would be copying content.
    """

    id: str
    t_in_ms: Ms
    t_out_ms: Ms
    style: TextStyle
    content: str | None = Field(default=None, max_length=500)
    placeholder: str | None = Field(default=None, max_length=200)
    position: Position = Field(default_factory=Position)
    entry: TextAnimation = Field(default_factory=TextAnimation)
    exit: TextAnimation = Field(default_factory=TextAnimation)
    motion: list[MotionTrack] = Field(default_factory=list)


# ==========================================================================
# Reframe, constraints, degradation
# ==========================================================================


class ReframeTrack(_Base):
    slot: int = Field(ge=0)
    mode: Literal[
        "fit", "fill_center", "fill_subject", "track_subject", "manual", "blurred_bars"
    ] = "fill_subject"
    subject_query: str | None = None
    smoothing: Unit = Field(
        default=0.7,
        description="Virtual-camera laziness. Without it, per-frame mask noise "
        "produces visible jitter -- the commonest failure in auto-reframe products.",
    )
    max_pan_speed_pct_per_s: float = Field(default=35.0, ge=0, le=200)
    keyframes: list[Keyframe] = Field(default_factory=list)
    padding_pct: float = Field(default=8.0, ge=0, le=50)


class Constraints(_Base):
    min_shot_ms: int = Field(
        default=180, ge=40,
        description="Below ~180ms a shot is not perceived as a shot, only as a flicker.",
    )
    max_shot_ms: int = Field(default=8000, ge=100)
    max_consecutive_same_scale: int = Field(default=2, ge=1)
    max_consecutive_same_source: int = Field(default=1, ge=1)
    max_segment_reuse: int = Field(default=3, ge=1)
    min_reuse_gap_ms: int = Field(
        default=4000, ge=0,
        description="Reuse is invisible when far apart and obvious when adjacent.",
    )
    forbid_jump_cut_same_source: bool = True
    require_licensed_audio: Literal[True] = True
    max_effect_layers: int = Field(default=4, ge=1)


class Compromise(_Base):
    kind: CompromiseKind
    detail: str = Field(max_length=300)
    slot: int | None = Field(default=None, ge=0)
    severity: Literal["minor", "moderate", "major"] = "minor"


class Degradation(_Base):
    degraded: bool = False
    compromises: list[Compromise] = Field(default_factory=list)
    coverage: Unit = 1.0

    @model_validator(mode="after")
    def _flag_consistent(self) -> Degradation:
        if self.compromises and not self.degraded:
            raise ValueError(
                "compromises recorded but degraded=False; a degraded render that "
                "does not say so is the worst output this system can produce"
            )
        return self


# ==========================================================================
# The blueprint
# ==========================================================================


class Blueprint(_Base):
    ebp_version: Literal["1.0"] = EBP_VERSION
    id: str = Field(pattern=r"^bp_[a-zA-Z0-9]{12,}$")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    provenance: Provenance
    canvas: Canvas
    audio: AudioTrack
    style: StyleProfile
    slots: list[Slot] = Field(min_length=1)
    cuts: list[Cut] = Field(min_length=1)

    parent_id: str | None = Field(default=None, pattern=r"^bp_[a-zA-Z0-9]{12,}$")
    name: str | None = Field(default=None, max_length=200)
    transitions: list[Transition] = Field(default_factory=list)
    motion: list[MotionTrack] = Field(default_factory=list)
    speed: list[SpeedTrack] = Field(default_factory=list)
    effects: list[EffectInstance] = Field(default_factory=list)
    grade: Grade = Field(default_factory=Grade)
    captions: CaptionTrack = Field(default_factory=CaptionTrack)
    text_objects: list[TextObject] = Field(default_factory=list)
    reframe: list[ReframeTrack] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)
    degradation: Degradation = Field(default_factory=Degradation)

    # ---- cross-field invariants JSON Schema cannot express ----------------

    @model_validator(mode="after")
    def _slots_ordered_and_indexed(self) -> Blueprint:
        for i, s in enumerate(self.slots):
            if s.index != i:
                raise ValueError(f"slots[{i}].index is {s.index}; must equal position")
        for a, b in zip(self.slots, self.slots[1:]):
            if b.t_in_ms < a.t_out_ms:
                raise ValueError(
                    f"slots {a.index} and {b.index} overlap "
                    f"({a.t_out_ms} > {b.t_in_ms})"
                )
        return self

    @model_validator(mode="after")
    def _cuts_ordered_and_indexed(self) -> Blueprint:
        for i, c in enumerate(self.cuts):
            if c.index != i:
                raise ValueError(f"cuts[{i}].index is {c.index}; must equal position")
        for a, b in zip(self.cuts, self.cuts[1:]):
            if b.t_ms <= a.t_ms:
                raise ValueError(f"cuts {a.index} and {b.index} are not increasing in time")
        return self

    @model_validator(mode="after")
    def _references_resolvable(self) -> Blueprint:
        n_slots, n_cuts = len(self.slots), len(self.cuts)
        n_sections = len(self.audio.sections)

        for t in self.transitions:
            if t.at_cut >= n_cuts:
                raise ValueError(f"transition references cut {t.at_cut}; only {n_cuts} exist")
        for m in self.motion:
            if m.slot >= n_slots:
                raise ValueError(f"motion references slot {m.slot}; only {n_slots} exist")
        for sp in self.speed:
            if sp.slot >= n_slots:
                raise ValueError(f"speed references slot {sp.slot}; only {n_slots} exist")
        for r in self.reframe:
            if r.slot >= n_slots:
                raise ValueError(f"reframe references slot {r.slot}; only {n_slots} exist")
        for e in self.effects:
            if e.slot is not None and e.slot >= n_slots:
                raise ValueError(f"effect references slot {e.slot}; only {n_slots} exist")
            if e.section is not None and e.section >= n_sections:
                raise ValueError(f"effect references section {e.section}; only {n_sections} exist")
        for c in self.cuts:
            for ref, label in ((c.from_slot, "from_slot"), (c.to_slot, "to_slot")):
                if ref is not None and ref >= n_slots:
                    raise ValueError(f"cut {c.index}.{label} references slot {ref}; only {n_slots} exist")
        for g in self.grade.per_slot:
            if g.slot >= n_slots:
                raise ValueError(f"grade override references slot {g.slot}; only {n_slots} exist")
        for s in self.slots:
            if s.section is not None and s.section >= n_sections:
                raise ValueError(f"slot {s.index} references section {s.section}; only {n_sections} exist")
        for sfx in self.audio.sfx:
            if sfx.bound_to_cut is not None and sfx.bound_to_cut >= n_cuts:
                raise ValueError(f"sfx bound_to_cut {sfx.bound_to_cut}; only {n_cuts} cuts exist")
        return self

    @model_validator(mode="after")
    def _within_canvas_duration(self) -> Blueprint:
        if self.slots[-1].t_out_ms > self.canvas.duration_ms:
            raise ValueError(
                f"last slot ends at {self.slots[-1].t_out_ms}ms, beyond canvas "
                f"duration {self.canvas.duration_ms}ms"
            )
        return self

    @model_validator(mode="after")
    def _min_shot_respected(self) -> Blueprint:
        for s in self.slots:
            if s.duration_ms < self.constraints.min_shot_ms:
                raise ValueError(
                    f"slot {s.index} is {s.duration_ms}ms, below min_shot_ms "
                    f"({self.constraints.min_shot_ms}); it would read as a flicker"
                )
        return self

    # ---- convenience ------------------------------------------------------

    @property
    def is_bound(self) -> bool:
        """True when every non-dropped slot has an assignment."""
        return all(s.is_bound for s in self.slots)

    @property
    def duration_ms(self) -> int:
        return self.canvas.duration_ms

    def transition_at(self, cut_index: int) -> Transition | None:
        return next((t for t in self.transitions if t.at_cut == cut_index), None)

    def slots_in_section(self, section_index: int) -> list[Slot]:
        return [s for s in self.slots if s.section == section_index]

    def effect_count(self, effect: EffectType, section: int | None = None) -> int:
        return sum(
            1
            for e in self.effects
            if e.type is effect and (section is None or e.section == section)
        )

    def check_effect_budget(self) -> list[str]:
        """Report effects exceeding the style's declared restraint. Empty is good."""
        problems: list[str] = []
        for name, limit in self.style.effect_budget.items():
            try:
                effect = EffectType(name)
            except ValueError:
                problems.append(f"unknown effect '{name}' in effect_budget")
                continue
            for si in range(len(self.audio.sections)):
                used = self.effect_count(effect, si)
                if used > limit:
                    problems.append(
                        f"section {si}: {used}x {name} exceeds budget of {limit}"
                    )
        return problems

    def low_confidence_subsystems(self, floor: float = CONFIDENCE_FLOOR) -> list[str]:
        """Subsystems the UI must label as approximate."""
        conf = self.provenance.confidence.model_dump(exclude_none=True)
        return [k for k, v in conf.items() if k != "overall" and v < floor]
