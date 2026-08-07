"""Canonical vocabulary shared by the reference analyser and the footage indexer.

These enums are the reason clip matching is possible at all. If the analyser
emits ``"motorbike"`` and the indexer emits ``"motorcycle"``, matching degrades
to embedding similarity alone and the structural constraints stop working.
Every model that produces one of these values does so under constrained
decoding against this vocabulary -- never free text.

See docs/04-ai-pipeline.md and docs/06-blueprint-spec.md.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """str-valued enum; serialises to a bare string in JSON."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


# --------------------------------------------------------------------------
# Shot vocabulary
# --------------------------------------------------------------------------


class ShotScale(StrEnum):
    """Derived geometrically from subject-area ratio, not asked of a VLM.

    Thresholds (subject_mask_area / frame_area), applied with hysteresis so a
    subject hovering on a boundary does not oscillate between buckets:

        extreme_close   > 0.55
        close             0.28 - 0.55
        medium_close      0.15 - 0.28
        medium            0.07 - 0.15
        wide              0.02 - 0.07
        extreme_wide    < 0.02
    """

    EXTREME_CLOSE = "extreme_close"
    CLOSE = "close"
    MEDIUM_CLOSE = "medium_close"
    MEDIUM = "medium"
    WIDE = "wide"
    EXTREME_WIDE = "extreme_wide"
    ANY = "any"

    @property
    def ordinal(self) -> int:
        """Position on the tight->loose axis. ``ANY`` has no position."""
        order = [
            ShotScale.EXTREME_CLOSE,
            ShotScale.CLOSE,
            ShotScale.MEDIUM_CLOSE,
            ShotScale.MEDIUM,
            ShotScale.WIDE,
            ShotScale.EXTREME_WIDE,
        ]
        if self is ShotScale.ANY:
            raise ValueError("ShotScale.ANY has no ordinal position")
        return order.index(self)

    def distance(self, other: "ShotScale") -> int:
        """Buckets between two scales. ``ANY`` matches anything at distance 0."""
        if self is ShotScale.ANY or other is ShotScale.ANY:
            return 0
        return abs(self.ordinal - other.ordinal)


class CameraMotion(StrEnum):
    STATIC = "static"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    TILT_UP = "tilt_up"
    TILT_DOWN = "tilt_down"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    DOLLY_IN = "dolly_in"
    DOLLY_OUT = "dolly_out"
    TRUCK_LEFT = "truck_left"
    TRUCK_RIGHT = "truck_right"
    ROLL = "roll"
    HANDHELD = "handheld"
    TRACKING = "tracking"
    ORBIT = "orbit"
    AERIAL = "aerial"
    ANY = "any"

    @property
    def is_lateral(self) -> bool:
        """Whether this motion can support a directional (whip/slide) transition."""
        return self in {
            CameraMotion.PAN_LEFT,
            CameraMotion.PAN_RIGHT,
            CameraMotion.TRUCK_LEFT,
            CameraMotion.TRUCK_RIGHT,
            CameraMotion.TRACKING,
        }

    @property
    def is_push(self) -> bool:
        return self in {
            CameraMotion.ZOOM_IN,
            CameraMotion.ZOOM_OUT,
            CameraMotion.DOLLY_IN,
            CameraMotion.DOLLY_OUT,
        }


class CameraHeight(StrEnum):
    GROUND = "ground"
    LOW = "low"
    EYE = "eye"
    HIGH = "high"
    AERIAL = "aerial"
    ANY = "any"


class SubjectClass(StrEnum):
    """Deliberately coarse.

    Fine-grained classes ("sedan" vs "coupe") do not help matching and hurt
    cross-domain transfer, which is the entire product: a car reference must be
    able to map onto motorcycle footage. Coarse classes are what make
    ``mechanical_detail`` a bridge between a wheel and an exhaust.
    """

    PERSON_FACE = "person_face"
    PERSON_BODY = "person_body"
    PERSON_GROUP = "person_group"
    VEHICLE = "vehicle"
    MECHANICAL_DETAIL = "mechanical_detail"
    ANIMAL = "animal"
    FOOD = "food"
    PRODUCT = "product"
    ARCHITECTURE = "architecture"
    LANDSCAPE = "landscape"
    SKY = "sky"
    WATER = "water"
    TEXT_GRAPHIC = "text_graphic"
    ABSTRACT = "abstract"
    CROWD = "crowd"
    ANY = "any"


class NarrativeRole(StrEnum):
    HOOK = "hook"
    ESTABLISH = "establish"
    DETAIL = "detail"
    ACTION_BEAT = "action_beat"
    REACTION = "reaction"
    REVEAL = "reveal"
    TRANSITION_SHOT = "transition_shot"
    PAYOFF = "payoff"
    OUTRO = "outro"
    ANY = "any"


class Composition(StrEnum):
    CENTERED = "centered"
    THIRDS_LEFT = "thirds_left"
    THIRDS_RIGHT = "thirds_right"
    SYMMETRIC = "symmetric"
    NEGATIVE_SPACE_LEFT = "negative_space_left"
    NEGATIVE_SPACE_RIGHT = "negative_space_right"
    LOW_HORIZON = "low_horizon"
    HIGH_HORIZON = "high_horizon"
    ANY = "any"


# --------------------------------------------------------------------------
# Editorial vocabulary
# --------------------------------------------------------------------------


class CutMode(StrEnum):
    ON_BEAT = "on_beat"
    SUBDIVIDED = "subdivided"
    FREE = "free"
    IMPACT = "impact"


