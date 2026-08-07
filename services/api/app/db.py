"""Persistence.

SQLAlchemy 2.0 models mirroring docs/11-database-schema.md. Runs on SQLite for
local development and Postgres in production — the column types are chosen to be
portable, and anything Postgres-specific (pgvector, RLS, partitioning) lives in
migrations rather than here.

Vectors are stored as JSON in the portable path. That is fine at the scale where
you are running SQLite, and the query that needs real ANN is already isolated in
one place (see `docs/11 §6`), so swapping to pgvector/Qdrant is a repository
change rather than a model change.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    # SQLAlchemy's documented API for this is a class-level mapping.
    type_annotation_map: ClassVar[dict] = {dict[str, Any]: JSON, list[Any]: JSON}


# ---------------------------------------------------------------------------
# tenancy
# ---------------------------------------------------------------------------


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), default="Personal")
    plan: Mapped[str] = mapped_column(String(20), default="free")

    # Denormalised quota counters, incremented transactionally at job creation
    # so a burst of concurrent requests cannot race past the limit.
    renders_used_period: Mapped[int] = mapped_column(Integer, default=0)
    gpu_seconds_used_period: Mapped[float] = mapped_column(Float, default=0.0)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    api_key: Mapped[str] = mapped_column(String(64), unique=True, default=lambda: f"sk_{uuid.uuid4().hex}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------------------
# assets
# ---------------------------------------------------------------------------


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="clip")
    status: Mapped[str] = mapped_column(String(20), default="pending")

    filename: Mapped[str] = mapped_column(String(500))
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # MediaProfile
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(40), nullable=True)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)

    #: URL-fetched references are hard-deleted within 24h. A legal commitment,
    #: so it gets an indexed column rather than a background heuristic.
    retention_class: Mapped[str] = mapped_column(String(20), default="standard")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    segments: Mapped[list[Segment]] = relationship(back_populates="asset", cascade="all, delete-orphan")


class Segment(Base):
    """A usable sub-shot. The matcher's unit of work, not whole files."""

    __tablename__ = "segments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)

    t_in_ms: Mapped[int] = mapped_column(Integer)
    t_out_ms: Mapped[int] = mapped_column(Integer)
    usable_in_ms: Mapped[int] = mapped_column(Integer)
    usable_out_ms: Mapped[int] = mapped_column(Integer)

    shot_scale: Mapped[str] = mapped_column(String(20))
    camera_motion: Mapped[str] = mapped_column(String(20))
    camera_height: Mapped[str] = mapped_column(String(20), default="any")
    subject_class: Mapped[str] = mapped_column(String(30), default="any")
    composition: Mapped[str] = mapped_column(String(30), default="any")

    motion_energy: Mapped[float] = mapped_column(Float, default=0.5)
    motion_direction_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[float] = mapped_column(Float, default=0.7)
    mean_luma: Mapped[float] = mapped_column(Float, default=0.5)
    camera_angle_deg: Mapped[float] = mapped_column(Float, default=0.0)
    has_face: Mapped[bool] = mapped_column(Boolean, default=False)
    has_speech: Mapped[bool] = mapped_column(Boolean, default=False)

    #: JSON in the portable path; pgvector column in production.
    semantic_vec: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    asset: Mapped[Asset] = relationship(back_populates="segments")


Index("ix_segments_match", Segment.org_id, Segment.shot_scale, Segment.quality)


# ---------------------------------------------------------------------------
# references & blueprints
# ---------------------------------------------------------------------------


class Reference(Base):
    __tablename__ = "references"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)

    #: Perceptual, not byte-level — the same video at a different bitrate must
    #: hit the same cache entry or the cost model collapses (docs/08 §1).
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    analyzer_version: Mapped[str] = mapped_column(String(20))
    blueprint_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("fingerprint", "analyzer_version", name="uq_reference_cache"),
    )


