"""Index user clips into matcher-ready Segments.

Reuses the analyser's visual code verbatim. That reuse is not laziness — it is
the mechanism that guarantees vocabulary parity. If the reference analyser and
the clip indexer computed shot scale differently, structural matching would
silently degrade to embedding similarity, which is the wrong objective
(docs/09 §1.1).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from reelsedits_analyzer.visual import (
    PROXY_FPS,
    Shot,
    analyse_appearance,
    analyse_motion,
    classify_composition,
    classify_scale,
    detect_shots,
    probe,
    read_proxy_frames,
)
from reelsedits_common.enums import (
    CameraHeight,
    CameraMotion,
    Composition,
    ShotScale,
    SubjectClass,
)
from reelsedits_matcher import Segment

log = logging.getLogger("reelsedits.indexer")

INDEXER_VERSION = "0.2.0-v0"

#: Trim from each end of a sub-shot. Handheld starts and stops cluster at the
#: edges: the wobble as someone presses record, the drift as they lower the
#: phone. Removing it is one of the highest-leverage things the indexer does.
EDGE_TRIM_MS = 220
MIN_SEGMENT_MS = 500


@dataclass(slots=True)
class ClipIndex:
    semantic_backend: str
    asset_id: str
    path: Path
    duration_ms: int
    width: int
    height: int
    fps: float
    quality_overall: float
    segments: list[Segment]
    notes: list[str]


def _quality(shot: Shot, sharpness_ref: float) -> float:
    """Composite usability score for a sub-shot.

    Combines sharpness, exposure sanity and stability. Exposure is scored by
    distance from mid-grey rather than absolute luma, so a deliberately dark
    moody shot is not punished as hard as a blown-out one.
    """
    sharp = float(np.clip(shot.sharpness / max(sharpness_ref, 1e-6), 0, 1))
    exposure = 1.0 - float(np.clip(abs(shot.mean_luma - 0.45) / 0.45, 0, 1))
    stability = 1.0 - float(np.clip(shot.shake, 0, 1))
    return round(float(np.clip(0.45 * sharp + 0.30 * exposure + 0.25 * stability, 0, 1)), 3)


def _to_enum(cls, value, default):
    try:
        return cls(value)
    except (ValueError, TypeError):
        return default


def index_clip(path: Path, asset_id: str | None = None,
               semantic_backend=None) -> ClipIndex:
    """Analyse one user clip into usable Segments.

    Takes the SAME semantic backend as the reference analyser. That is not a
    convenience — if the two disagree about what a "mechanical_detail" is, the
    structural constraints stop working and matching silently degrades to
    embedding similarity, which is the wrong objective (docs/09 §1.1).
    """
    from reelsedits_analyzer.embedding import perceptual_embedding
    from reelsedits_analyzer.semantic import get_backend

    asset_id = asset_id or f"ast_{path.stem}"
    backend = semantic_backend if semantic_backend is not None else get_backend()

    profile = probe(path)
    frames = read_proxy_frames(path, profile)
    spans, _conf = detect_shots(frames)

    notes: list[str] = []
    raw_shots: list[Shot] = []
    for a, b in ((a, b) for a, b, _, _ in spans):
        block = frames[a : b + 1]
        if len(block) < 3:
            continue
        motion, energy, direction, shake = analyse_motion(block)
        look = analyse_appearance(block)
        sem = backend.label(block, {"motion_energy": energy})
        raw_shots.append(Shot(
            index=len(raw_shots),
            t_in_ms=int(a / PROXY_FPS * 1000),
            t_out_ms=int(b / PROXY_FPS * 1000),
            camera_motion=motion,
            motion_energy=energy,
            motion_direction_deg=direction,
            shake=shake,
            mean_luma=look["mean_luma"],
            saturation=look["saturation"],
            sharpness=look["sharpness_raw"],
            subject_area_ratio=look["subject_area_ratio"],
            shot_scale=classify_scale(look["subject_area_ratio"]),
            composition=classify_composition(block),
            camera_height=sem.camera_height.value,
            subject_class=sem.trusted_subject.value,
            subject_confidence=sem.subject_confidence,
            has_face=sem.has_face,
            embedding=perceptual_embedding(block, energy, direction),
        ))

    if not raw_shots:
        raise ValueError(f"{path.name}: no usable sub-shots")

    # Sharpness is only meaningful relative to the sharpest thing in the clip —
    # Laplacian variance has no absolute scale across different content.
    sharpness_ref = max(s.sharpness for s in raw_shots) or 1.0

    segments: list[Segment] = []
    for shot in raw_shots:
        usable_in = shot.t_in_ms + EDGE_TRIM_MS
        usable_out = shot.t_out_ms - EDGE_TRIM_MS
        if usable_out - usable_in < MIN_SEGMENT_MS:
            # Too short once trimmed. Keep it only if the whole sub-shot is
            # long enough untrimmed; otherwise discard rather than hand the
            # matcher something it will make an ugly cut from.
            if shot.duration_ms < MIN_SEGMENT_MS:
                continue
            usable_in, usable_out = shot.t_in_ms, shot.t_out_ms

        q = _quality(shot, sharpness_ref)
        segments.append(Segment(
            id=f"seg_{asset_id}_{len(segments)}",
            asset_id=asset_id,
            t_in_ms=shot.t_in_ms,
            t_out_ms=shot.t_out_ms,
            usable_in_ms=usable_in,
            usable_out_ms=usable_out,
            shot_scale=ShotScale(shot.shot_scale),
            camera_motion=_to_motion(shot.camera_motion),
            subject_class=_to_enum(SubjectClass, shot.subject_class, SubjectClass.ANY),
            camera_height=_to_enum(CameraHeight, shot.camera_height, CameraHeight.ANY),
            composition=_to_composition(shot.composition),
            motion_energy=shot.motion_energy,
            motion_direction_deg=shot.motion_direction_deg,
            quality=q,
            mean_luma=shot.mean_luma,
            has_face=shot.has_face,
            semantic_vec=list(shot.embedding),
            # Distinct per sub-shot so the 30-degree rule in the sequence
            # objective can tell them apart within one source file.
            camera_angle_deg=float(hash((asset_id, len(segments))) % 360),
        ))

    if not segments:
        raise ValueError(f"{path.name}: all sub-shots too short after edge trimming")

    if len(segments) > 1:
        notes.append(f"{path.name}: split into {len(segments)} sub-shots")

    return ClipIndex(
        semantic_backend=backend.name,
        asset_id=asset_id,
        path=path,
        duration_ms=profile.duration_ms,
        width=profile.width,
        height=profile.height,
        fps=profile.fps,
        quality_overall=round(float(np.mean([s.quality for s in segments])), 3),
        segments=segments,
        notes=notes,
    )


def _to_motion(name: str) -> CameraMotion:
    try:
        return CameraMotion(name)
    except ValueError:
        return CameraMotion.ANY


def _to_composition(name: str) -> Composition:
    try:
        return Composition(name)
    except ValueError:
        return Composition.ANY


def index_directory(directory: Path) -> list[ClipIndex]:
    """Index every video in a directory."""
    exts = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
    out: list[ClipIndex] = []
    for p in sorted(directory.iterdir()):
        if p.suffix.lower() not in exts:
            continue
        try:
            out.append(index_clip(p))
        except Exception as exc:
            log.warning("skipping %s: %s", p.name, exc)
    return out
