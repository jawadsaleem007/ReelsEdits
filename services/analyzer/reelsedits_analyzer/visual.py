"""Shot boundary detection, camera motion, composition and colour.

**v0 baselines.** Histogram distance and frame differencing stand in for the
TransNetV2 + AutoShot ensemble; Farneback optical flow stands in for SEA-RAFT.
Both are chosen because they run on CPU with no weights to download, so the
whole pipeline is testable today. docs/07 §3 and §5 describe the replacements.

The output contracts match what the production models will emit, so swapping
them in is a function-body change.

One thing here is NOT a placeholder: the gradual-transition *interval* logic.
Collapsing a 14-frame dissolve to a single cut point loses the transition
duration the blueprint needs, and that mistake is easy to bake in early and
expensive to remove later.
"""

from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("reelsedits.analyzer.visual")

#: Analysis runs on a small proxy. Motion structure survives downscaling and
#: this is ~20x cheaper than full resolution (docs/04 stage 0).
PROXY_W = 256
PROXY_FPS = 12

HARD_CUT_THRESHOLD = 0.42
GRADUAL_THRESHOLD = 0.14
MIN_SHOT_FRAMES = 4


@dataclass(slots=True)
class MediaProfile:
    width: int
    height: int
    fps: float
    duration_ms: int
    has_audio: bool
    codec: str = "h264"
    rotation: int = 0

    @property
    def aspect(self) -> str:
        r = self.width / max(self.height, 1)
        for name, val in (("9:16", 0.5625), ("1:1", 1.0), ("4:5", 0.8),
                          ("16:9", 1.7778), ("4:3", 1.3333)):
            if abs(r - val) < 0.05:
                return name
        return "custom"


@dataclass(slots=True)
class Shot:
    index: int
    t_in_ms: int
    t_out_ms: int
    #: Transition INTO this shot. duration_ms 0 means a hard cut.
    transition_type: str = "hard_cut"
    transition_duration_ms: int = 0
    transition_direction_deg: float | None = None
    transition_confidence: float = 1.0

    camera_motion: str = "static"
    motion_energy: float = 0.0
    motion_direction_deg: float | None = None
    shake: float = 0.0

    mean_luma: float = 0.5
    saturation: float = 0.5
    shadow_hue_deg: float = 0.0
    highlight_hue_deg: float = 0.0
    sharpness: float = 0.5
    subject_area_ratio: float = 0.12
    shot_scale: str = "medium"
    composition: str = "centered"

    # Populated by the semantic backend. camera_height is measured from the
    # horizon and is real even with no model; subject_class stays "any" unless
    # a backend we trust supplied it (docs/07 §2).
    camera_height: str = "any"
    subject_class: str = "any"
    subject_confidence: float = 0.0
    narrative_role: str = "any"
    description: str | None = None
    has_face: bool = False
    embedding: list = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return self.t_out_ms - self.t_in_ms


@dataclass(slots=True)
class VisualAnalysis:
    profile: MediaProfile
    shots: list[Shot]
    sbd_confidence: float
    grade: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: Which semantic backend produced the labels. Recorded because a blueprint
    #: built with heuristics and one built with a VLM are not comparable, and
    #: the cache key must distinguish them.
    semantic_backend: str = "none"


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def probe(path: Path) -> MediaProfile:
    import json

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)

    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise ValueError(f"{path.name}: no video stream")
    has_audio = any(s["codec_type"] == "audio" for s in data["streams"])

    num, den = (v.get("avg_frame_rate") or "25/1").split("/")
    fps = float(num) / float(den) if float(den) else 25.0

    duration_s = float(data["format"].get("duration") or v.get("duration") or 0)

    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])

    w, h = int(v["width"]), int(v["height"])
    # Portrait footage that reports landscape dimensions with a rotation flag is
    # a classic source of silently-wrong downstream geometry (docs/20 §"will go
    # wrong" #2). Normalise here, once.
    if abs(rotation) in (90, 270):
        w, h = h, w

    return MediaProfile(
        width=w, height=h, fps=round(fps, 3),
        duration_ms=int(duration_s * 1000), has_audio=has_audio,
        codec=v.get("codec_name", "unknown"), rotation=rotation,
    )


def read_proxy_frames(path: Path, profile: MediaProfile) -> np.ndarray:
    """Decode to a small BGR array stack at PROXY_FPS."""

    h = max(64, int(PROXY_W * profile.height / max(profile.width, 1)))
    h -= h % 2
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", f"fps={PROXY_FPS},scale={PROXY_W}:{h}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        capture_output=True,
    )
    buf = np.frombuffer(proc.stdout, dtype=np.uint8)
    frame_size = PROXY_W * h * 3
    n = len(buf) // frame_size
    if n == 0:
        raise ValueError(f"{path.name}: decoded no frames")
    return buf[: n * frame_size].reshape(n, h, PROXY_W, 3)


