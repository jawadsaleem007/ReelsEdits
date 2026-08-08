"""Shot descriptors for the matcher's similarity tiebreak.

**Named honestly.** These are *perceptual* descriptors — colour, texture,
composition, motion — not semantic ones. They cannot tell a motorcycle from a
bicycle. What they can do is tell "warm, soft, slow, centred" from "cold, busy,
fast, off-centre", which is a real editorial distinction and is what the
tiebreak is for.

When a CLIP/SigLIP backend is available its embedding is used instead, and the
two are kept the same length so nothing downstream has to branch.

One thing worth stating, because it is counterintuitive: raw semantic
similarity is the *wrong* primary objective for this product (docs/09 §1.1). A
car reference must route onto motorcycle footage, and an embedding that ranks
"car close-up" nearest to "another car close-up" fights that. So similarity
carries weight 0.08 in the matcher and structure carries 0.92. This module
exists to make that 0.08 mean something, not to take over.
"""

from __future__ import annotations

import numpy as np

#: Fixed so stored vectors stay comparable across analyser versions. Changing
#: it is a migration, not a tweak.
EMBEDDING_DIM = 64


def perceptual_embedding(frames: np.ndarray, motion_energy: float = 0.5,
                         motion_direction_deg: float | None = None) -> list[float]:
    """A 64-d L2-normalised descriptor of how a shot looks and moves.

    Composed of four blocks, each of which corresponds to something an editor
    would actually notice:

        0..23   colour   hue histogram, weighted by saturation
        24..39  tone     luminance distribution
        40..55  texture  edge density across a 4x4 spatial grid
        56..63  motion   energy and direction

    The spatial grid in the texture block is what makes composition legible to
    the descriptor: a subject on the left produces a different signature from
    the same subject centred, even at identical colour and motion.
    """
    import cv2

    if len(frames) == 0:
        return [0.0] * EMBEDDING_DIM

    sample = frames[:: max(1, len(frames) // 5)][:5]

    hue_hist = np.zeros(24, dtype=np.float32)
    luma_hist = np.zeros(16, dtype=np.float32)
    texture = np.zeros(16, dtype=np.float32)

    for frame in sample:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hue, sat = hsv[..., 0].astype(np.float32), hsv[..., 1].astype(np.float32)

        # Weight hue by saturation: the hue of a grey pixel is meaningless and
        # would otherwise swamp the histogram in desaturated footage.
        bins = np.clip((hue / 180.0 * 24).astype(int), 0, 23)
        np.add.at(hue_hist, bins.ravel(), (sat / 255.0).ravel())

        luma_hist += np.histogram(gray, bins=16, range=(0, 256))[0].astype(np.float32)

        edges = cv2.Canny(gray, 60, 160).astype(np.float32) / 255.0
        h, w = edges.shape
        for gy in range(4):
            for gx in range(4):
                cell = edges[gy * h // 4:(gy + 1) * h // 4,
                             gx * w // 4:(gx + 1) * w // 4]
                texture[gy * 4 + gx] += float(cell.mean())

    def norm(block: np.ndarray) -> np.ndarray:
        total = float(block.sum())
        return block / total if total > 1e-6 else block

    hue_hist = norm(hue_hist)
    luma_hist = norm(luma_hist)
    texture = texture / max(len(sample), 1)

    motion = np.zeros(8, dtype=np.float32)
    motion[0] = float(np.clip(motion_energy, 0, 1))
    if motion_direction_deg is not None:
        rad = np.radians(motion_direction_deg)
        motion[1] = float(np.cos(rad))
        motion[2] = float(np.sin(rad))
    motion[3] = float(texture.mean())            # overall busyness
    motion[4] = float(texture.std())             # how unevenly detail is spread
    motion[5] = float(luma_hist[:4].sum())       # shadow weight
    motion[6] = float(luma_hist[-4:].sum())      # highlight weight
    motion[7] = float(hue_hist.max())            # colour dominance

    vec = np.concatenate([hue_hist, luma_hist, texture, motion]).astype(np.float32)
    assert vec.shape[0] == EMBEDDING_DIM, f"expected {EMBEDDING_DIM}, got {vec.shape[0]}"

    n = float(np.linalg.norm(vec))
    if n > 1e-6:
        vec = vec / n
    return [round(float(v), 5) for v in vec]


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    """Cosine similarity, tolerant of missing or mismatched vectors.

    Returns 0.0 rather than raising: a segment indexed by an older analyser
    version should degrade to "no signal", not break matching.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    va, vb = np.asarray(a), np.asarray(b)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.clip(np.dot(va, vb) / (na * nb), -1.0, 1.0))
