"""ReelsEdits API.

Stateless FastAPI. Never touches a GPU, never blocks on video work, never
handles media bytes -- uploads go direct to S3 via presigned URLs. Target p99
under 120ms on every route.

Handlers below carry the real request/response contracts and the real control
flow (idempotency, quota, policy gates). The datastore and workflow calls are
marked TODO and stubbed; see docs/20-implementation-plan.md for the order in
which they get filled in.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import errors
from .config import Settings, get_settings
from .deps import Principal, require_principal
from .schemas import (
    AlternativesOut,
    AssetBatchCreate,
    AssetOut,
    AssignmentOut,
    AssignmentPatch,
    CoverageReport,
    MultipartComplete,
    MusicMatchRequest,
    MusicTrackOut,
    ProjectCreate,
    ProjectOut,
    ProjectPatch,
    ReferenceCreate,
    ReferenceOut,
    RenderCreate,
    RenderOut,
    StyleCard,
    UsageOut,
)

log = logging.getLogger("reelsedits.api")

COVERAGE_FLOOR = 0.55
COVERAGE_GOOD = 0.85


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    log.info("starting %s in %s", settings.service_name, settings.environment)
    # TODO: open DB pool, Redis, Temporal client, S3 client
    yield
    log.info("shutting down")


app = FastAPI(
    title="ReelsEdits API",
    version="1.0.0",
    description=(
        "Style transfer for video editing. Submit a reference, upload footage, "
        "get an edit. See https://github.com/jawadsaleem007/ReelsEdits/blob/main/docs/12-api-design.md"
    ),
    openapi_url="/openapi.json",
    docs_url="/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining",
                    "X-Quota-Renders-Remaining", "X-Request-Id",
                    "Idempotency-Replayed"],
)
app.add_exception_handler(errors.ProblemDetail, errors.problem_handler)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a request id and record latency on every response."""
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    response: Response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    return response