# ---------------------------------------------------------------------------
# shot boundaries
# ---------------------------------------------------------------------------


def _hist(frame: np.ndarray) -> np.ndarray:
    import cv2

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
    return (h / max(h.sum(), 1e-9)).flatten()


def detect_shots(frames: np.ndarray) -> tuple[list[tuple[int, int, str, int]], float]:
    """Find shot boundaries and gradual-transition intervals.

    Returns ``([(start_frame, end_frame, transition_type, transition_frames)], confidence)``.

    Two signals, deliberately uncorrelated so their agreement is informative:
    chi-squared histogram distance (colour distribution change) and mean
    absolute pixel difference (spatial change). A hard cut spikes both. A
    dissolve produces a *sustained plateau* in both — which is what the interval
    detection looks for, rather than a single peak.
    """
    n = len(frames)
    if n < MIN_SHOT_FRAMES * 2:
        return [(0, n - 1, "hard_cut", 0)], 0.5

    hists = np.array([_hist(f) for f in frames])
    gray = frames.mean(axis=3).astype(np.float32)

    hist_d = np.array([
        0.5 * np.sum((hists[i] - hists[i + 1]) ** 2 / (hists[i] + hists[i + 1] + 1e-9))
        for i in range(n - 1)
    ])
    pix_d = np.array([
        np.abs(gray[i + 1] - gray[i]).mean() / 255.0 for i in range(n - 1)
    ])

    def norm(a: np.ndarray) -> np.ndarray:
        p99 = float(np.percentile(a, 99)) or 1.0
        return np.clip(a / p99, 0, 1)

    combined = 0.6 * norm(hist_d) + 0.4 * norm(pix_d)

    boundaries: list[tuple[int, str, int]] = []
    i = 0
    while i < len(combined):
        if combined[i] >= HARD_CUT_THRESHOLD:
            # Hard cut unless the elevated region persists, in which case this
            # is the leading edge of a gradual transition.
            j = i
            while j + 1 < len(combined) and combined[j + 1] >= GRADUAL_THRESHOLD:
                j += 1
            span = j - i + 1
            if span >= 3:
                boundaries.append((i, "cross_dissolve", span))
            else:
                boundaries.append((i, "hard_cut", 0))
            i = j + MIN_SHOT_FRAMES
        elif combined[i] >= GRADUAL_THRESHOLD:
            j = i
            while j + 1 < len(combined) and combined[j + 1] >= GRADUAL_THRESHOLD:
                j += 1
            span = j - i + 1
            if span >= 3:
                boundaries.append((i, "cross_dissolve", span))
                i = j + MIN_SHOT_FRAMES
            else:
                i += 1
        else:
            i += 1

    shots: list[tuple[int, int, str, int]] = []
    prev_end = 0
    prev_type, prev_dur = "hard_cut", 0
    for b, ttype, tdur in boundaries:
        if b - prev_end >= MIN_SHOT_FRAMES:
            shots.append((prev_end, b, prev_type, prev_dur))
            prev_end, prev_type, prev_dur = b + 1, ttype, tdur
    if n - 1 - prev_end >= MIN_SHOT_FRAMES or not shots:
        shots.append((prev_end, n - 1, prev_type, prev_dur))

    # Confidence: separation between boundary and non-boundary scores. A video
    # where every frame pair looks equally different is one where we are not
    # really detecting shots.
    peaks = combined[combined >= HARD_CUT_THRESHOLD]
    floor = combined[combined < GRADUAL_THRESHOLD]
    if peaks.size and floor.size:
        sep = float(np.mean(peaks) - np.mean(floor))
        conf = float(np.clip(0.4 + sep, 0.2, 0.95))
    else:
        conf = 0.45
    return shots, round(conf, 3)


# ---------------------------------------------------------------------------
# motion
# ---------------------------------------------------------------------------


