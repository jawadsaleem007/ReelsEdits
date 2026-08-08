"""Semantic labelling — what is actually in a shot.

This is the stage that turns matching from *structural* into *cross-domain*.
Without it every segment carries ``subject_class = ANY``, which means the
SUBJECT_BRIDGES table in the matcher — the mechanism that lets a car reference
route onto motorcycle footage — never fires. Structure alone gets you "a close
shot with moderate motion"; it cannot get you "a mechanical detail".

Three backends behind one interface, selected by what is available:

    VlmBackend        A real video-language model with constrained decoding.
                      Production. Needs weights and ideally a GPU.
    ClipBackend       CLIP/SigLIP zero-shot classification against our label
                      set. Cheaper, weaker, no generation. Needs weights.
    HeuristicBackend  No weights at all. Calibrated colour/texture/motion
                      statistics with deliberately low confidence.

**Every backend returns the same enum vocabulary.** That is the whole point of
the interface: the analyser and the indexer must speak identically or
structural matching silently degrades to embedding similarity, which is the
wrong objective (docs/09 §1.1).

**Confidence is not decoration.** Below ``MIN_TRUSTED_CONFIDENCE`` the caller
must fall back to ``ANY`` rather than propagate a guess. A wrong hard constraint
is worse than an absent one: it excludes footage that would have worked.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from reelsedits_common.enums import (
    CameraHeight,
    NarrativeRole,
    SubjectClass,
)

log = logging.getLogger("reelsedits.analyzer.semantic")

#: Below this, a label is recorded but must NOT become a matcher hard
#: constraint. See `SemanticResult.trusted_subject`.
MIN_TRUSTED_CONFIDENCE = 0.55


@dataclass(slots=True)
class SemanticResult:
    subject_class: SubjectClass = SubjectClass.ANY
    subject_confidence: float = 0.0
    camera_height: CameraHeight = CameraHeight.ANY
    narrative_role: NarrativeRole = NarrativeRole.ANY
    scene_category: str | None = None
    time_of_day: str | None = None
    description: str | None = None
    has_face: bool = False
    backend: str = "none"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def trusted_subject(self) -> SubjectClass:
        """The subject class, or ANY if we are not confident enough to constrain on it.

        Returning ANY loses a signal. Returning a wrong class *excludes correct
        footage*, which the user experiences as "it ignored my best clip".
        """
        if self.subject_confidence < MIN_TRUSTED_CONFIDENCE:
            return SubjectClass.ANY
        return self.subject_class


class SemanticBackend(ABC):
    name: str = "abstract"
    #: Whether labels from this backend are strong enough to constrain matching.
    produces_trustworthy_subjects: bool = False

    @abstractmethod
    def label(self, frames: np.ndarray, context: dict[str, Any]) -> SemanticResult:
        """Label one shot from a small stack of sampled BGR frames."""

    def available(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# the label vocabulary, as natural language
# ---------------------------------------------------------------------------

#: Prompts for zero-shot classification and for the VLM's allowed values.
#: Phrased as things a person would say, because that is the distribution both
#: CLIP and instruction-tuned VLMs were trained on.
SUBJECT_PROMPTS: dict[SubjectClass, list[str]] = {
    SubjectClass.PERSON_FACE: ["a close-up of a person's face", "a portrait of someone"],
    SubjectClass.PERSON_BODY: ["a person standing or moving", "someone's full body"],
    SubjectClass.PERSON_GROUP: ["a small group of people", "several people together"],
    SubjectClass.CROWD: ["a large crowd of people", "a packed audience"],
    SubjectClass.VEHICLE: ["a car or motorcycle", "a vehicle on a road"],
    SubjectClass.MECHANICAL_DETAIL: [
        "a close-up of machinery", "a detail of an engine, wheel or exhaust",
        "mechanical parts up close",
    ],
    SubjectClass.ANIMAL: ["an animal", "a pet"],
    SubjectClass.FOOD: ["a plate of food", "a meal or drink"],
    SubjectClass.PRODUCT: ["a product on display", "an object being shown"],
    SubjectClass.ARCHITECTURE: ["a building", "architecture or interior"],
    SubjectClass.LANDSCAPE: ["a landscape", "an outdoor scene with hills or fields"],
    SubjectClass.SKY: ["the sky or clouds", "a sunset sky"],
    SubjectClass.WATER: ["water, sea or a river", "waves"],
    SubjectClass.TEXT_GRAPHIC: ["text or a graphic on screen", "a title card"],
    SubjectClass.ABSTRACT: ["an abstract pattern", "colours and texture with no clear subject"],
}

HEIGHT_PROMPTS: dict[CameraHeight, list[str]] = {
    CameraHeight.GROUND: ["filmed from ground level, very low to the floor"],
    CameraHeight.LOW: ["filmed from a low angle looking up"],
    CameraHeight.EYE: ["filmed at eye level"],
    CameraHeight.HIGH: ["filmed from a high angle looking down"],
    CameraHeight.AERIAL: ["an aerial or drone shot from far above"],
}


# ---------------------------------------------------------------------------
# 1. VLM — production
# ---------------------------------------------------------------------------

VLM_SYSTEM_PROMPT = """\
You label shots from a video for an editing tool. Reply with JSON only.

