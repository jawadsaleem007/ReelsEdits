"""Perceptual fingerprinting for the blueprint cache.

Deliberately *not* byte-level. The same TikTok downloaded twice, at different
bitrates, from different mirrors, must hit the same cache entry — otherwise the
entire cost model collapses, because reference re-use is exactly the case where
the same *content* arrives as different *bytes* (docs/08 §1).

A cache hit costs ~0.4% of a miss, so this function is load-bearing on margin
rather than merely on latency.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import numpy as np

PHASH_FPS = 1.0
PHASH_SIZE = 32
DCT_KEEP = 8


def _dct_phash(gray: np.ndarray) -> int:
    """64-bit perceptual hash: DCT, keep the low-frequency 8x8, threshold on median.

    Robust to bitrate, scale and mild crop, which is precisely the set of
    transformations a re-uploaded video has been through.
    """
    from scipy.fftpack import dct

    d = dct(dct(gray.astype(np.float32), axis=0, norm="ortho"), axis=1, norm="ortho")
    low = d[:DCT_KEEP, :DCT_KEEP].flatten()
    med = np.median(low[1:])          # skip DC, which only encodes brightness
    bits = 0
    for i, v in enumerate(low):
        if v > med:
            bits |= 1 << i
    return bits


def phash_sequence(path: Path, fps: float = PHASH_FPS) -> list[int]:
    """One perceptual hash per second of video."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", f"fps={fps},scale={PHASH_SIZE}:{PHASH_SIZE}",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True,
    )
    buf = np.frombuffer(proc.stdout, dtype=np.uint8)
    frame = PHASH_SIZE * PHASH_SIZE
    n = len(buf) // frame
    if n == 0:
        return []
    frames = buf[: n * frame].reshape(n, PHASH_SIZE, PHASH_SIZE)
    return [_dct_phash(f) for f in frames]


def _duration_ms(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return int(float(out) * 1000)
    except ValueError:
        return 0


def fingerprint_video(path: Path, analyzer_version: str) -> str:
    """Content-addressed identity of a reference.

    ``analyzer_version`` is part of the key and is mandatory. Ship a better
    shot-boundary detector and every old blueprint must miss; omit it and you
    serve blueprints from a superseded model indefinitely, with no error
    anywhere — the kind of bug found three months later by a confused user.
    """
    hashes = phash_sequence(path)
    # Quantise duration to 100ms so a re-encode with one extra trailing frame
    # still matches.
    duration_bucket = round(_duration_ms(path) / 100)

    payload = b"|".join([
        b"".join(h.to_bytes(8, "big") for h in hashes),
        str(duration_bucket).encode(),
        analyzer_version.encode(),
    ])
    return hashlib.sha256(payload).hexdigest()


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def near_duplicate(a: list[int], b: list[int], max_bit_ratio: float = 0.12,
                   min_agreement: float = 0.8) -> bool:
    """Whether two pHash sequences describe the same video.

    Catches the very common case of a repost with a different intro card or a
    2% crop — the exact-fingerprint path would miss it and pay for a full
    re-analysis.
    """
    if not a or not b:
        return False
    n = min(len(a), len(b))
    if n < 3 or abs(len(a) - len(b)) > max(2, 0.25 * n):
        return False
    close = sum(1 for i in range(n) if hamming(a[i], b[i]) <= 64 * max_bit_ratio)
    return close / n >= min_agreement