def analyse_motion(frames: np.ndarray) -> tuple[str, float, float | None, float]:
    """Camera motion class, energy, dominant direction and shake.

    Returns ``(motion_class, energy_0_1, direction_deg_or_None, shake_0_1)``.

    Classification comes from decomposing the flow field:
      - coherent translation          -> pan / truck
      - divergence-dominated          -> zoom / dolly
      - high-frequency, low-coherence -> handheld
      - low magnitude                 -> static

    The divergence/curl split is the clean discriminator between a push-in and a
    rotation, which otherwise look similar in aggregate magnitude and render
    completely differently (docs/08 §4.1).
    """
    import cv2

    if len(frames) < 3:
        return "static", 0.0, None, 0.0

    step = max(1, len(frames) // 12)
    sampled = frames[::step][:12]
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in sampled]

    mags, dxs, dys, divs = [], [], [], []
    for a, b in zip(grays, grays[1:]):
        flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        fx, fy = flow[..., 0], flow[..., 1]
        mags.append(float(np.mean(np.hypot(fx, fy))))
        dxs.append(float(np.mean(fx)))
        dys.append(float(np.mean(fy)))

        h, w = fx.shape
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy = w / 2, h / 2
        rx, ry = xx - cx, yy - cy
        r = np.hypot(rx, ry) + 1e-6
        # Radial component of flow: positive mean => expansion => zoom in
        divs.append(float(np.mean((fx * rx + fy * ry) / r)))

    mean_mag = float(np.mean(mags))
    mean_dx, mean_dy = float(np.mean(dxs)), float(np.mean(dys))
    mean_div = float(np.mean(divs))

    # Energy is normalised against a reference of ~6px/frame at proxy scale,
    # which is a brisk but not extreme camera move.
    energy = float(np.clip(mean_mag / 6.0, 0.0, 1.0))

    # Shake: how much the per-frame direction jitters relative to its magnitude.
    shake = float(np.clip(np.std(dxs) + np.std(dys), 0, 4) / 4.0) if len(dxs) > 1 else 0.0

    translation = math.hypot(mean_dx, mean_dy)
    coherence = translation / max(mean_mag, 1e-6)

    direction: float | None = None
    if mean_mag < 0.35:
        motion = "static"
    elif abs(mean_div) > 0.55 * mean_mag:
        motion = "zoom_in" if mean_div > 0 else "zoom_out"
    elif coherence > 0.55:
        direction = math.degrees(math.atan2(mean_dy, mean_dx))
        if abs(mean_dx) >= abs(mean_dy):
            motion = "pan_right" if mean_dx > 0 else "pan_left"
        else:
            motion = "tilt_down" if mean_dy > 0 else "tilt_up"
    elif shake > 0.35:
        motion = "handheld"
    else:
        motion = "tracking"

    return motion, round(energy, 3), (round(direction, 1) if direction is not None else None), round(shake, 3)


# ---------------------------------------------------------------------------
# appearance
# ---------------------------------------------------------------------------


def analyse_appearance(frames: np.ndarray) -> dict:
    """Luma, saturation, split-tone signature, sharpness, subject prominence.

    The split-tone signature — mean hue of the darkest 15% versus the brightest
    15% of pixels — is the single most recognisable element of most short-form
    grades, and it is cheap to measure (docs/08 §5.2).
    """
    import cv2

    sample = frames[:: max(1, len(frames) // 6)][:6]
    lumas, sats, sharps, subj = [], [], [], []
    shadow_hues, highlight_hues = [], []

    for f in sample:
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)

        lumas.append(float(gray.mean()) / 255.0)
        sats.append(float(hsv[..., 1].mean()) / 255.0)
        sharps.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))

        lo, hi = np.percentile(gray, 15), np.percentile(gray, 85)
        hue = hsv[..., 0].astype(np.float32) * 2.0     # OpenCV hue is 0-179
        if (gray <= lo).any():
            shadow_hues.append(_circular_mean(hue[gray <= lo]))
        if (gray >= hi).any():
            highlight_hues.append(_circular_mean(hue[gray >= hi]))

        # Subject prominence proxy: fraction of the frame in the top quartile of
        # local edge density. A stand-in for a SAM 3 mask area ratio -- crude,
        # but it puts shot scale on a measured footing rather than a guess.
        edges = cv2.Canny(gray, 60, 160)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        dense = cv2.dilate(edges, k)
        subj.append(float((dense > 0).mean()))

    max_sharp = max(sharps) or 1.0
    return {
        "mean_luma": round(float(np.mean(lumas)), 4),
        "saturation": round(float(np.mean(sats)), 4),
        "sharpness": round(float(np.clip(np.mean(sharps) / max(max_sharp, 1e-6), 0, 1)), 4),
        "sharpness_raw": round(float(np.mean(sharps)), 2),
        "shadow_hue_deg": round(_circular_mean_of(shadow_hues), 1),
        "highlight_hue_deg": round(_circular_mean_of(highlight_hues), 1),
        "subject_area_ratio": round(float(np.clip(np.mean(subj), 0.005, 0.95)), 4),
    }


def _circular_mean(a: np.ndarray) -> float:
    r = np.radians(a.astype(np.float64))
    return float(np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360)


def _circular_mean_of(vals: list[float]) -> float:
    if not vals:
        return 0.0
    return _circular_mean(np.array(vals))


#: Subject-area-ratio thresholds. Measured, not asked of a model (docs/04 §4).
_SCALE_BANDS = [
    (0.55, "extreme_close"), (0.28, "close"), (0.15, "medium_close"),
    (0.07, "medium"), (0.02, "wide"),
]