SettingsDep = Annotated[Settings, Depends(get_settings)]
PrincipalDep = Annotated[Principal, Depends(require_principal)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


# ===========================================================================
# health
# ===========================================================================


@app.get("/healthz", tags=["ops"], include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"], include_in_schema=False)
async def readyz(settings: SettingsDep) -> dict[str, object]:
    # TODO: probe DB, Redis, Temporal
    return {
        "status": "ok",
        "versions": {
            "analyzer": settings.analyzer_version,
            "indexer": settings.indexer_version,
            "matcher": settings.matcher_version,
            "renderer": settings.renderer_version,
        },
    }


# ===========================================================================
# references
# ===========================================================================


@app.post("/v1/references", response_model=ReferenceOut, status_code=201, tags=["references"])
async def create_reference(
    body: ReferenceCreate,
    principal: PrincipalDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKey = None,
) -> ReferenceOut:
    """Submit a reference video for analysis.

    Two paths with different legal weight. Uploaded files are the user's own.
    URL fetching is policy-gated per domain and produces an *ephemeral* asset
    hard-deleted within 24 hours -- see docs/18 section 7.
    """
    if body.source_url:
        domain = body.source_url.host or ""
        if domain not in settings.fetchable_domains:
            raise errors.source_not_fetchable(domain)

    # TODO: idempotency lookup; fingerprint; blueprint cache lookup;
    #       start Temporal AnalyzeReference workflow
    ref_id = f"ref_{uuid.uuid4().hex[:12]}"
    cache_hit = False

    if cache_hit:
        return ReferenceOut(
            id=ref_id, status="ready", cache_hit=True,
            estimated_ready_in_ms=0, blueprint_id="bp_...",
        )
    return ReferenceOut(
        id=ref_id, status="analyzing", cache_hit=False,
        estimated_ready_in_ms=52_000,
        events_url=f"/v1/references/{ref_id}/events",
    )


@app.get("/v1/references/{reference_id}", response_model=ReferenceOut, tags=["references"])
async def get_reference(reference_id: str, principal: PrincipalDep) -> ReferenceOut:
    raise NotImplementedError  # TODO


@app.get("/v1/references/{reference_id}/events", tags=["references"])
async def reference_events(reference_id: str, principal: PrincipalDep) -> StreamingResponse:
    """SSE analysis progress.

    ``detail`` carries a human-readable stage description, not just a
    percentage. During a 60-second wait, "Extracting colour grade" reads as
    real work; a bar reads as a hang.
    """

    async def stream():
        # TODO: subscribe to the Redis pubsub fanout for this reference
        yield 'event: stage\ndata: {"stage":"probing","progress":0.05}\n\n'

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/v1/blueprints/{blueprint_id}/style-card", response_model=StyleCard, tags=["blueprints"])
async def style_card(blueprint_id: str, principal: PrincipalDep) -> StyleCard:
    """The style card. Contains no frames from the reference -- only the
    derived description. docs/01 section 3.1."""
    raise NotImplementedError  # TODO


# ===========================================================================
# assets
# ===========================================================================


@app.post("/v1/assets/batch", response_model=list[AssetOut], status_code=201, tags=["assets"])
async def create_assets(
    body: AssetBatchCreate,
    principal: PrincipalDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKey = None,
) -> list[AssetOut]:
    """Register assets and return presigned upload URLs.

    Bytes never transit the API. Routing video through this tier would make it
    the bottleneck at roughly 200 concurrent users.
    """
    out: list[AssetOut] = []
    for spec in body.assets:
        if spec.bytes > settings.max_upload_bytes:
            raise errors.ProblemDetail(
                type_="payload-too-large",
                title="File too large",
                status=413,
                detail=f"{spec.filename} is {spec.bytes} bytes; limit is "
                       f"{settings.max_upload_bytes}.",
                fix="Compress or trim the clip before uploading.",
            )
        # TODO: sha256 dedupe within org; presign multipart
        out.append(AssetOut(id=f"ast_{uuid.uuid4().hex[:12]}", kind=spec.kind,
                            status="uploading", dedupe_hit=False))
    return out


@app.post("/v1/assets/{asset_id}/complete", response_model=AssetOut, tags=["assets"])
async def complete_asset(
    asset_id: str, body: MultipartComplete, principal: PrincipalDep
) -> AssetOut:
    """Finalise the multipart upload and start indexing."""
    raise NotImplementedError  # TODO


# ===========================================================================
# projects
# ===========================================================================


@app.post("/v1/projects", response_model=ProjectOut, status_code=201, tags=["projects"])
async def create_project(body: ProjectCreate, principal: PrincipalDep) -> ProjectOut:
    raise NotImplementedError  # TODO


@app.patch("/v1/projects/{project_id}", response_model=ProjectOut, tags=["projects"])
async def patch_project(
    project_id: str, body: ProjectPatch, principal: PrincipalDep
) -> ProjectOut:
    raise NotImplementedError  # TODO


@app.get("/v1/projects/{project_id}/coverage", response_model=CoverageReport, tags=["projects"])
async def coverage(project_id: str, principal: PrincipalDep) -> CoverageReport:
    """Coverage report with specific, actionable gaps.

    Runs before any render is committed. A user who waits 90 seconds for a bad
    render has been robbed twice. docs/08 section 9.
    """
    raise NotImplementedError  # TODO


# ===========================================================================
# matching
# ===========================================================================


@app.patch("/v1/projects/{project_id}/assignment", response_model=AssignmentOut, tags=["matching"])
async def patch_assignment(
    project_id: str, body: AssignmentPatch, principal: PrincipalDep
) -> AssignmentOut:
    """Swap or lock a slot's assignment.

    Two things happen server-side and both matter more than the response:

    1. The swap is logged to ClickHouse as a preference pair. This is the
       matcher's training signal and the thing competitors cannot buy.
       docs/09 section 6.
    2. Neighbouring slots are recomputed, because the pairwise sequence terms
       changed. ``locked`` pins the user's choice so the matcher works around
       it rather than optimising it away.
    """
    # TODO: apply changes; log swap events; re-run matcher on the neighbourhood
    changed = [c.slot for c in body.changes]
    recomputed = sorted({s + d for s in changed for d in (-1, 1) if s + d >= 0})
    return AssignmentOut(
        assignment_id=f"asn_{uuid.uuid4().hex[:8]}",
        changed_slots=changed,
        recomputed_slots=recomputed,
        dirty_ranges=[(min(changed) - 1, max(changed) + 2)],
        overall_confidence=0.81,
    )


@app.get(
    "/v1/projects/{project_id}/slots/{slot_index}/alternatives",
    response_model=AlternativesOut, tags=["matching"],
)
async def alternatives(
    project_id: str, slot_index: int, principal: PrincipalDep, limit: int = 5
) -> AlternativesOut:
    """Ranked alternatives with per-term score breakdowns."""
    raise NotImplementedError  # TODO


# ===========================================================================
# renders
# ===========================================================================


@app.post("/v1/renders", response_model=RenderOut, status_code=202, tags=["renders"])
async def create_render(
    body: RenderCreate,
    principal: PrincipalDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKey = None,
) -> RenderOut:
    """Queue a preview or export render.

    Refuses to render below the coverage floor without explicit
    acknowledgement. Silent degradation is the worst output this system can
    produce -- the user ships it and blames their own eye.
    """
    # TODO: load project; check quota transactionally; compute coverage
    coverage_value = 0.78
    if coverage_value < COVERAGE_FLOOR and not body.acknowledge_degradation:
        raise errors.insufficient_coverage(coverage_value, COVERAGE_FLOOR, gaps=[])

    # TODO: verify music licence is resolved -- hard constraint, docs/06 s3.1
    # TODO: render cache lookup on
    #       sha256(blueprint || assignment || asset_ids || renderer_version)
    return RenderOut(
        id=f"rnd_{uuid.uuid4().hex[:12]}",
        status="queued", cache_hit=False,
        queue_position=3, estimated_ready_in_ms=61_000,
    )


@app.get("/v1/renders/{render_id}", response_model=RenderOut, tags=["renders"])
async def get_render(render_id: str, principal: PrincipalDep) -> RenderOut:
    raise NotImplementedError  # TODO


# ===========================================================================
# music
# ===========================================================================


@app.post("/v1/music/match", response_model=list[MusicTrackOut], tags=["music"])
async def match_music(body: MusicMatchRequest, principal: PrincipalDep) -> list[MusicTrackOut]:
    """Find licensed tracks matching a blueprint's rhythmic skeleton.

    Ranks by BPM proximity, section-layout alignment and energy-curve shape --
    the catalogue is analysed with the same pipeline as references, so the
    comparison is apples-to-apples. docs/07 section 6.3.
    """
    raise NotImplementedError  # TODO


# ===========================================================================
# usage
# ===========================================================================


@app.get("/v1/usage", response_model=UsageOut, tags=["billing"])
async def usage(principal: PrincipalDep) -> UsageOut:
    raise NotImplementedError  # TODO
