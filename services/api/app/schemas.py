"""Request and response models for the public API.

Deliberately separate from the blueprint models in ``reelsedits_common``: the
API surface and the blueprint format version independently (docs/12 section 8),
and coupling them would force an API major every time the blueprint evolves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# references
# ---------------------------------------------------------------------------


class ReferenceCreate(_Model):
    source_url: HttpUrl | None = None
    asset_id: str | None = None
    name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _one_source(self) -> "ReferenceCreate":
        if bool(self.source_url) == bool(self.asset_id):
            raise ValueError("provide exactly one of source_url or asset_id")
        return self


class ReferenceOut(_Model):
    id: str
    status: Literal["analyzing", "ready", "failed"]
    cache_hit: bool
    estimated_ready_in_ms: int
    blueprint_id: str | None = None
    events_url: str | None = None
    error: str | None = None


class StyleCard(_Model):
    """The screen where the user decides whether to trust the system.

    Deliberately contains no frames from the reference -- only the derived
    description. See docs/01 section 3.1.
    """

    blueprint_id: str
    summary: str
    pacing: dict[str, float]
    transition_mix: dict[str, float]
    shot_scale_mix: dict[str, float]
    palette: list[dict[str, Any]]
    tags: list[str]
    confidence: dict[str, float]
    low_confidence_subsystems: list[str] = Field(
        default_factory=list,
        description="Subsystems the UI must label 'approximate' rather than present "
                    "as measurements.",
    )


# ---------------------------------------------------------------------------
# assets
# ---------------------------------------------------------------------------


class AssetCreate(_Model):
    kind: Literal["reference", "clip", "audio", "lut", "font", "overlay"] = "clip"
    filename: str = Field(max_length=500)
    bytes: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    project_id: str | None = None


class AssetBatchCreate(_Model):
    project_id: str | None = None
    assets: list[AssetCreate] = Field(min_length=1, max_length=100)


class UploadPart(_Model):
    part_number: int
    url: str


class UploadInstructions(_Model):
    method: Literal["single", "multipart"]
    upload_id: str | None = None
    part_size: int
    parts: list[UploadPart]


class AssetOut(_Model):
    id: str
    kind: str
    status: str
    dedupe_hit: bool = False
    upload: UploadInstructions | None = None
    profile: dict[str, Any] | None = None
    error: str | None = None


class MultipartComplete(_Model):
    parts: list[dict[str, Any]] = Field(min_length=1)


# ---------------------------------------------------------------------------
# projects & coverage
# ---------------------------------------------------------------------------


class ProjectCreate(_Model):
    name: str = Field(default="Untitled", max_length=200)
    blueprint_id: str | None = None
    asset_ids: list[str] = Field(default_factory=list)


class ProjectPatch(_Model):
    name: str | None = None
    blueprint_id: str | None = None
    add_asset_ids: list[str] = Field(default_factory=list)
    remove_asset_ids: list[str] = Field(default_factory=list)


class ProjectOut(_Model):
    id: str
    name: str
    state: str
    blueprint_id: str | None
    asset_count: int
    coverage: float | None
    created_at: datetime
    updated_at: datetime


class CoverageGap(_Model):
    """A specific, actionable statement -- never a generic warning.

    'You need a shot with strong right-to-left motion' sends a user out to
    shoot for ten minutes. 'Insufficient footage' churns them.
    """

    slots: list[int]
    severity: Literal["minor", "moderate", "major"]
    message: str
    suggested_action: Literal["shoot", "upload", "substitute", "accept"]
    fallback: str | None = None


class CoverageReport(_Model):
    overall: float
    verdict: Literal["good", "degraded", "insufficient"]
    per_slot: list[dict[str, Any]]
    gaps: list[CoverageGap]
    can_render: bool
    requires_acknowledgement: bool


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


class AssignmentChange(_Model):
    slot: int = Field(ge=0)
    segment_id: str
    in_ms: int | None = Field(default=None, ge=0)
    out_ms: int | None = Field(default=None, ge=0)
    locked: bool = False


class AssignmentPatch(_Model):
    changes: list[AssignmentChange] = Field(min_length=1)


class AssignmentOut(_Model):
    assignment_id: str
    changed_slots: list[int]
    recomputed_slots: list[int]
    dirty_ranges: list[tuple[int, int]]
    overall_confidence: float


class Alternative(_Model):
    segment_id: str
    score: float
    rank: int
    reason: str
    breakdown: dict[str, float] = Field(
        default_factory=dict,
        description="Per-term fit scores. Exposed deliberately: a user who can see "
                    "why we ranked something makes a better-informed correction, "
                    "which is a better training label.",
    )


class AlternativesOut(_Model):
    slot: dict[str, Any]
    alternatives: list[Alternative]


# ---------------------------------------------------------------------------
# renders
# ---------------------------------------------------------------------------


class RenderCreate(_Model):
    project_id: str
    preset: Literal["preview", "1080p", "4k", "master", "project_file"] = "preview"
    acknowledge_degradation: bool = False
    webhook_url: HttpUrl | None = None
    only_slots: list[int] | None = Field(
        default=None,
        description="Partial re-render. Supply dirty_ranges from an assignment PATCH.",
    )


class RenderOut(_Model):
    id: str
    status: Literal["queued", "rendering", "complete", "failed"]
    cache_hit: bool = False
    queue_position: int | None = None
    estimated_ready_in_ms: int | None = None
    download_url: str | None = None
    duration_ms: int | None = None
    bytes: int | None = None
    degradation: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# music
# ---------------------------------------------------------------------------


class MusicMatchRequest(_Model):
    blueprint_id: str
    limit: int = Field(default=6, ge=1, le=25)
    mood: list[str] = Field(default_factory=list)
    max_bpm_delta: float = Field(default=6.0, ge=0, le=40)


class MusicTrackOut(_Model):
    id: str
    title: str
    artist: str | None
    bpm: float
    duration_ms: int
    match_score: float
    structure_alignment: float
    preview_url: str
    licence_terms_summary: str


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------


class UsageOut(_Model):
    period_start: datetime
    period_end: datetime
    renders_used: int
    renders_quota: int
    gpu_seconds_used: int
    storage_gb: float
    estimated_cost_usd: float
