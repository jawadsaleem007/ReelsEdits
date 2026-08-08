"""ReelsEdits API.

Real handlers wired to the real pipeline. Uploads land in storage, jobs run off
the request path, progress streams over SSE, and renders come back as playable
files.

Two shapes are preserved from docs/12 even though the local backend does not
strictly need them, because retrofitting either is expensive:

* **Media bytes do not pass through handler code.** Uploads go to a dedicated
  endpoint that streams to storage; everything else exchanges keys and URLs.
* **Long operations return a job immediately.** Nothing blocks a request on
  video work.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session

from . import errors
from .config import Settings, get_settings
from .db import (
    Asset,
    Blueprint,
    Job,
    Org,
    Project,
    ProjectAsset,
    Render,
    Segment,
    SwapEvent,
    get_session,
    init_engine,
    session_factory,
)
from .deps import RENDER_QUOTAS, Principal, require_principal
from .jobs import get_runner, init_runner
from .matching import (
    COVERAGE_FLOOR,
    alternatives_for_slot,
    compute_coverage,
)
from .storage import content_key, get_storage, init_storage

log = logging.getLogger("reelsedits.api")

SUPPORTED_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logging.basicConfig(level=s.log_level,
                        format="%(levelname)s %(name)s: %(message)s")

    init_engine(s.database_url)
    init_storage("local", root=Path(s.storage_root))
    init_runner(session_factory(), max_workers=s.worker_concurrency)

    # A default org so the app is usable immediately. Real auth replaces this;
    # the tenancy boundary already exists so that swap is additive.
    db = session_factory()()
    if db.get(Org, "default") is None:
        db.add(Org(id="default", name="Personal", plan="pro",
                   renders_used_period=0))
        db.commit()
    db.close()

    log.info("ReelsEdits API ready — db=%s storage=%s", s.database_url, s.storage_root)
    yield


app = FastAPI(
    title="ReelsEdits API",
    version="0.2.0",
    description="Style transfer for video editing.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id", "X-Quota-Renders-Remaining"],
)
app.add_exception_handler(errors.ProblemDetail, errors.problem_handler)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    response: Response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    response.headers["X-Response-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    return response


SettingsDep = Annotated[Settings, Depends(get_settings)]
PrincipalDep = Annotated[Principal, Depends(require_principal)]
DbDep = Annotated[Session, Depends(get_session)]
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]


def _owned(db: Session, model, obj_id: str, principal: Principal):
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "org_id", principal.org_id) != principal.org_id:
        raise errors.ProblemDetail(
            type_="not-found", title=f"{model.__name__} not found",
            status=404, detail=f"No {model.__name__.lower()} with id {obj_id}.",
            fix="Check the id, or list the resources to find the right one.",
        )
    return obj


def _enqueue(db: Session, principal: Principal, kind: str, *, project_id: str | None = None,
             input_: dict[str, Any], idempotency_key: str | None = None) -> Job:
    if idempotency_key:
        existing = (
            db.query(Job)
            .filter(Job.org_id == principal.org_id,
                    Job.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            return existing

    job = Job(org_id=principal.org_id, project_id=project_id, kind=kind,
              input=input_, idempotency_key=idempotency_key)
    db.add(job)
    db.commit()
    get_runner().submit(job.id)
    return job


def _job_payload(job: Job) -> dict[str, Any]:
    return {
        "id": job.id, "kind": job.kind, "state": job.state,
        "stage": job.stage, "progress": job.progress, "detail": job.detail,
        "output": job.output, "error": job.error_detail,
    }


# ===========================================================================
# ops
# ===========================================================================


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
async def readyz(db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    db.query(Org).count()
    from reelsedits_analyzer.fusion import ANALYZER_VERSION
    from reelsedits_analyzer.semantic import get_backend
    from reelsedits_renderer.ffmpeg_render import RENDERER_VERSION

    backend = get_backend()
    return {
        "status": "ok",
        "versions": {"analyzer": ANALYZER_VERSION, "renderer": RENDERER_VERSION,
                     "matcher": settings.matcher_version},
        "semantic": {
            "backend": backend.name,
            # Without a trustworthy backend every subject_class is ANY, the
            # SUBJECT_BRIDGES table never fires, and matching runs on shot
            # scale, motion and quality alone. Say so plainly rather than
            # leaving users to wonder why a good clip was ignored.
            "cross_domain_matching": backend.produces_trustworthy_subjects,
        },
    }


# ===========================================================================
# assets
# ===========================================================================


@app.post("/v1/assets", status_code=201, tags=["assets"])
async def upload_asset(
    db: DbDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    file: UploadFile = File(...),
    kind: str = Form("clip"),
    project_id: str | None = Form(None),
) -> dict[str, Any]:
    """Upload a video.

    Streams to storage in chunks rather than reading into memory — a 4GB clip
    must not become a 4GB resident buffer.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise errors.unsupported_media(codec=None, container=suffix or "unknown")

    asset = Asset(org_id=principal.org_id, kind=kind, filename=file.filename or "upload.mp4",
                  status="uploading")
    db.add(asset)
    db.flush()

    key = content_key(principal.org_id, asset.id, asset.filename)
    tmp = Path("/tmp") / f"upload-{asset.id}{suffix}"
    written = 0
    try:
        with tmp.open("wb") as out:
            while chunk := await file.read(1 << 20):
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise errors.ProblemDetail(
                        type_="payload-too-large", title="File too large", status=413,
                        detail=f"{file.filename} exceeds "
                               f"{settings.max_upload_bytes / 1e9:.1f}GB.",
                        fix="Trim or compress the clip before uploading.",
                    )
                out.write(chunk)

        get_storage().put(key, tmp)
        asset.storage_key = key
        asset.bytes = written
        asset.status = "uploaded"

        if project_id:
            project = _owned(db, Project, project_id, principal)
            db.add(ProjectAsset(project_id=project.id, asset_id=asset.id, role=kind))
        db.commit()
    finally:
        tmp.unlink(missing_ok=True)

    return {"id": asset.id, "filename": asset.filename, "bytes": asset.bytes,
            "kind": asset.kind, "status": asset.status}


