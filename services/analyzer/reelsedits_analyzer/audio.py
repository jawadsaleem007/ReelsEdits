"""Audio analysis — the rhythmic skeleton.

**v0 baseline.** This uses librosa rather than Demucs + a transformer beat
tracker. It is deliberately the simplest thing that produces a usable beat grid,
so the rest of the pipeline can be built and tested against real output. The
production stack in docs/07 §6 replaces the internals of these functions without
changing their signatures:

    beat_grid()      librosa.beat  ->  Beat This! transformer + madmom DBN
    sections()       SSM novelty   ->  learned boundary detector
    (absent)                       ->  Demucs v4 stem separation, first

What does NOT change is the output contract, because the blueprint schema is
already frozen. That is the point of having built the schema first.

Nothing here retains audio. Analysis produces numbers; the waveform is dropped
when the function returns. See docs/18 §3.
"""

from __future__ import annotations

import atexit
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("reelsedits.analyzer.audio")

SR = 22_050
HOP = 512
ENERGY_HZ = 20


@dataclass(slots=True)
class AudioAnalysis:
    bpm: float
    beat_grid_ms: list[int]
    downbeats_ms: list[int]
    sections: list[dict]
    energy_curve: list[float]
    energy_hz: int
    impacts: list[dict]
    time_signature: str
    confidence: float
    duration_ms: int
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def extract_audio(video: Path, out: Path | None = None) -> Path | None:
    """Demux to 22.05kHz mono WAV. Returns None for silent video."""
    if out is None:
        # mkstemp returns (fd, path) and the fd is OPEN. Closing it is not
        # optional: POSIX permits unlinking an open file, Windows does not, so
        # leaking the descriptor made the later cleanup fail with WinError 32
        # ("used by another process") -- and that cleanup is the step that
        # deletes the reference's audio (docs/18 §3).
        fd, name = tempfile.mkstemp(suffix=".wav", prefix="reelsedits-audio-")
        os.close(fd)
        out = Path(name)

    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", str(SR), "-f", "wav", str(out)],
        capture_output=True,
    )
    if proc.returncode != 0 or not out.exists() or out.stat().st_size < 1024:
        # ffmpeg wrote nothing usable; do not leave the stub behind.
        delete_audio(out)
        return None
    return out


def delete_audio(path: Path) -> bool:
    """Delete extracted audio, retrying briefly.

    This is a compliance step, not housekeeping: the reference's audio is a
    copyrighted master and must not survive analysis (docs/18 §3). On Windows
    an antivirus or search indexer can hold a just-written file open for a
    moment, so a single unlink is not reliable.

    Returns whether the file is gone. Never raises -- a cleanup failure must
    not fail the user's job -- but it logs at ERROR and registers an exit-time
    retry, because silently leaving the audio on disk is the outcome we most
    need to avoid.
    """
    for attempt in range(5):
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError:
            if attempt < 4:
                time.sleep(0.1 * (attempt + 1))

    log.error(
        "could not delete extracted audio %s; scheduling a retry at exit", path
    )
    atexit.register(lambda p=path: p.unlink(missing_ok=True))
    return False


# ---------------------------------------------------------------------------
# beat grid
# ---------------------------------------------------------------------------


def beat_grid(y: np.ndarray, sr: int = SR) -> tuple[float, list[int], float, list[str]]:
    """Estimate tempo and beat positions.

    Returns ``(bpm, beat_times_ms, confidence, notes)``.

    Confidence comes from **inter-beat-interval regularity** rather than from
    the tracker's own score. A grid whose intervals scatter is a grid we should
    not force cuts onto, regardless of how confident the tracker claims to be —
    and downstream, low beat confidence tells the planner to prefer content-
    driven `free` cuts in that region (docs/08 §2.1).
    """
    import librosa

    notes: list[str] = []
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    tempo, beats = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, hop_length=HOP, trim=False
    )
    bpm = float(np.atleast_1d(tempo)[0])
    times = librosa.frames_to_time(beats, sr=sr, hop_length=HOP)
    grid = [round(t * 1000) for t in times]

    if len(grid) < 4:
        # Fall back to a synthetic grid at the estimated tempo. Better than
        # failing: an even grid at roughly the right tempo still produces a
        # watchable edit, and confidence records that we guessed.
        notes.append("Too few beats detected; synthesised an even grid.")
        bpm = bpm if 40 < bpm < 220 else 120.0
        ibi = 60_000 / bpm
        n = max(4, int(len(y) / sr * 1000 / ibi))
        return bpm, [round(i * ibi) for i in range(n)], 0.30, notes

    ibis = np.diff(grid)
    regularity = 1.0 - min(1.0, float(np.std(ibis) / max(np.mean(ibis), 1e-6)))
    confidence = round(float(np.clip(0.35 + 0.65 * regularity, 0.0, 1.0)), 3)

    if not 40 <= bpm <= 220:
        notes.append(f"Tempo estimate {bpm:.1f} outside plausible range; halved/doubled.")
        while bpm > 220:
            bpm /= 2
        while bpm < 40:
            bpm *= 2

    return round(bpm, 2), grid, confidence, notes


