"""Reference analysis pipeline.

Nine stages producing an Editing Blueprint from a reference video. Runs ONCE
per unique reference, ever -- cached by perceptual fingerprint. Budget: 75s
wall-clock cold, ~2s warm.

Stages 1-6 run concurrently over the same decoded frame streams; stage 2 must
complete first because 3-6 operate per-shot.

See docs/04-ai-pipeline.md part A.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("reelsedits.analyzer")


# ---------------------------------------------------------------------------
# stage contracts
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MediaProfile:
    container: str
    video_codec: str
    width: int
    height: int
    fps: float
    #: Phone footage is frequently variable frame rate. This flag is the source
    #: of a whole class of subtle timing bugs; every downstream stage must
    #: index into the PTS map rather than compute frame_index / fps.
    is_vfr: bool
    pts_map_ms: list[float]
    duration_ms: int
    rotation: int = 0
    has_audio: bool = True
    color_primaries: str = "bt709"


@dataclass(slots=True)
class Proxies:
    """Three derived streams, deliberately different.

    Semantic models gain nothing from 60fps; motion analysis is destroyed by
    2fps. Running everything at full resolution and frame rate would roughly
    quadruple cost for no accuracy gain.
    """

    analysis: Path      # 512px long edge, 2 fps  -- semantic models
    motion: Path        # 256px, full frame rate  -- optical flow, SBD
    audio: Path         # 44.1kHz mono WAV


@dataclass(slots=True)
class StageResult:
    name: str
    data: dict[str, Any]
    confidence: float
    gpu_seconds: float
    artefact_key: str | None = None


class Stage(Protocol):
    name: str

    async def run(self, ctx: AnalysisContext) -> StageResult: ...


@dataclass
class AnalysisContext:
    reference_id: str
    source: Path
    profile: MediaProfile | None = None
    proxies: Proxies | None = None
    results: dict[str, StageResult] = field(default_factory=dict)
    analyzer_version: str = "1.4.2"

    def require(self, stage: str) -> dict[str, Any]:
        if stage not in self.results:
            raise RuntimeError(f"stage '{stage}' has not run")
        return self.results[stage].data


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


class ProbeStage:
    """Stage 0 -- probe, demux, build proxies. CPU. ~2s."""

    name = "probe"

    async def run(self, ctx: AnalysisContext) -> StageResult:
        # TODO: ffprobe -> MediaProfile; build the PTS map explicitly so that
        #       "frame 412" means the same instant in every downstream stage
        # TODO: generate the three proxies in one ffmpeg invocation
        raise NotImplementedError


class AudioStage:
    """Stage 1 -- the rhythmic skeleton. GPU. ~8s.

    Highest-value stage in the pipeline. Source separation runs FIRST because
    beat detection on a full mix with loud vocals and heavy sidechain is
    materially worse than on an isolated drum stem, and the same separation
    improves structure boundaries and SFX isolation.

    The extracted audio is DELETED at the end of this stage. That is an
    explicit, tested step -- not an implicit consequence of temp cleanup.
    See docs/18.
    """

    name = "audio"

    async def run(self, ctx: AnalysisContext) -> StageResult:
        # TODO: Demucs v4 -> stems
        # TODO: transformer beat tracker (primary) + madmom DBN (confidence signal)
        # TODO: CQT self-similarity -> section boundaries, snapped to downbeats
        # TODO: RMS + spectral flux + LUFS -> energy curve at 20Hz
        # TODO: energy-derivative peaks & downbeats -> impacts
        # TODO: CLAP embedding -> mood, genre
        # TODO: onsets on the 'other' stem -> SFX events
        # TODO: assert audio deleted before returning
        raise NotImplementedError


class StructureStage:
    """Stage 2 -- shots and transitions. GPU. ~6s.

    Ensemble of TransNetV2 and an AutoShot-class model. Agreement is a free
    confidence signal; disagreement is resolved by frame-difference plus
    histogram distance at that locus. Gradual transitions yield an INTERVAL,
    not a point -- collapsing it would lose the transition duration the
    blueprint needs.
    """

    name = "structure"

    async def run(self, ctx: AnalysisContext) -> StageResult:
        # TODO: run both SBD models on the motion proxy
        # TODO: fuse -> boundaries with confidence, gradual intervals
        # TODO: classify each interval (see docs/08 section 4.1)
        raise NotImplementedError


class MotionStage:
    """Stage 3 -- optical flow and camera motion. GPU. ~12s.

    Flow rather than a learned camera-motion classifier, because we need the
    magnitude CURVE, not just a label: it drives cut micro-placement,
    speed-ramp detection, transition direction, and the motion_energy the
    matcher relies on. A classifier gives one label and loses four things.
    """

    name = "motion"

    async def run(self, ctx: AnalysisContext) -> StageResult:
        # TODO: SEA-RAFT dense flow on the motion proxy
        # TODO: RANSAC-fit affine/homography -> global camera motion,
        #       residual -> subject motion (tracking-shot detection)
        # TODO: Helmholtz decomposition -> divergence (zoom) vs curl (spin)
        # TODO: high-frequency energy -> handheld / stabilised / gimbal
        # TODO: three-estimator speed inference; below 0.6 confidence record
        #       speed 1.0 rather than guessing (docs/08 section 7)
        raise NotImplementedError


class SemanticStage:
    """Stage 4 -- what is actually happening. GPU. ~18s.

    Every categorical value is produced under CONSTRAINED DECODING against the
    enums in reelsedits_common. Free text produces a long tail of near-synonyms
    (motorbike / motorcycle / bike) that silently destroys matching.

    Shot scale is MEASURED (subject_mask_area / frame_area), not asked of the
    VLM. Measurements should be measured.
    """

    name = "semantics"

    async def run(self, ctx: AnalysisContext) -> StageResult:
        # TODO: sample 4-8 frames per shot -> VLM with JSON schema constraint
        # TODO: SAM 3 concept segmentation -> masks, stable IDs, trajectories
        # TODO: subject_area_ratio -> ShotScale with hysteresis
        # TODO: faces: detection, expression, gaze; identity embeddings are
        #       EPHEMERAL -- within-job continuity only, never persisted
        # TODO: composition: thirds, symmetry, negative space, horizon
        raise NotImplementedError


class GradeStage:
    """Stage 5 -- colour and effects. GPU. ~7s.

    Does NOT recover a LUT. Cannot: the camera-native 'before' does not exist,
    4:2:0 compression destroyed the fine colour detail, and clipping is not
    invertible. See docs/08 section 5 for the full argument.

    Instead: measure the delivered look, fit parametric GradeParams by
    optimising the USER's footage toward the reference's statistics, and
    report an honest confidence.
    """

    name = "grade"

    async def run(self, ctx: AnalysisContext) -> StageResult:
        # TODO: per-shot and global histograms in RGB and Lab
        # TODO: split-tone signature (mean hue of darkest 15% vs brightest 15%)
        # TODO: L-BFGS-B fit of GradeParams with a skin-tone penalty term
        # TODO: k-means palette in Lab
        # TODO: effect detectors: grain PSD, vignette falloff, aberration,
        #       glow halo energy, VHS scanline periodicity, light leak gradient
        # TODO: confidence from bitrate, clipping fraction, fit residual
        raise NotImplementedError


class TextStage:
    """Stage 6 -- text, captions, speech. GPU. ~9s.

    font_family is CLASSIFIED into one of nine families and mapped to a
    licensed font. We do not claim to identify the exact typeface -- compressed
    video does not preserve enough glyph detail, and a confidently wrong font
    is worse than an honest family match.
    """

    name = "text"

    async def run(self, ctx: AnalysisContext) -> StageResult:
        # TODO: PaddleOCR at 4fps, VLM fallback below confidence threshold
        # TODO: group detections into temporal text objects
        # TODO: WhisperX -> word-level timestamps (non-negotiable: caption
        #       style cannot be reproduced without them)
        # TODO: correlate text-object changes with word timings -> caption mode
        raise NotImplementedError


class FusionStage:
    """Stage 7 -- the editorial view. CPU. ~3s.

    Turns per-shot measurements into editorial structure: beat-relative cut
    mapping (including the signed offset), pacing profile, shot-type sequence,
    and effect budget.
    """

    name = "fusion"

    async def run(self, ctx: AnalysisContext) -> StageResult:
        # TODO: map every cut to (beat_index, signed offset_ms, subdivision, mode)
        # TODO: extract the offset DISTRIBUTION per section -- mean and stddev.
        #       Experts cut 20-60ms early; reproducing the distribution rather
        #       than snapping to the grid is what makes rhythm feel performed.
        # TODO: cut density vs energy envelope -> per-section target_cut_density
        # TODO: effect counts per section -> effect_budget (restraint is style)
        raise NotImplementedError


class PlannerStage:
    """Stage 8 -- intent and generalisation. Frontier LLM. ~6s.

    The ONLY non-deterministic component, and it sits upstream of the
    blueprint. Its output is schema-validated and invariant-checked, then
    frozen; everything downstream is a pure function.

    temperature=0.2, fixed seed, model version recorded in provenance.
    """

    name = "planner"

    async def run(self, ctx: AnalysisContext) -> StageResult:
        # TODO: serialise fused analysis (~8-20k tokens)
        # TODO: frontier LLM -> intent explanation, slot REQUIREMENTS (not
        #       references to reference shots), importance weights,
        #       confidence flags
        # TODO: fall back to open-weight VLM, stamp planner_tier='fallback'
        # TODO: validate against schemas/blueprint.schema.json AND the Pydantic
        #       invariants before returning
        raise NotImplementedError


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

#: Stage 2 must precede 3-6; 1 is independent. 7 and 8 are sequential.
STAGE_GRAPH: list[list[Stage]] = [
    [ProbeStage()],
    [AudioStage(), StructureStage()],
    [MotionStage(), SemanticStage(), GradeStage(), TextStage()],
    [FusionStage()],
    [PlannerStage()],
]


async def analyze(ctx: AnalysisContext) -> dict[str, StageResult]:
    """Run the pipeline, persisting each stage's artefact.

    Per-stage artefacts go to S3 so a failure in stage 4 re-runs only stage 4.
    On a pipeline this failure-prone that converts most incidents from
    "re-run 47 GPU-seconds" into "re-run 18".
    """
    for wave in STAGE_GRAPH:
        results = await asyncio.gather(
            *(stage.run(ctx) for stage in wave), return_exceptions=True
        )
        for stage, result in zip(wave, results):
            if isinstance(result, BaseException):
                log.error("stage %s failed for %s: %s", stage.name, ctx.reference_id, result)
                raise result
            ctx.results[stage.name] = result
            # TODO: persist result.artefact_key to S3
    return ctx.results
