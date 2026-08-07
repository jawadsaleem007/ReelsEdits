"""Background job execution.

A thread-pool runner that executes the real analyzer, indexer, matcher and
renderer. Jobs are durable in Postgres/SQLite; this class only dispatches, so
swapping it for Redis Streams + Temporal (docs/03 §3.2) means replacing
`submit` and `_run` — the stage functions and the job table are unchanged.

Two properties are preserved deliberately, because retrofitting them is
expensive:

* **Progress is reported per stage, with a human-readable detail string.**
  During a 60-second wait, "Detecting shot boundaries" reads as real work; a
  spinner reads as a hang.
* **Every stage failure is recorded on the job row**, not just logged, so the
  UI can show what went wrong instead of a generic error.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import Asset, Blueprint, Job, Project, ProjectAsset, Reference, Render, Segment
from .storage import get_storage

log = logging.getLogger("reelsedits.jobs")


def _now():
    return datetime.now(timezone.utc)


class JobRunner:
    """Executes jobs off the request path."""

    def __init__(self, session_factory: Callable, max_workers: int = 2) -> None:
        #: A sessionmaker. Calling it yields a Session; the worker thread needs
        #: its own, separate from the request-scoped one.
        self.session_factory = session_factory
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="reelsedits")
        self._subscribers: dict[str, list] = {}
        self._lock = threading.Lock()

    # -- progress fan-out ---------------------------------------------------

    def subscribe(self, job_id: str):
        """Register a queue for SSE progress on one job."""
        import queue

        q: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q) -> None:
        with self._lock:
            subs = self._subscribers.get(job_id, [])
            if q in subs:
                subs.remove(q)
            if not subs:
                self._subscribers.pop(job_id, None)

    def _publish(self, job_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subscribers.get(job_id, []))
        for q in subs:
            # A slow or dead SSE consumer must never stall the job. Dropping an
            # event is correct here: the client polls /v1/jobs/{id} for truth.
            with contextlib.suppress(Exception):
                q.put_nowait(event)

    def _progress(self, db, job: Job, stage: str, progress: float, detail: str) -> None:
        job.stage = stage
        job.progress = round(progress, 3)
        job.detail = detail
        db.commit()
        self._publish(job.id, {"type": "stage", "stage": stage,
                               "progress": job.progress, "detail": detail})

    # -- dispatch -----------------------------------------------------------

    def submit(self, job_id: str) -> None:
        self.pool.submit(self._run, job_id)

    def _run(self, job_id: str) -> None:
        db = self.session_factory()
        job = db.get(Job, job_id)
        if job is None:
            db.close()
            return

        job.state = "running"
        job.started_at = _now()
        job.attempts += 1
        db.commit()

        started = time.perf_counter()
        try:
            handler = {
                "analyze": self._analyze,
                "index": self._index,
                "render": self._render,
            }[job.kind]
            output = handler(db, job)

            job.state = "complete"
            job.output = output
            job.progress = 1.0
            job.finished_at = _now()
            job.cost_ledger = {
                **(job.cost_ledger or {}),
                "wall_seconds": round(time.perf_counter() - started, 2),
            }
            db.commit()
            self._publish(job_id, {"type": "complete", **(output or {})})

        except Exception as exc:
            log.exception("job %s (%s) failed", job_id, job.kind)
            # Roll back FIRST. If the failure was a database error the session
            # is poisoned, and writing the failure state would raise again --
            # leaving the job stuck in 'running' forever with no error anywhere.
            # A job that fails invisibly is worse than one that fails loudly.
            try:
                db.rollback()
            except Exception:
                log.exception("rollback failed for job %s", job_id)

            try:
                job = db.get(Job, job_id)
                if job is not None:
                    job.state = "failed"
                    job.error_code = type(exc).__name__
                    # Truncated: enough to debug, not enough to leak internal
                    # paths into a user-visible field.
                    job.error_detail = f"{exc}"[:1000]
                    job.finished_at = _now()
                    db.commit()
            except Exception:
                log.exception("could not record failure for job %s", job_id)

            self._publish(job_id, {"type": "failed", "error": str(exc)[:300]})
        finally:
            db.close()

    # -- stages -------------------------------------------------------------

    def _analyze(self, db, job: Job) -> dict[str, Any]:
        """Reference video → Editing Blueprint, with cache lookup first."""
        from reelsedits_analyzer.audio import analyze_audio
        from reelsedits_analyzer.fusion import ANALYZER_VERSION, build_blueprint
        from reelsedits_analyzer.visual import analyze_visual
        from reelsedits_common.enums import MusicStrategy

        from .fingerprint import fingerprint_video

        storage = get_storage()
        asset = db.get(Asset, job.input["asset_id"])
        path = storage.local_path(asset.storage_key)

        self._progress(db, job, "fingerprint", 0.05, "Checking whether we've seen this before")
        fp = fingerprint_video(path, ANALYZER_VERSION)

        cached = (
            db.query(Reference)
            .filter(Reference.fingerprint == fp,
                    Reference.analyzer_version == ANALYZER_VERSION,
                    Reference.blueprint_id.isnot(None))
            .first()
        )
        if cached is not None:
            # A cache hit costs ~0.4% of a miss. This is the single most
            # important number in the cost model (docs/14 §6).
            #
            # The Reference row IS the cache index: one row per
            # (fingerprint, analyzer_version), shared across orgs and projects.
            # We reuse it rather than inserting a duplicate — which the unique
            # constraint would reject anyway. Sharing is legally clean because
            # a blueprint contains no media from either party (docs/18 §5).
            self._progress(db, job, "cached", 1.0, "Already analysed — reusing the blueprint")
            project = db.get(Project, job.project_id) if job.project_id else None
            if project is not None:
                project.blueprint_id = cached.blueprint_id
                project.state = "reference_ready"
                db.commit()
            return {"blueprint_id": cached.blueprint_id, "reference_id": cached.id,
                    "cache_hit": True}

        self._progress(db, job, "probing", 0.10, "Reading the video")
        visual = analyze_visual(path)

        self._progress(db, job, "structure", 0.30,
                       f"Found {len(visual.shots)} shots — measuring camera motion")

        self._progress(db, job, "audio", 0.55, "Tracking the beat and finding the structure")
        audio = analyze_audio(path, visual.profile.duration_ms)

        self._progress(db, job, "fusion", 0.80,
                       f"{audio.bpm:.0f} BPM — mapping cuts to the beat grid")
        project = db.get(Project, job.project_id) if job.project_id else None
        bp = build_blueprint(
            audio, visual,
            name=job.input.get("name") or asset.filename,
            music_strategy=MusicStrategy(
                (project.music_strategy if project else None) or "platform_attach"
            ),
            sound_name=project.sound_name if project else None,
            platform=(project.platform if project else "unknown"),
        )

        self._progress(db, job, "saving", 0.95, "Writing the blueprint")
        doc = bp.model_dump(mode="json", by_alias=True, exclude_none=True)
        import hashlib
        import json as _json

        row = Blueprint(
            id=bp.id.removeprefix("bp_")[:32],
            org_id=job.org_id,
            name=bp.name,
            doc=doc,
            doc_sha256=hashlib.sha256(
                _json.dumps(doc, sort_keys=True).encode()
            ).hexdigest(),
            duration_ms=bp.canvas.duration_ms,
            slot_count=len(bp.slots),
            cut_count=len(bp.cuts),
            bpm=bp.audio.bpm,
            cuts_per_second=bp.style.pacing.cuts_per_second,
            confidence_overall=bp.provenance.confidence.overall,
        )
        db.add(row)
        db.flush()

        ref = Reference(org_id=job.org_id, asset_id=asset.id, fingerprint=fp,
                        analyzer_version=ANALYZER_VERSION, blueprint_id=row.id,
                        source_url=job.input.get("source_url"))
        db.add(ref)

        if project is not None:
            project.blueprint_id = row.id
            project.state = "reference_ready"
        db.commit()

        return {"blueprint_id": row.id, "reference_id": ref.id, "cache_hit": False,
                "shots": len(visual.shots), "bpm": bp.audio.bpm}

    def _index(self, db, job: Job) -> dict[str, Any]:
        """User clips → Segments the matcher can work with."""
        from reelsedits_indexer.index import index_clip

        storage = get_storage()
        asset_ids: list[str] = job.input["asset_ids"]
        total = len(asset_ids)
        indexed, failed = 0, []

        for i, asset_id in enumerate(asset_ids):
            asset = db.get(Asset, asset_id)
            if asset is None:
                continue
            self._progress(db, job, "indexing", (i + 0.5) / max(total, 1),
                           f"Analysing {asset.filename} ({i + 1} of {total})")
            try:
                path = storage.local_path(asset.storage_key)
                result = index_clip(path, asset_id=asset.id)

                asset.width, asset.height = result.width, result.height
                asset.fps, asset.duration_ms = result.fps, result.duration_ms
                asset.status = "ready"

                # Re-indexing replaces prior segments rather than duplicating.
                db.query(Segment).filter(Segment.asset_id == asset.id).delete()
                for s in result.segments:
                    db.add(Segment(
                        id=s.id, asset_id=asset.id, org_id=job.org_id,
                        t_in_ms=s.t_in_ms, t_out_ms=s.t_out_ms,
                        usable_in_ms=s.usable_in_ms, usable_out_ms=s.usable_out_ms,
                        shot_scale=s.shot_scale.value, camera_motion=s.camera_motion.value,
                        camera_height=s.camera_height.value,
                        subject_class=s.subject_class.value,
                        composition=s.composition.value,
                        motion_energy=s.motion_energy,
                        motion_direction_deg=s.motion_direction_deg,
                        quality=s.quality, mean_luma=s.mean_luma,
                        camera_angle_deg=s.camera_angle_deg,
                        has_face=s.has_face, has_speech=s.has_speech,
                    ))
                indexed += 1
                db.commit()
            except Exception as exc:
                asset.status = "failed"
                asset.error = str(exc)[:500]
                failed.append({"asset_id": asset.id, "filename": asset.filename,
                               "error": str(exc)[:200]})
                db.commit()

        if job.project_id:
            project = db.get(Project, job.project_id)
            if project and project.blueprint_id:
                self._progress(db, job, "coverage", 0.95, "Checking coverage against the style")
                from .matching import compute_coverage

                report = compute_coverage(db, project)
                project.coverage = report["overall"]
                project.coverage_report = report
                project.state = "ready_to_render"
                db.commit()

        return {"indexed": indexed, "failed": failed,
                "segments": db.query(Segment).filter(Segment.org_id == job.org_id).count()}

    def _render(self, db, job: Job) -> dict[str, Any]:
        """Match (unless already bound) then render to a real file."""
        import hashlib
        import json as _json

        from reelsedits_common import Blueprint as BlueprintModel
        from reelsedits_renderer.ffmpeg_render import RENDERER_VERSION, render

        from .matching import bind_project

        storage = get_storage()
        project = db.get(Project, job.project_id)
        preset = job.input.get("preset", "preview")

        self._progress(db, job, "matching", 0.10, "Choosing which clip fits each cut")
        bound_doc, assignment, match_result = bind_project(db, project, force=job.input.get("force", False))
        project.bound_doc = bound_doc
        db.commit()

        bp = BlueprintModel.model_validate(bound_doc)

        # Determinism means an unchanged blueprint + assignment + assets can
        # return the previous render instantly.
        asset_ids = sorted(
            r.asset_id for r in db.query(ProjectAsset).filter(
                ProjectAsset.project_id == project.id).all()
        )
        cache_key = hashlib.sha256(_json.dumps({
            "doc": bound_doc, "assets": asset_ids,
            "renderer": RENDERER_VERSION, "preset": preset,
        }, sort_keys=True).encode()).hexdigest()

        existing = (
            db.query(Render)
            .filter(Render.render_cache_key == cache_key, Render.project_id == project.id)
            .first()
        )
        if existing is not None and existing.storage_key and storage.exists(existing.storage_key):
            self._progress(db, job, "cached", 1.0, "Nothing changed — reusing the previous render")
            return {"render_id": existing.id, "cache_hit": True,
                    "url": storage.url_for(existing.storage_key)}

        self._progress(db, job, "rendering", 0.35, f"Rendering {preset}")

        paths: dict[str, Path] = {}
        for seg in db.query(Segment).filter(Segment.org_id == project.org_id).all():
            asset = db.get(Asset, seg.asset_id)
            if asset and asset.storage_key:
                paths[seg.id] = storage.local_path(asset.storage_key)

        render_id = __import__("uuid").uuid4().hex
        out_key = f"{project.org_id}/renders/{render_id}.mp4"
        tmp = Path("/tmp") / f"reelsedits-{render_id}.mp4"

        t0 = time.perf_counter()
        result = render(bp, paths, tmp, preset=preset)
        gpu_seconds = round(time.perf_counter() - t0, 2)

        self._progress(db, job, "storing", 0.90, "Saving the finished video")
        storage.put(out_key, tmp)
        tmp.unlink(missing_ok=True)

        row = Render(
            id=render_id, project_id=project.id, org_id=project.org_id, job_id=job.id,
            preset=preset, storage_key=out_key,
            width=result.width, height=result.height,
            duration_ms=result.duration_ms, bytes=result.bytes,
            render_cache_key=cache_key, renderer_version=RENDERER_VERSION,
            assignment=assignment,
            degradation={"compromises": result.compromises,
                         "degraded": bool(result.compromises),
                         "coverage": match_result["coverage"]},
            gpu_seconds=gpu_seconds,
        )
        db.add(row)
        project.state = "preview_ready" if preset == "preview" else "complete"

        job.cost_ledger = {"gpu_seconds": {"render": gpu_seconds},
                           "output_bytes": result.bytes}
        db.commit()

        return {"render_id": row.id, "cache_hit": False,
                "url": storage.url_for(out_key),
                "width": row.width, "height": row.height,
                "duration_ms": row.duration_ms, "bytes": row.bytes,
                "degradation": row.degradation}


_runner: JobRunner | None = None


def init_runner(session_factory, max_workers: int = 2) -> JobRunner:
    global _runner
    _runner = JobRunner(session_factory, max_workers=max_workers)
    return _runner


def get_runner() -> JobRunner:
    if _runner is None:
        raise RuntimeError("job runner not initialised")
    return _runner