def downbeats(grid: list[int], beats_per_bar: int = 4) -> list[int]:
    """Pick downbeats from the beat grid.

    Phase is chosen by testing all `beats_per_bar` offsets and keeping the one
    whose beats carry the most onset energy — bar starts are where the kick is.
    With no energy available we take every Nth beat, which is right most of the
    time in 4/4 short-form music.
    """
    return grid[::beats_per_bar]


def downbeats_from_energy(
    grid: list[int], energy: list[float], energy_hz: int, beats_per_bar: int = 4
) -> list[int]:
    """Phase-aligned downbeats: choose the offset with the most energy on-beat."""
    if not grid or not energy:
        return downbeats(grid, beats_per_bar)

    def energy_at(ms: int) -> float:
        i = int(ms / 1000 * energy_hz)
        return energy[i] if 0 <= i < len(energy) else 0.0

    best_phase, best_score = 0, -1.0
    for phase in range(beats_per_bar):
        score = sum(energy_at(b) for b in grid[phase::beats_per_bar])
        if score > best_score:
            best_phase, best_score = phase, score
    return grid[best_phase::beats_per_bar]


# ---------------------------------------------------------------------------
# energy and impacts
# ---------------------------------------------------------------------------


def energy_curve(y: np.ndarray, sr: int = SR, hz: int = ENERGY_HZ) -> list[float]:
    """Perceptual energy envelope, normalised 0–1, resampled to `hz`.

    RMS combined with spectral flux: RMS alone misses a build that gets busier
    without getting louder, which is exactly the moment an editor accelerates.
    """
    import librosa

    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    flux = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)

    n = min(len(rms), len(flux))
    rms, flux = rms[:n], flux[:n]

    # Smooth to a ~0.6s window BEFORE normalising. Percussive material is
    # extremely peaky at frame resolution -- near-silent between kicks, huge on
    # them -- so normalising raw RMS pushes almost every sample to zero and the
    # curve stops describing anything. What we want here is the section-level
    # loudness envelope, which is what drives cut density and drop detection.
    win = max(3, int(0.6 * sr / HOP))
    kernel = np.ones(win) / win
    rms = np.convolve(rms, kernel, mode="same")
    flux = np.convolve(flux, kernel, mode="same")

    def norm(a: np.ndarray) -> np.ndarray:
        lo, hi = float(np.percentile(a, 2)), float(np.percentile(a, 98))
        return np.clip((a - lo) / max(hi - lo, 1e-9), 0, 1)

    combined = 0.65 * norm(rms) + 0.35 * norm(flux)

    duration_s = len(y) / sr
    target_n = max(2, int(duration_s * hz))
    src_t = np.linspace(0, duration_s, num=n)
    dst_t = np.linspace(0, duration_s, num=target_n)
    return [round(float(v), 4) for v in np.interp(dst_t, src_t, combined)]