class TransitionType(StrEnum):
    HARD_CUT = "hard_cut"
    CROSS_DISSOLVE = "cross_dissolve"
    FADE_BLACK = "fade_black"
    FADE_WHITE = "fade_white"
    FLASH = "flash"
    BLUR = "blur"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    WHIP_PAN = "whip_pan"
    SPIN = "spin"
    MOTION_BLUR = "motion_blur"
    RGB_SPLIT = "rgb_split"
    FILM_BURN = "film_burn"
    LIGHT_LEAK = "light_leak"
    SHAKE = "shake"
    GLITCH = "glitch"
    MASK_WIPE = "mask_wipe"
    LUMA_WIPE = "luma_wipe"
    PUSH = "push"
    SLIDE = "slide"
    MORPH = "morph"
    CUSTOM = "custom"

    @property
    def needs_direction(self) -> bool:
        """Transitions that read as broken without a coherent motion direction."""
        return self in {
            TransitionType.WHIP_PAN,
            TransitionType.PUSH,
            TransitionType.SLIDE,
            TransitionType.MASK_WIPE,
        }

    @property
    def needs_motion(self) -> bool:
        """Transitions requiring the adjacent footage to actually be moving."""
        return self in {
            TransitionType.WHIP_PAN,
            TransitionType.MOTION_BLUR,
            TransitionType.SPIN,
        }


class EffectType(StrEnum):
    GLOW = "glow"
    BLOOM = "bloom"
    FILM_GRAIN = "film_grain"
    VHS = "vhs"
    CRT = "crt"
    CHROMATIC_ABERRATION = "chromatic_aberration"
    LENS_DISTORTION = "lens_distortion"
    GAUSSIAN_BLUR = "gaussian_blur"
    RADIAL_BLUR = "radial_blur"
    DIRECTIONAL_BLUR = "directional_blur"
    SHARPEN = "sharpen"
    VIGNETTE = "vignette"
    NOISE = "noise"
    PARTICLES = "particles"
    LIGHT_LEAK = "light_leak"
    DUST_OVERLAY = "dust_overlay"
    HALATION = "halation"
    SCANLINES = "scanlines"
    PIXELATE = "pixelate"
    DATAMOSH = "datamosh"
    PRISM = "prism"
    SHAKE = "shake"
    LETTERBOX = "letterbox"


class SpeedMode(StrEnum):
    CONSTANT = "constant"
    RAMP = "ramp"
    FREEZE = "freeze"
    REVERSE = "reverse"
    TIMELAPSE = "timelapse"
    HYPERLAPSE = "hyperlapse"


class Easing(StrEnum):
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"
    CUBIC_IN = "cubic_in"
    CUBIC_OUT = "cubic_out"
    CUBIC_IN_OUT = "cubic_in_out"
    EXPO_IN = "expo_in"
    EXPO_OUT = "expo_out"
    BACK_OUT = "back_out"
    BOUNCE_OUT = "bounce_out"
    HOLD = "hold"


# --------------------------------------------------------------------------
# Audio vocabulary
# --------------------------------------------------------------------------


class SectionKind(StrEnum):
    INTRO = "intro"
    VERSE = "verse"
    PRE_CHORUS = "pre_chorus"
    BUILD = "build"
    DROP = "drop"
    CHORUS = "chorus"
    BRIDGE = "bridge"
    BREAKDOWN = "breakdown"
    OUTRO = "outro"
    UNKNOWN = "unknown"


class MusicStrategy(StrEnum):
    """How the rhythmic skeleton is realised with real audio.

    There is deliberately no member for "reuse the reference's track". The
    absence is the point -- see docs/18-legal-ethics.md.
    """

    CATALOGUE_MATCH = "catalogue_match"
    USER_SUPPLIED = "user_supplied"
    SILENT = "silent"
    GENERATED = "generated"

    @property
    def requires_licence(self) -> bool:
        return self in {MusicStrategy.CATALOGUE_MATCH, MusicStrategy.GENERATED}


class CaptionMode(StrEnum):
    WORD_BY_WORD = "word_by_word"
    KARAOKE = "karaoke"
    PHRASE = "phrase"
    LINE = "line"
    STATIC = "static"
    NONE = "none"


class FontFamily(StrEnum):
    """A *classified* family, mapped to a licensed font at render time.

    We do not claim to identify the exact typeface used in a reference.
    Compressed video does not preserve enough glyph detail for that to be
    reliable, and a confidently wrong font is worse than an honest family match.
    """

    SANS_GEOMETRIC = "sans_geometric"
    SANS_GROTESQUE = "sans_grotesque"
    SANS_ROUNDED = "sans_rounded"
    SANS_CONDENSED = "sans_condensed"
    SERIF = "serif"
    SLAB = "slab"
    DISPLAY_HEAVY = "display_heavy"
    HANDWRITTEN = "handwritten"
    MONO = "mono"


class TextAnimationKind(StrEnum):
    NONE = "none"
    FADE = "fade"
    SLIDE_UP = "slide_up"
    SLIDE_DOWN = "slide_down"
    SLIDE_LEFT = "slide_left"
    SLIDE_RIGHT = "slide_right"
    POP = "pop"
    BOUNCE = "bounce"
    TYPEWRITER = "typewriter"
    SCALE_IN = "scale_in"
    BLUR_IN = "blur_in"
    WIPE = "wipe"
    SHAKE = "shake"


class CompromiseKind(StrEnum):
    SLOT_DROPPED = "slot_dropped"
    TRANSITION_SUBSTITUTED = "transition_substituted"
    EFFECT_SKIPPED = "effect_skipped"
    SPEED_FLATTENED = "speed_flattened"
    REUSE_EXCEEDED = "reuse_exceeded"
    QUALITY_BELOW_THRESHOLD = "quality_below_threshold"
    GRADE_LOW_CONFIDENCE = "grade_low_confidence"
    MUSIC_TEMPO_MISMATCH = "music_tempo_mismatch"