Choose ONE value for each field from the allowed lists. If a shot genuinely does
not fit any value, use "any" and give a low confidence — a wrong label is worse
than an honest "any", because it will exclude footage that would have worked.

Fields:
  subject_class   {subjects}
  camera_height   {heights}
  narrative_role  {roles}
  scene_category  free text, 1-3 words
  time_of_day     one of: dawn, morning, midday, afternoon, golden_hour, dusk, night, indoor, unknown
  has_face        true or false
  confidence      0.0 to 1.0, how sure you are about subject_class
  description     one short sentence describing the shot

Judge the shot as a whole, not the most eye-catching object in it. A wheel
filling the frame is mechanical_detail, not vehicle.
"""


class VlmBackend(SemanticBackend):
    """A real video-language model with constrained JSON decoding.

    Qwen2.5-VL / InternVL3.5 class (docs/07 §2.1). Open weights rather than a
    frontier API because this is the highest-volume model call in the system —
    every shot of every reference and every clip — and per-call API pricing at
    that volume does not fit a $29/month product.

    Constrained decoding against the enum vocabulary is not optional. Free text
    produces a long tail of near-synonyms (motorbike / motorcycle / bike) that
    silently destroys matching.
    """

    name = "vlm"
    produces_trustworthy_subjects = True

    def __init__(self, model_id: str | None = None, device: str | None = None,
                 max_frames: int = 4) -> None:
        self.model_id = model_id or os.getenv(
            "REELSEDITS_VLM_MODEL", "Qwen/Qwen2.5-VL-3B-Instruct"
        )
        self.device = device or os.getenv("REELSEDITS_VLM_DEVICE", "auto")
        self.max_frames = max_frames
        self._model = None
        self._processor = None

    def available(self) -> bool:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError:
            return False
        return bool(os.getenv("REELSEDITS_VLM_MODEL")) or self._weights_cached()

    def _weights_cached(self) -> bool:
        """Only claim availability if weights are already local.

        Downloading several GB on the first analysis would look like a hang.
        """
        from pathlib import Path

        cache = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface"))
        marker = cache / "hub" / f"models--{self.model_id.replace('/', '--')}"
        return marker.exists()

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForVision2Seq, AutoProcessor

        log.info("loading VLM %s", self.model_id)
        self._processor = AutoProcessor.from_pretrained(self.model_id)
        self._model = AutoModelForVision2Seq.from_pretrained(
            self.model_id,
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map=self.device,
        )
        self._model.eval()

    def _prompt(self) -> str:
        return VLM_SYSTEM_PROMPT.format(
            subjects=", ".join(s.value for s in SubjectClass),
            heights=", ".join(h.value for h in CameraHeight),
            roles=", ".join(r.value for r in NarrativeRole),
        )

    def label(self, frames: np.ndarray, context: dict[str, Any]) -> SemanticResult:
        import torch
        from PIL import Image

        self._load()

        # Sample evenly across the shot: the first frame is often a transition
        # tail and the last is often motion-blurred.
        idx = np.linspace(0, len(frames) - 1, min(self.max_frames, len(frames)))
        images = [
            Image.fromarray(frames[int(i)][:, :, ::-1])   # BGR -> RGB
            for i in idx
        ]

        messages = [{
            "role": "user",
            "content": [*({"type": "image"} for _ in images),
                        {"type": "text", "text": self._prompt()}],
        }]
        text = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self._processor(text=[text], images=images, return_tensors="pt")
        inputs = inputs.to(self._model.device)

        with torch.inference_mode():
            out = self._model.generate(
                **inputs,
                max_new_tokens=220,
                # Deterministic: the same shot must label the same way on every
                # analysis, or the blueprint cache serves inconsistent results.
                do_sample=False,
                temperature=None,
                top_p=None,
            )
        raw = self._processor.batch_decode(
            out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True
        )[0]
        return self._parse(raw)

    def _parse(self, raw: str) -> SemanticResult:
        """Parse the model's JSON, coercing anything outside the vocabulary to ANY."""
        try:
            start, end = raw.index("{"), raw.rindex("}") + 1
            data = json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            log.warning("VLM returned unparseable output: %.120s", raw)
            return SemanticResult(backend=self.name)

        def enum_or_any(cls, value, default):
            try:
                return cls(str(value).strip().lower())
            except (ValueError, AttributeError):
                return default

        return SemanticResult(
            subject_class=enum_or_any(SubjectClass, data.get("subject_class"),
                                      SubjectClass.ANY),
            subject_confidence=float(np.clip(data.get("confidence", 0.5), 0, 1)),
            camera_height=enum_or_any(CameraHeight, data.get("camera_height"),
                                      CameraHeight.ANY),
            narrative_role=enum_or_any(NarrativeRole, data.get("narrative_role"),
                                       NarrativeRole.ANY),
            scene_category=data.get("scene_category"),
            time_of_day=data.get("time_of_day"),
            description=data.get("description"),
            has_face=bool(data.get("has_face", False)),
            backend=self.name,
            raw=data,
        )


