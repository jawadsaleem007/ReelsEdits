"""Synthetic test media.

Generated with ffmpeg rather than committed as binaries: the repo stays text,
and the fixtures carry *known ground truth* — we build a reference with exactly
12 shots cut on a 128 BPM grid with an energy jump at 8s, so the tests can
assert against what we put in rather than against whatever the analyser happens
to produce.
"""

from __future__ import annotations

import math
import struct
import subprocess
import wave
from pathlib import Path

import pytest

BPM = 128.0
IBI = 60.0 / BPM
DROP_AT_S = 8.0

#: (lavfi source, duration in beats, extra filter) — 12 shots, varied scale and motion
SHOT_RECIPE = [
    ("testsrc2=size=640x1136:rate=30", 4, ""),
    ("mandelbrot=size=640x1136:rate=30", 2,
     "zoompan=z='min(zoom+0.004,1.4)':d=1:s=640x1136"),
    ("smptebars=size=640x1136:rate=30", 2, ""),
    ("life=size=640x1136:rate=30:mold=10", 3, "hue=h=90"),
    ("testsrc2=size=640x1136:rate=30", 2, "crop=320:568:0:0,scale=640:1136"),
    ("rgbtestsrc=size=640x1136:rate=30", 2, ""),
    ("mandelbrot=size=640x1136:rate=30", 4, "hue=h=200"),
    ("testsrc2=size=640x1136:rate=30", 2, "crop=200:355:100:100,scale=640:1136"),
    ("life=size=640x1136:rate=30", 3, ""),
    ("smptebars=size=640x1136:rate=30", 2, "hue=h=300"),
    ("rgbtestsrc=size=640x1136:rate=30", 3, "crop=320:568:160:284,scale=640:1136"),
    ("testsrc2=size=640x1136:rate=30", 6, "hue=h=40"),
]

CLIP_RECIPE = [
    ("wide_landscape", "testsrc2=size=720x1280:rate=30", 8, "hue=h=20"),
    ("push_in", "mandelbrot=size=720x1280:rate=30", 6,
     "zoompan=z='min(zoom+0.003,1.5)':d=1:s=720x1280"),
    ("detail_macro", "rgbtestsrc=size=720x1280:rate=30", 5,
     "crop=180:320:270:480,scale=720:1280"),
    ("pan_right", "life=size=720x1280:rate=30:mold=8", 7,
     "crop=480:1280:0:0,scale=720:1280"),
    ("close_texture", "smptebars=size=720x1280:rate=30", 5,
     "crop=240:426:240:427,scale=720:1280"),
    ("action_busy", "life=size=720x1280:rate=30", 6, "hue=h=260"),
    ("medium_calm", "testsrc2=size=720x1280:rate=30", 9,
     "crop=480:853:120:213,scale=720:1280"),
    ("wide_sky", "mandelbrot=size=720x1280:rate=30", 6, "hue=h=180"),
]


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _write_beat_track(path: Path, duration_s: float = 24.0, sr: int = 44_100) -> None:
    """A kick-and-hat click track at BPM, with a clear energy jump at DROP_AT_S."""
    frames = []
    for n in range(int(sr * duration_s)):
        t = n / sr
        phase = (t % IBI) / IBI
        env = math.exp(-phase * 28)
        kick = math.sin(2 * math.pi * 55 * t) * env
        hat = (math.sin(2 * math.pi * 7000 * t) * math.exp(-phase * 90) * 0.25
               if int(t / IBI) % 2 else 0.0)
        amp = 0.25 if t < DROP_AT_S else (0.95 if t < 18 else 0.35)
        frames.append(struct.pack("<h", int(max(-1, min(1, (kick + hat) * amp)) * 30_000)))

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(frames))


def _recipe_hash() -> str:
    """Cache key for the generated media.

    Keyed on the recipe itself, so changing a fixture regenerates automatically
    while an unchanged recipe reuses ~25s of ffmpeg work. Set
    REELSEDITS_TEST_MEDIA to point at a CI cache directory.
    """
    import hashlib

    blob = repr((SHOT_RECIPE, CLIP_RECIPE, BPM, DROP_AT_S)).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict:
    """Build the reference and user clips once, cached by recipe hash."""
    import os
    import tempfile

    cache_root = Path(os.environ.get("REELSEDITS_TEST_MEDIA", tempfile.gettempdir()))
    root = cache_root / f"reelsedits-fixtures-{_recipe_hash()}"
    clips_dir = root / "clips"

    if (root / "reference.mp4").exists() and clips_dir.is_dir() \
            and len(list(clips_dir.glob("*.mp4"))) == len(CLIP_RECIPE):
        return {
            "root": root,
            "reference": root / "reference.mp4",
            "clips_dir": clips_dir,
            "expected_shots": len(SHOT_RECIPE),
            "expected_bpm": BPM,
            "drop_at_ms": int(DROP_AT_S * 1000),
            "n_clips": len(CLIP_RECIPE),
        }

    root.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(exist_ok=True)

    beat = root / "beat.wav"
    _write_beat_track(beat)

    parts = []
    for i, (src, beats, extra) in enumerate(SHOT_RECIPE):
        p = root / f"_shot{i:02d}.mp4"
        vf = "scale=640:1136" + (f",{extra}" if extra else "")
        _ffmpeg(["-f", "lavfi", "-i", src, "-t", f"{beats * IBI}", "-vf", vf,
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                 "-r", "30", str(p)])
        parts.append(p)

    listing = root / "_list.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    silent = root / "_silent.mp4"
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing),
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(silent)])

    reference = root / "reference.mp4"
    _ffmpeg(["-i", str(silent), "-i", str(beat), "-c:v", "copy", "-c:a", "aac",
             "-shortest", str(reference)])

    for name, src, dur, extra in CLIP_RECIPE:
        _ffmpeg(["-f", "lavfi", "-i", src, "-t", str(dur), "-vf", extra or "null",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                 "-r", "30", str(clips_dir / f"{name}.mp4")])

    return {
        "root": root,
        "reference": reference,
        "clips_dir": clips_dir,
        "expected_shots": len(SHOT_RECIPE),
        "expected_bpm": BPM,
        "drop_at_ms": int(DROP_AT_S * 1000),
        "n_clips": len(CLIP_RECIPE),
    }


# ---------------------------------------------------------------------------
# cached analyses
# ---------------------------------------------------------------------------
#
# Analysis is expensive. Without session-scoped caching the suite re-decodes and
# re-analyses the same reference in a dozen tests and takes minutes, at which
# point people stop running it.


@pytest.fixture(scope="session")
def audio_analysis(media):
    from reelsedits_analyzer.audio import analyze_audio

    return analyze_audio(media["reference"])


@pytest.fixture(scope="session")
def visual_analysis(media):
    from reelsedits_analyzer.visual import analyze_visual

    return analyze_visual(media["reference"])


@pytest.fixture(scope="session")
def indexed_clips(media):
    from reelsedits_indexer.index import index_directory

    return index_directory(media["clips_dir"])