def classify_scale(subject_area_ratio: float) -> str:
    for threshold, name in _SCALE_BANDS:
        if subject_area_ratio > threshold:
            return name
    return "extreme_wide"


def classify_composition(frames: np.ndarray) -> str:
    """Where the visual weight sits: thirds, symmetric, or centred."""
    import cv2

    f = frames[len(frames) // 2]
    gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160).astype(np.float32)
    _h, w = edges.shape

    col_weight = edges.sum(axis=0)
    total = col_weight.sum() or 1.0
    centroid_x = float((col_weight * np.arange(w)).sum() / total) / w

    left, right = col_weight[: w // 2].sum(), col_weight[w // 2:].sum()
    symmetry = 1.0 - abs(left - right) / max(left + right, 1e-6)

    if symmetry > 0.88:
        return "symmetric"
    if centroid_x < 0.40:
        return "thirds_left"
    if centroid_x > 0.60:
        return "thirds_right"
    return "centered"


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def analyze_visual(path: Path, semantic_backend=None) -> VisualAnalysis:
    from .embedding import perceptual_embedding
    from .semantic import get_backend

    profile = probe(path)
    frames = read_proxy_frames(path, profile)
    spans, sbd_conf = detect_shots(frames)

    backend = semantic_backend if semantic_backend is not None else get_backend()

    shots: list[Shot] = []
    for a, b, ttype, tframes in spans:
        block = frames[a : b + 1]
        if len(block) < 2:
            continue
        motion, energy, direction, shake = analyse_motion(block)
        look = analyse_appearance(block)
        sem = backend.label(block, {"motion_energy": energy,
                                    "shot_scale": classify_scale(look["subject_area_ratio"])})

        shots.append(Shot(
            index=len(shots),
            t_in_ms=int(a / PROXY_FPS * 1000),
            t_out_ms=int(b / PROXY_FPS * 1000),
            transition_type=ttype,
            transition_duration_ms=int(tframes / PROXY_FPS * 1000),
            transition_direction_deg=direction if ttype != "hard_cut" else None,
            transition_confidence=sbd_conf,
            camera_motion=motion,
            motion_energy=energy,
            motion_direction_deg=direction,
            shake=shake,
            mean_luma=look["mean_luma"],
            saturation=look["saturation"],
            shadow_hue_deg=look["shadow_hue_deg"],
            highlight_hue_deg=look["highlight_hue_deg"],
            sharpness=look["sharpness"],
            subject_area_ratio=look["subject_area_ratio"],
            shot_scale=classify_scale(look["subject_area_ratio"]),
            composition=classify_composition(block),
            camera_height=sem.camera_height.value,
            # trusted_subject, not subject_class: a low-confidence label must
            # not become a hard matcher constraint that excludes good footage.
            subject_class=sem.trusted_subject.value,
            subject_confidence=sem.subject_confidence,
            narrative_role=sem.narrative_role.value,
            description=sem.description,
            has_face=sem.has_face,
            embedding=perceptual_embedding(block, energy, direction),
        ))

    grade = _global_grade(frames)
    notes = []
    if sbd_conf < 0.5:
        notes.append("Shot boundaries have low separation; cut list may be approximate.")

    if backend.name == "heuristic":
        notes.append(
            "No semantic model available: subject classes are unset, so "
            "cross-domain matching is inactive and matching runs on shot scale, "
            "camera motion and quality alone."
        )

    return VisualAnalysis(profile=profile, shots=shots, sbd_confidence=sbd_conf,
                          grade=grade, notes=notes, semantic_backend=backend.name)


def _global_grade(frames: np.ndarray) -> dict:
    """Measured colour statistics of the delivered look.

    Not a recovered LUT — see docs/08 §5 for why that is not obtainable from a
    delivered video. These statistics are the target the renderer optimises the
    USER's footage toward.
    """
    import cv2

    look = analyse_appearance(frames)
    gray = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames[::4][:12]])
    pct = {f"p{p}": round(float(np.percentile(gray, p)) / 255.0, 4)
           for p in (1, 5, 25, 50, 75, 95, 99)}

    shadow, highlight = look["shadow_hue_deg"], look["highlight_hue_deg"]
    diff = abs(((shadow - highlight + 180) % 360) - 180)
    return {
        "luma_percentiles": pct,
        "sat_mean": look["saturation"],
        "shadow_hue_deg": shadow,
        "highlight_hue_deg": highlight,
        # Split tone is strongest when shadows and highlights are opposed.
        "split_tone_strength": round(float(np.clip(diff / 180.0, 0, 1)), 3),
        # Confidence is capped: a delivered, compressed video does not support a
        # confident grade estimate, and claiming otherwise would be dishonest.
        "confidence": 0.55,
    }