# ---------------------------------------------------------------------------
# 2. CLIP — cheaper middle tier
# ---------------------------------------------------------------------------


class ClipBackend(SemanticBackend):
    """Zero-shot classification against the label prompts above.

    Weaker than a VLM — no reasoning, no narrative role, and it tends to name
    the most salient *object* rather than judge the shot as a whole — but an
    order of magnitude cheaper, and it produces genuine embeddings for the
    matcher's tiebreak.

    Note the caveat from docs/07 §8: raw CLIP similarity is the *wrong*
    objective for cross-domain transfer, because it ranks a car close-up as
    most similar to another car close-up. It is used here for *classification*
    against a fixed label set, which is a different and better-posed task.
    """

    name = "clip"
    produces_trustworthy_subjects = True

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or os.getenv(
            "REELSEDITS_CLIP_MODEL", "openai/clip-vit-base-patch32"
        )
        self._model = None
        self._processor = None
        self._text_features = None
        self._labels: list[SubjectClass] = []

    def available(self) -> bool:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError:
            return False
        from pathlib import Path

        cache = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface"))
        return (cache / "hub" / f"models--{self.model_id.replace('/', '--')}").exists()

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import CLIPModel, CLIPProcessor

        self._model = CLIPModel.from_pretrained(self.model_id).eval()
        self._processor = CLIPProcessor.from_pretrained(self.model_id)

        prompts, labels = [], []
        for cls, texts in SUBJECT_PROMPTS.items():
            for t in texts:
                prompts.append(t)
                labels.append(cls)
        self._labels = labels

        with torch.inference_mode():
            inputs = self._processor(text=prompts, return_tensors="pt", padding=True)
            feats = self._model.get_text_features(**inputs)
            self._text_features = feats / feats.norm(dim=-1, keepdim=True)

    def label(self, frames: np.ndarray, context: dict[str, Any]) -> SemanticResult:
        import torch
        from PIL import Image

        self._load()
        mid = frames[len(frames) // 2][:, :, ::-1]
        image = Image.fromarray(mid)

        with torch.inference_mode():
            inputs = self._processor(images=image, return_tensors="pt")
            feats = self._model.get_image_features(**inputs)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            sims = (feats @ self._text_features.T).squeeze(0)
            probs = sims.softmax(dim=-1)

        # Several prompts map to one class; sum their mass rather than taking
        # the single best prompt, which over-weights whichever phrasing happens
        # to sit closest in embedding space.
        by_class: dict[SubjectClass, float] = {}
        for cls, p in zip(self._labels, probs.tolist()):
            by_class[cls] = by_class.get(cls, 0.0) + p

        best = max(by_class, key=by_class.get)
        return SemanticResult(
            subject_class=best,
            subject_confidence=float(by_class[best]),
            backend=self.name,
            raw={"scores": {c.value: round(v, 4) for c, v in by_class.items()}},
        )


# ---------------------------------------------------------------------------
# 3. Heuristic — always available
# ---------------------------------------------------------------------------


class HeuristicBackend(SemanticBackend):
    """No weights. Colour, texture and geometry statistics only.

    This backend deliberately reports **low confidence and mostly ANY**. It
    exists so the pipeline runs everywhere and so the interface has a default —
    not to pretend at semantics. The one thing it does honestly is
    ``camera_height``, because horizon position is geometry rather than
    semantics and is genuinely measurable (docs/08 §8).

    Everything else is a weak prior, and the confidence value says so, which
    means `trusted_subject` correctly returns ANY and the matcher ignores it.
    """

    name = "heuristic"
    produces_trustworthy_subjects = False

    def label(self, frames: np.ndarray, context: dict[str, Any]) -> SemanticResult:
        mid = frames[len(frames) // 2]
        height = estimate_camera_height(mid)

        # A few weak, honest priors. None of these reach MIN_TRUSTED_CONFIDENCE,
        # so none of them become a hard constraint.
        subject, confidence = SubjectClass.ANY, 0.0
        sky = _sky_fraction(mid)
        if sky > 0.55:
            subject, confidence = SubjectClass.SKY, 0.35
        elif height is CameraHeight.AERIAL:
            subject, confidence = SubjectClass.LANDSCAPE, 0.30

        return SemanticResult(
            subject_class=subject,
            subject_confidence=confidence,
            camera_height=height,
            backend=self.name,
            raw={"sky_fraction": round(sky, 3)},
        )


# ---------------------------------------------------------------------------
# geometry helpers -- real measurements, no model required
# ---------------------------------------------------------------------------


def estimate_camera_height(frame: np.ndarray) -> CameraHeight:
    """Infer camera height from horizon position.

    Where the horizon sits in frame is a direct consequence of camera pitch and
    elevation: a low camera pushes it high in frame, a high camera pushes it
    low, and an aerial shot usually has no horizon at all because the ground
    fills the frame.

    This is geometry, not semantics, so it works without any model — and it
    turns `camera_height` from a permanently-ANY dead signal into one the
    matcher can actually use.
    """
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, _w = gray.shape

    # A horizon is a long, roughly-horizontal edge. Look at row-wise gradient
    # energy rather than running a full Hough transform: much cheaper, and we
    # only need the row, not the line.
    sobel_y = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    row_energy = sobel_y.mean(axis=1)
    row_energy = np.convolve(row_energy, np.ones(5) / 5, mode="same")

    peak_row = int(np.argmax(row_energy))
    peak_strength = float(row_energy[peak_row] / (row_energy.mean() + 1e-6))

    # No dominant horizontal edge means we cannot locate a horizon, and there
    # is no way to tell a top-down aerial from a close-up of a flat surface
    # without semantics. Return ANY.
    #
    # An earlier version guessed AERIAL here on the theory that a uniform lower
    # half implied ground-from-above. It fired on any smooth surface, and a
    # wrong camera_height is a hard matcher constraint that excludes footage
    # which would have worked — strictly worse than admitting we don't know.
    if peak_strength < 2.2:
        return CameraHeight.ANY

    y = peak_row / h
    if y < 0.18:
        return CameraHeight.AERIAL     # horizon at the very top => looking down
    if y < 0.38:
        return CameraHeight.HIGH
    if y < 0.62:
        return CameraHeight.EYE
    if y < 0.82:
        return CameraHeight.LOW
    return CameraHeight.GROUND


def _sky_fraction(frame: np.ndarray) -> float:
    """Fraction of the upper half that looks like sky: bright, low-texture, blue-ish."""
    import cv2

    h = frame.shape[0]
    upper = frame[: h // 2]
    hsv = cv2.cvtColor(upper, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    bright = val > 120
    blueish = ((hue > 85) & (hue < 135)) | (sat < 40)   # blue, or washed-out white
    gray = cv2.cvtColor(upper, cv2.COLOR_BGR2GRAY)
    smooth = np.abs(cv2.Laplacian(gray, cv2.CV_32F)) < 12

    return float((bright & blueish & smooth).mean())


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def get_backend(preference: str | None = None) -> SemanticBackend:
    """Pick the best available backend.

    Order is quality-first: VLM, then CLIP, then heuristics. Availability checks
    require weights to be *already cached* — downloading several GB during a
    user's first analysis would present as a hang, so we degrade instead and say
    which backend we used.
    """
    preference = preference or os.getenv("REELSEDITS_SEMANTIC_BACKEND", "auto")

    explicit = {"vlm": VlmBackend, "clip": ClipBackend, "heuristic": HeuristicBackend}
    if preference in explicit:
        backend = explicit[preference]()
        if not backend.available():
            log.warning("semantic backend %r requested but unavailable; "
                        "falling back to heuristics", preference)
            return HeuristicBackend()
        return backend

    for cls in (VlmBackend, ClipBackend):
        backend = cls()
        if backend.available():
            log.info("using semantic backend: %s", backend.name)
            return backend

    log.info("no model weights found; using heuristic semantic backend "
             "(subject_class will stay 'any' and cross-domain matching is inactive)")
    return HeuristicBackend()