class Blueprint(Base):
    """Immutable and versioned. A user edit creates a new row with parent_id set.

    Never UPDATE, never DELETE: blueprints are the corpus (docs/02 §4), they are
    kilobytes, and deleting one destroys training data to save nothing.
    """

    __tablename__ = "blueprints"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    reference_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("blueprints.id"), nullable=True)

    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    doc: Mapped[dict[str, Any]] = mapped_column(JSON)
    doc_sha256: Mapped[str] = mapped_column(String(64))

    # Denormalised so listing and ranking do not parse the JSON.
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    slot_count: Mapped[int] = mapped_column(Integer, default=0)
    cut_count: Mapped[int] = mapped_column(Integer, default=0)
    bpm: Mapped[float] = mapped_column(Float, default=0.0)
    cuts_per_second: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_overall: Mapped[float] = mapped_column(Float, default=0.0)

    visibility: Mapped[str] = mapped_column(String(20), default="private")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------------------
# projects, jobs, renders
# ---------------------------------------------------------------------------


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Untitled")
    state: Mapped[str] = mapped_column(String(30), default="draft")

    blueprint_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Blueprint bound to this project's footage. Kept separate from the free
    #: blueprint so the style stays portable (docs/06 §1.2).
    bound_doc: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    music_strategy: Mapped[str] = mapped_column(String(30), default="platform_attach")
    sound_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    platform: Mapped[str] = mapped_column(String(20), default="unknown")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class ProjectAsset(Base):
    __tablename__ = "project_assets"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), default="clip")


class Job(Base):
    """Durable job state.

    Lives in the database rather than the queue, because queues lose messages,
    get replayed, and cannot answer "what is the state of job X". The queue is a
    dispatch mechanism; this table is the source of truth (docs/11 §1).
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    reference_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=1)

    stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: Per-job cost ledger. Makes margin-per-job a query rather than a
    #: spreadsheet exercise (docs/14 §8).
    cost_ledger: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("org_id", "idempotency_key", name="uq_job_idempotency"),
    )


class Render(Base):
    __tablename__ = "renders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    preset: Mapped[str] = mapped_column(String(20), default="preview")
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    bytes: Mapped[int] = mapped_column(Integer, default=0)

    #: Determinism contract: equal keys imply byte-identical output, so an
    #: unchanged blueprint + assignment + assets returns the cached render.
    render_cache_key: Mapped[str] = mapped_column(String(64), index=True)
    renderer_version: Mapped[str] = mapped_column(String(20))

    assignment: Mapped[list[Any]] = mapped_column(JSON, default=list)
    degradation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    gpu_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SwapEvent(Base):
    """A preference pair labelled by a domain expert at peak engagement.

    This table is the moat (docs/09 §6). In production it goes to ClickHouse;
    here it is Postgres/SQLite so the local app captures the signal from day one
    rather than adding it later and losing everything before that.
    """

    __tablename__ = "swap_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    blueprint_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    slot_index: Mapped[int] = mapped_column(Integer)
    slot_features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rejected_segment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejected_score: Mapped[float] = mapped_column(Float, default=0.0)
    chosen_segment_id: Mapped[str] = mapped_column(String(64))
    chosen_score: Mapped[float] = mapped_column(Float, default=0.0)
    #: Position of the user's pick in OUR ranking. 1 means we were right.
    #: A rising mean is a matcher regression, visible in one query.
    chosen_rank: Mapped[int] = mapped_column(Integer, default=0)
    matcher_version: Mapped[str] = mapped_column(String(20), default="0.9.1")


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------

_engine = None
_Session = None


def init_engine(url: str, echo: bool = False):
    """Create the engine and tables. Idempotent."""
    global _engine, _Session

    connect_args = {}
    if url.startswith("sqlite"):
        # The job runner writes from a worker thread while requests read from
        # the event loop; SQLite needs this to permit that.
        connect_args["check_same_thread"] = False

    _engine = create_engine(url, echo=echo, future=True, connect_args=connect_args)

    if url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _rec):
            cur = dbapi_conn.cursor()
            # WAL lets the worker write while requests read, instead of
            # serialising the whole app behind one lock.
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    Base.metadata.create_all(_engine)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def session_factory():
    if _Session is None:
        raise RuntimeError("database not initialised; call init_engine() first")
    return _Session


def get_session():
    """FastAPI dependency."""
    factory = session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()