@app.get("/v1/files/{key:path}", include_in_schema=False)
async def serve_file(key: str):
    """Serve stored objects. The S3 backend replaces this with presigned URLs."""
    try:
        path = get_storage().local_path(key)
    except (FileNotFoundError, ValueError):
        raise errors.ProblemDetail(
            type_="not-found", title="File not found", status=404,
            detail="No stored object with that key.", fix="Check the URL.",
        ) from None
    return FileResponse(path, media_type="video/mp4", filename=path.name)


# ===========================================================================
# projects
# ===========================================================================


@app.post("/v1/projects", status_code=201, tags=["projects"])
async def create_project(db: DbDep, principal: PrincipalDep,
                         body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    project = Project(
        org_id=principal.org_id,
        name=body.get("name") or "Untitled",
        music_strategy=body.get("music_strategy") or "platform_attach",
        sound_name=body.get("sound_name"),
        platform=body.get("platform") or "unknown",
    )
    db.add(project)
    db.commit()
    return {"id": project.id, "name": project.name, "state": project.state}


@app.get("/v1/projects", tags=["projects"])
async def list_projects(db: DbDep, principal: PrincipalDep) -> list[dict[str, Any]]:
    rows = (db.query(Project).filter(Project.org_id == principal.org_id)
            .order_by(Project.updated_at.desc()).limit(50).all())
    return [{"id": p.id, "name": p.name, "state": p.state, "coverage": p.coverage,
             "blueprint_id": p.blueprint_id,
             "updated_at": p.updated_at.isoformat()} for p in rows]


@app.get("/v1/projects/{project_id}", tags=["projects"])
async def get_project(project_id: str, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    p = _owned(db, Project, project_id, principal)
    assets = (db.query(Asset).join(ProjectAsset, ProjectAsset.asset_id == Asset.id)
              .filter(ProjectAsset.project_id == p.id).all())
    renders = (db.query(Render).filter(Render.project_id == p.id)
               .order_by(Render.created_at.desc()).limit(5).all())
    storage = get_storage()
    return {
        "id": p.id, "name": p.name, "state": p.state, "coverage": p.coverage,
        "blueprint_id": p.blueprint_id, "coverage_report": p.coverage_report,
        "music_strategy": p.music_strategy, "sound_name": p.sound_name,
        "assets": [{"id": a.id, "filename": a.filename, "kind": a.kind,
                    "status": a.status, "duration_ms": a.duration_ms,
                    "segments": len(a.segments)} for a in assets],
        "renders": [{"id": r.id, "preset": r.preset, "url": storage.url_for(r.storage_key),
                     "width": r.width, "height": r.height, "bytes": r.bytes,
                     "degradation": r.degradation,
                     "created_at": r.created_at.isoformat()} for r in renders],
    }


# ===========================================================================
# reference analysis
# ===========================================================================


@app.post("/v1/projects/{project_id}/reference", status_code=202, tags=["references"])
async def attach_reference(project_id: str, db: DbDep, principal: PrincipalDep,
                           body: dict[str, Any], idempotency_key: IdemKey = None) -> dict[str, Any]:
    """Analyse a reference into a blueprint. Returns a job immediately."""
    project = _owned(db, Project, project_id, principal)
    asset = _owned(db, Asset, body["asset_id"], principal)

    project.state = "reference_pending"
    db.commit()

    job = _enqueue(db, principal, "analyze", project_id=project.id,
                   input_={"asset_id": asset.id, "name": body.get("name")},
                   idempotency_key=idempotency_key)
    return {"job_id": job.id, "state": job.state,
            "events_url": f"/v1/jobs/{job.id}/events"}


@app.get("/v1/blueprints/{blueprint_id}/style-card", tags=["blueprints"])
async def style_card(blueprint_id: str, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    """The screen where the user decides whether to trust the system.

    Contains no frames from the reference — only the derived description
    (docs/01 §3.1).
    """
    row = db.get(Blueprint, blueprint_id)
    if row is None:
        raise errors.ProblemDetail(type_="not-found", title="Blueprint not found",
                                   status=404, detail="No blueprint with that id.",
                                   fix="Check the id.")
    from reelsedits_common import Blueprint as BlueprintModel

    bp = BlueprintModel.model_validate(row.doc)
    conf = bp.provenance.confidence.model_dump(exclude_none=True)
    return {
        "blueprint_id": row.id,
        "name": bp.name,
        "summary": bp.style.summary,
        "pacing": bp.style.pacing.model_dump(),
        "shot_scale_mix": bp.style.shot_scale_mix,
        "transition_mix": bp.style.transition_mix,
        "tags": bp.style.tags,
        "bpm": bp.audio.bpm,
        "duration_ms": bp.canvas.duration_ms,
        "slot_count": len(bp.slots),
        "sections": [{"kind": s.kind.value, "t_in_ms": s.t_in_ms,
                      "t_out_ms": s.t_out_ms, "energy": s.energy}
                     for s in bp.audio.sections],
        "confidence": conf,
        "low_confidence_subsystems": bp.low_confidence_subsystems(),
        "semantic_backend": bp.provenance.semantic_backend,
        "notes": bp.provenance.notes,
        "music": (bp.audio.music_binding.model_dump(mode="json", exclude_none=True)
                  if bp.audio.music_binding else None),
    }


# ===========================================================================
# indexing & coverage
# ===========================================================================


@app.post("/v1/projects/{project_id}/index", status_code=202, tags=["projects"])
async def index_project(project_id: str, db: DbDep, principal: PrincipalDep,
                        idempotency_key: IdemKey = None) -> dict[str, Any]:
    project = _owned(db, Project, project_id, principal)
    asset_ids = [
        r.asset_id for r in db.query(ProjectAsset)
        .filter(ProjectAsset.project_id == project.id).all()
    ]
    clips = [a.id for a in db.query(Asset).filter(
        Asset.id.in_(asset_ids), Asset.kind == "clip").all()] if asset_ids else []

    if not clips:
        raise errors.ProblemDetail(
            type_="no-footage", title="No footage to index", status=400,
            detail="This project has no clips attached.",
            fix="POST /v1/assets with kind=clip and this project_id first.",
        )

    project.state = "indexing"
    db.commit()
    job = _enqueue(db, principal, "index", project_id=project.id,
                   input_={"asset_ids": clips}, idempotency_key=idempotency_key)
    return {"job_id": job.id, "clips": len(clips),
            "events_url": f"/v1/jobs/{job.id}/events"}


@app.get("/v1/projects/{project_id}/coverage", tags=["projects"])
async def coverage(project_id: str, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    project = _owned(db, Project, project_id, principal)
    if not project.blueprint_id:
        raise errors.ProblemDetail(
            type_="no-blueprint", title="No reference attached", status=400,
            detail="Coverage needs a blueprint to measure against.",
            fix="Attach and analyse a reference first.",
        )
    report = compute_coverage(db, project)
    project.coverage = report["overall"]
    project.coverage_report = report
    db.commit()
    return report


# ===========================================================================
# matching
# ===========================================================================


@app.get("/v1/projects/{project_id}/slots/{slot_index}/alternatives", tags=["matching"])
async def alternatives(project_id: str, slot_index: int, db: DbDep,
                       principal: PrincipalDep, limit: int = 6) -> dict[str, Any]:
    project = _owned(db, Project, project_id, principal)
    try:
        return alternatives_for_slot(db, project, slot_index, limit=limit)
    except ValueError as exc:
        raise errors.ProblemDetail(type_="invalid-slot", title="Invalid slot",
                                   status=400, detail=str(exc),
                                   fix="Use a slot index from the blueprint.") from exc


@app.patch("/v1/projects/{project_id}/assignment", tags=["matching"])
async def swap_clip(project_id: str, db: DbDep, principal: PrincipalDep,
                    body: dict[str, Any]) -> dict[str, Any]:
    """Swap the clip assigned to a slot.

    The swap is logged as a preference pair. That log is the matcher's training
    signal and the thing competitors cannot buy (docs/09 §6), so it is written
    here rather than added later — every week without it is data lost forever.
    """
    project = _owned(db, Project, project_id, principal)
    if not project.bound_doc:
        raise errors.ProblemDetail(
            type_="not-bound", title="Nothing to swap yet", status=400,
            detail="This project has not been matched or rendered yet.",
            fix="Render a preview first, then swap clips on the result.",
        )

    slot_index = int(body["slot"])
    chosen_id = body["segment_id"]

    ranked = alternatives_for_slot(db, project, slot_index, limit=100)
    by_id = {a["segment_id"]: a for a in ranked["alternatives"]}
    chosen = by_id.get(chosen_id)
    if chosen is None:
        raise errors.ProblemDetail(
            type_="invalid-segment", title="Segment cannot fill this slot", status=400,
            detail=f"{chosen_id} does not satisfy slot {slot_index}'s requirements.",
            fix="Choose from GET /v1/projects/{id}/slots/{i}/alternatives.",
        )

    doc = dict(project.bound_doc)
    slots = [dict(s) for s in doc["slots"]]
    previous = slots[slot_index].get("assignment") or {}

    db.add(SwapEvent(
        org_id=principal.org_id, project_id=project.id,
        blueprint_id=project.blueprint_id, slot_index=slot_index,
        slot_features=ranked["slot"]["requirements"],
        rejected_segment_id=previous.get("segment_id"),
        rejected_score=previous.get("score", 0.0),
        chosen_segment_id=chosen_id,
        chosen_score=chosen["score"],
        chosen_rank=chosen["rank"],
    ))

    slots[slot_index]["assignment"] = {
        "segment_id": chosen_id, "in_ms": chosen["in_ms"], "out_ms": chosen["out_ms"],
        "score": chosen["score"], "reason": chosen["reason"], "locked": True,
    }
    doc["slots"] = slots
    project.bound_doc = doc
    db.commit()

    # Only the swapped slot and its transition neighbours need re-rendering.
    dirty = [max(0, slot_index - 1), min(len(slots) - 1, slot_index + 1)]
    return {"slot": slot_index, "segment_id": chosen_id, "rank": chosen["rank"],
            "dirty_range": dirty, "reason": chosen["reason"]}


# ===========================================================================
# render
# ===========================================================================


@app.post("/v1/projects/{project_id}/render", status_code=202, tags=["renders"])
async def create_render(project_id: str, db: DbDep, principal: PrincipalDep,
                        body: dict[str, Any] | None = None,
                        idempotency_key: IdemKey = None) -> dict[str, Any]:
    body = body or {}
    project = _owned(db, Project, project_id, principal)

    if not project.blueprint_id:
        raise errors.ProblemDetail(
            type_="no-blueprint", title="No reference attached", status=400,
            detail="Nothing to render against.",
            fix="Attach and analyse a reference first.",
        )

    # Quota is checked and incremented in one transaction so concurrent
    # requests cannot race past the limit.
    org = db.get(Org, principal.org_id)
    quota = RENDER_QUOTAS.get(org.plan, 3)
    if org.renders_used_period >= quota:
        raise errors.quota_exceeded(org.renders_used_period, quota,
                                    org.period_start.isoformat())

    report = compute_coverage(db, project)
    if report["overall"] < COVERAGE_FLOOR and not body.get("acknowledge_degradation"):
        raise errors.insufficient_coverage(report["overall"], COVERAGE_FLOOR,
                                           report["gaps"])

    org.renders_used_period += 1
    project.state = "rendering"
    db.commit()

    job = _enqueue(db, principal, "render", project_id=project.id,
                   input_={"preset": body.get("preset", "preview"),
                           "force": bool(body.get("acknowledge_degradation"))},
                   idempotency_key=idempotency_key)
    return {"job_id": job.id, "state": job.state,
            "quota_remaining": max(0, quota - org.renders_used_period),
            "events_url": f"/v1/jobs/{job.id}/events"}


@app.get("/v1/renders/{render_id}", tags=["renders"])
async def get_render(render_id: str, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    r = _owned(db, Render, render_id, principal)
    return {"id": r.id, "preset": r.preset, "url": get_storage().url_for(r.storage_key),
            "width": r.width, "height": r.height, "duration_ms": r.duration_ms,
            "bytes": r.bytes, "degradation": r.degradation,
            "assignment": r.assignment, "gpu_seconds": r.gpu_seconds}


# ===========================================================================
# jobs
# ===========================================================================


@app.get("/v1/jobs/{job_id}", tags=["jobs"])
async def get_job(job_id: str, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    return _job_payload(_owned(db, Job, job_id, principal))


@app.get("/v1/jobs/{job_id}/events", tags=["jobs"])
async def job_events(job_id: str, db: DbDep, principal: PrincipalDep):
    """SSE progress.

    `detail` carries a human-readable stage description, not just a percentage.
    During a 60-second wait "Tracking the beat" reads as real work; a bar reads
    as a hang.
    """
    job = _owned(db, Job, job_id, principal)
    runner = get_runner()

    def stream():
        # Replay current state first so a client that connects late is not
        # stuck on an empty stream until the next transition.
        yield f"event: stage\ndata: {json.dumps(_job_payload(job))}\n\n"
        if job.state in ("complete", "failed"):
            yield f"event: {job.state}\ndata: {json.dumps(_job_payload(job))}\n\n"
            return

        q = runner.subscribe(job_id)
        try:
            deadline = time.time() + 600
            while time.time() < deadline:
                try:
                    event = q.get(timeout=15)
                except Exception:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: {event.get('type', 'stage')}\ndata: {json.dumps(event)}\n\n"
                if event.get("type") in ("complete", "failed"):
                    return
        finally:
            runner.unsubscribe(job_id, q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ===========================================================================
# usage
# ===========================================================================


@app.get("/v1/usage", tags=["billing"])
async def usage(db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    org = db.get(Org, principal.org_id)
    quota = RENDER_QUOTAS.get(org.plan, 3)
    swaps = db.query(SwapEvent).filter(SwapEvent.org_id == org.id).count()
    return {
        "plan": org.plan,
        "renders_used": org.renders_used_period,
        "renders_quota": quota,
        "period_start": org.period_start.isoformat(),
        "projects": db.query(Project).filter(Project.org_id == org.id).count(),
        "segments": db.query(Segment).filter(Segment.org_id == org.id).count(),
        # Surfaced because it is the number that compounds: every swap is a
        # labelled preference pair for the matcher (docs/09 §6).
        "swap_events": swaps,
    }


# ===========================================================================
# web app
# ===========================================================================


@app.get("/", include_in_schema=False)
async def index() -> HTMLResponse:
    ui = Path(__file__).parent / "static" / "index.html"
    if ui.exists():
        return HTMLResponse(ui.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>ReelsEdits API</h1><p>See <a href='/docs'>/docs</a>.</p>")