def impacts(
    energy: list[float], hz: int, grid: list[int], downbeat_list: list[int]
) -> list[dict]:
    """Find drops and hits: sharp positive energy jumps near a downbeat.

    Requiring downbeat proximity is what separates a musical drop from a random
    loud moment. A drop that is not on a downbeat is usually an artefact.
    """
    if len(energy) < hz:
        return []

    arr = np.asarray(energy)
    window = max(2, hz // 2)
    kernel = np.ones(window) / window
    smooth = np.convolve(arr, kernel, mode="same")
    delta = np.diff(smooth, prepend=smooth[0])

    thresh = float(np.percentile(delta, 97))
    if thresh <= 0:
        return []

    found: list[dict] = []
    db = set(downbeat_list)
    for i, d in enumerate(delta):
        if d < thresh:
            continue
        t_ms = int(i / hz * 1000)
        near = min(db, key=lambda b: abs(b - t_ms), default=None)
        if near is None or abs(near - t_ms) > 250:
            continue
        if found and near - found[-1]["t_ms"] < 3000:
            continue
        strength = float(np.clip(smooth[i], 0, 1))
        found.append({
            "t_ms": int(near),
            "strength": round(strength, 3),
            "kind": "drop" if strength > 0.75 else "hit",
        })
    return found[:8]


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------

_SECTION_ORDER = ["intro", "build", "drop", "verse", "chorus", "breakdown", "outro"]


def sections(
    y: np.ndarray,
    sr: int,
    energy: list[float],
    hz: int,
    downbeat_list: list[int],
    duration_ms: int,
) -> list[dict]:
    """Segment into musical sections via self-similarity novelty.

    Boundaries are snapped to downbeats, because musical sections change on
    downbeats and snapping removes a whole class of off-by-a-beat errors.

    Section *kinds* are then assigned by relative energy rank rather than by a
    classifier — with no labelled training data, energy rank is honest and
    surprisingly serviceable: the loudest sustained section is the drop, the
    section rising into it is the build, the first is the intro, the last is the
    outro.
    """

    bounds_ms = _novelty_boundaries(y, sr, duration_ms)
    bounds_ms = _snap(bounds_ms, downbeat_list, tol_ms=600)
    bounds_ms = sorted({0, *bounds_ms, duration_ms})
    bounds_ms = _enforce_min_length(bounds_ms, min_ms=2500)

    spans = list(zip(bounds_ms, bounds_ms[1:]))
    if not spans:
        spans = [(0, duration_ms)]

    def mean_energy(a: int, b: int) -> float:
        i0, i1 = int(a / 1000 * hz), max(int(b / 1000 * hz), int(a / 1000 * hz) + 1)
        seg = energy[i0:i1]
        return float(np.mean(seg)) if seg else 0.0

    energies = [mean_energy(a, b) for a, b in spans]
    peak = int(np.argmax(energies)) if energies else 0

    # A section only counts as the drop if it is *clearly* the loudest. On
    # material with flat dynamics, labelling an arbitrary section "drop" would
    # make the renderer front-load its effect budget on nothing in particular.
    spread = (max(energies) - min(energies)) if len(energies) > 1 else 0.0
    has_drop = spread > 0.12

    out: list[dict] = []
    for i, ((a, b), e) in enumerate(zip(spans, energies)):
        # Peak is checked BEFORE the first/last positional rules. On a short
        # reference there may be only two or three sections, and letting
        # "last section = outro" win would mean an obvious energy peak never
        # gets labelled -- which is exactly the moment the edit is built around.
        if has_drop and i == peak and e > 0.45:
            kind = "drop"
        elif has_drop and i == peak - 1:
            kind = "build"
        elif i == 0:
            kind = "intro"
        elif i == len(spans) - 1:
            kind = "outro"
        elif e > np.median(energies):
            kind = "chorus"
        else:
            kind = "verse"

        # Cut density scales with energy. These coefficients are the v0 prior;
        # they are replaced by the measured density-vs-energy regression from
        # the reference itself once the fusion stage runs (docs/08 §3).
        out.append({
            "kind": kind,
            "t_in_ms": int(a),
            "t_out_ms": int(b),
            "energy": round(float(np.clip(e, 0, 1)), 3),
            "target_cut_density": round(float(0.6 + 2.4 * e), 2),
        })
    return out


def _novelty_boundaries(y: np.ndarray, sr: int, duration_ms: int) -> list[int]:
    import librosa

    try:
        cqt = np.abs(librosa.cqt(y=y, sr=sr, hop_length=HOP))
        cqt = librosa.util.normalize(cqt, axis=0)
        # Aim for a section roughly every 5 seconds. Short-form references are
        # frequently 15-30s, and at one section per 8s a 16s video collapsed to
        # intro+outro with no room for the build/drop the edit is shaped around.
        k = int(np.clip(round(duration_ms / 5000), 2, 8))
        frames = librosa.segment.agglomerative(cqt, k=k)
        times = librosa.frames_to_time(frames, sr=sr, hop_length=HOP)
        return [int(t * 1000) for t in times]
    except Exception as exc:
        log.warning("novelty segmentation failed (%s); using even split", exc)
        k = int(np.clip(round(duration_ms / 5000), 2, 6))
        return [int(duration_ms * i / k) for i in range(k)]


def _snap(values: list[int], anchors: list[int], tol_ms: int) -> list[int]:
    """Snap boundaries to the nearest anchor, one boundary per anchor.

    Musical sections change on downbeats, so snapping removes a class of
    off-by-a-beat errors. But two nearby boundaries must not collapse onto the
    *same* downbeat -- that silently deletes a section, which is how a 16s
    reference ends up as intro+outro with the drop unlabelled.
    """
    if not anchors:
        return values

    used: set[int] = set()
    out: list[int] = []
    for v in sorted(values):
        free = [a for a in anchors if a not in used]
        if not free:
            out.append(v)
            continue
        nearest = min(free, key=lambda a: abs(a - v))
        if abs(nearest - v) <= tol_ms:
            used.add(nearest)
            out.append(nearest)
        else:
            out.append(v)
    return out


def _enforce_min_length(bounds: list[int], min_ms: int) -> list[int]:
    """Drop boundaries that would create a section too short to read as one."""
    out = [bounds[0]]
    for b in bounds[1:]:
        if b - out[-1] >= min_ms:
            out.append(b)
    if len(out) > 1 and bounds[-1] - out[-1] < min_ms:
        out[-1] = bounds[-1]
    elif out[-1] != bounds[-1]:
        out.append(bounds[-1])
    return out


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def analyze_audio(video: Path, duration_ms_hint: int | None = None) -> AudioAnalysis:
    """Full audio pass. The waveform is not retained beyond this call."""
    import librosa

    wav = extract_audio(video)
    if wav is None:
        return _silent_fallback(duration_ms_hint or 30_000)

    try:
        y, sr = librosa.load(str(wav), sr=SR, mono=True)
    finally:
        # Explicit, not incidental. docs/18 §3.
        delete_audio(Path(wav))

    if y.size < SR // 2:
        return _silent_fallback(duration_ms_hint or 30_000)

    duration_ms = int(len(y) / sr * 1000)
    bpm, grid, conf, notes = beat_grid(y, sr)
    energy = energy_curve(y, sr)
    db = downbeats_from_energy(grid, energy, ENERGY_HZ)
    secs = sections(y, sr, energy, ENERGY_HZ, db, duration_ms)
    imp = impacts(energy, ENERGY_HZ, grid, db)

    # The waveform goes out of scope here and is never written anywhere.
    del y

    return AudioAnalysis(
        bpm=bpm,
        beat_grid_ms=grid,
        downbeats_ms=db,
        sections=secs,
        energy_curve=energy,
        energy_hz=ENERGY_HZ,
        impacts=imp,
        time_signature="4/4",
        confidence=conf,
        duration_ms=duration_ms,
        notes=notes,
    )


def _silent_fallback(duration_ms: int) -> AudioAnalysis:
    """No audio: synthesise a 120 BPM grid so the edit still has rhythm.

    Confidence 0.1 tells the planner to prefer content-driven cuts, which is the
    correct behaviour when there is no music to cut to.
    """
    bpm = 120.0
    ibi = 60_000 / bpm
    n = max(4, int(duration_ms / ibi))
    grid = [round(i * ibi) for i in range(n)]
    hz = ENERGY_HZ
    energy = [0.5] * max(2, int(duration_ms / 1000 * hz))
    return AudioAnalysis(
        bpm=bpm,
        beat_grid_ms=grid,
        downbeats_ms=grid[::4],
        sections=[{"kind": "verse", "t_in_ms": 0, "t_out_ms": duration_ms,
                   "energy": 0.5, "target_cut_density": 1.2}],
        energy_curve=energy,
        energy_hz=hz,
        impacts=[],
        time_signature="4/4",
        confidence=0.10,
        duration_ms=duration_ms,
        notes=["No audio track; synthesised a 120 BPM grid."],
    )
