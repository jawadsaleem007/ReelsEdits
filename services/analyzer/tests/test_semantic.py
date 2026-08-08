"""Semantic labelling and cross-domain matching.

The headline test is `test_cross_domain_transfer_activates_with_subjects`. Until
the semantic stage existed, every segment carried `subject_class = ANY`, which
meant the SUBJECT_BRIDGES table in the matcher — the mechanism that lets a car
reference route onto motorcycle footage — never fired. These tests prove it
fires now, using a stub backend so they run without model weights.
"""

from __future__ import annotations

import numpy as np
import pytest
from reelsedits_analyzer.embedding import EMBEDDING_DIM, cosine, perceptual_embedding
from reelsedits_analyzer.semantic import (
    MIN_TRUSTED_CONFIDENCE,
    ClipBackend,
    HeuristicBackend,
    SemanticBackend,
    SemanticResult,
    VlmBackend,
    estimate_camera_height,
    get_backend,
)
from reelsedits_common.enums import CameraHeight, NarrativeRole, ShotScale, SubjectClass

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def frames(color=(120, 120, 120), n=6, h=128, w=72, noise=0):
    a = np.full((n, h, w, 3), color, dtype=np.uint8)
    if noise:
        rng = np.random.default_rng(0)
        a = np.clip(a.astype(int) + rng.integers(-noise, noise, a.shape), 0, 255).astype(np.uint8)
    return a


def horizon_frames(y_fraction: float, n=4, h=200, w=120):
    """A frame with a hard horizontal edge at a given height."""
    a = np.zeros((n, h, w, 3), dtype=np.uint8)
    split = int(h * y_fraction)
    a[:, :split] = (200, 180, 140)     # bright above
    a[:, split:] = (40, 45, 50)        # dark below
    return a


class StubBackend(SemanticBackend):
    """Returns whatever the test asks for.

    This is how the integration is tested without model weights: the contract
    is 'a backend returns enum-valued labels with a confidence', and everything
    downstream depends only on that contract.
    """

    name = "stub"
    produces_trustworthy_subjects = True

    def __init__(self, mapping: dict[str, tuple[SubjectClass, float]]) -> None:
        self.mapping = mapping
        self.calls = 0

    def label(self, frames_, context):
        self.calls += 1
        key = context.get("key", "default")
        subject, conf = self.mapping.get(key, (SubjectClass.ANY, 0.0))
        return SemanticResult(subject_class=subject, subject_confidence=conf,
                              camera_height=CameraHeight.LOW, backend=self.name)


# ---------------------------------------------------------------------------
# the confidence gate
# ---------------------------------------------------------------------------


def test_low_confidence_label_is_not_trusted():
    """A wrong hard constraint excludes footage that would have worked, which
    the user experiences as 'it ignored my best clip'. Below the threshold we
    must return ANY."""
    r = SemanticResult(subject_class=SubjectClass.VEHICLE, subject_confidence=0.3)
    assert r.subject_class is SubjectClass.VEHICLE      # recorded
    assert r.trusted_subject is SubjectClass.ANY        # but not used to constrain


def test_confident_label_is_trusted():
    r = SemanticResult(subject_class=SubjectClass.VEHICLE, subject_confidence=0.9)
    assert r.trusted_subject is SubjectClass.VEHICLE


def test_threshold_is_not_zero():
    """A threshold of 0 would make the gate decorative."""
    assert 0.3 < MIN_TRUSTED_CONFIDENCE < 0.9


# ---------------------------------------------------------------------------
# camera height -- real geometry, no weights
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("y,expected", [
    (0.25, CameraHeight.HIGH),     # horizon high in frame => looking down
    (0.50, CameraHeight.EYE),
    (0.72, CameraHeight.LOW),      # horizon low in frame => looking up
    (0.90, CameraHeight.GROUND),
])
def test_horizon_position_gives_camera_height(y, expected):
    """Horizon position is a direct consequence of camera pitch, so this works
    with no model at all — turning camera_height from a permanently-ANY dead
    signal into one the matcher can use."""
    assert estimate_camera_height(horizon_frames(y)[0]) is expected


def test_no_horizon_returns_any_not_a_guess():
    """Regression: an earlier version guessed AERIAL whenever it found no
    horizon, on the theory that a uniform lower half meant ground-from-above.
    It fired on any flat surface. A wrong camera_height is a hard constraint,
    so admitting ignorance is strictly better."""
    flat = frames(color=(128, 128, 128), noise=3)
    assert estimate_camera_height(flat[0]) is CameraHeight.ANY


# ---------------------------------------------------------------------------
# embeddings
# ---------------------------------------------------------------------------


def test_embedding_is_fixed_length_and_normalised():
    v = perceptual_embedding(frames(noise=30))
    assert len(v) == EMBEDDING_DIM
    assert abs(sum(x * x for x in v) ** 0.5 - 1.0) < 0.01


def test_embedding_is_deterministic():
    a = frames(color=(30, 120, 200), noise=20)
    assert perceptual_embedding(a) == perceptual_embedding(a)


def test_embedding_discriminates_between_shots():
    """A descriptor that returns the same vector for everything makes the
    matcher's tiebreak a constant, which is worse than no tiebreak because it
    looks like a signal."""
    warm = perceptual_embedding(frames(color=(20, 120, 220), noise=25))
    cold = perceptual_embedding(frames(color=(220, 120, 20), noise=25))
    assert cosine(warm, warm) == pytest.approx(1.0, abs=1e-4)
    assert cosine(warm, cold) < 0.95


def test_motion_changes_the_embedding():
    still = perceptual_embedding(frames(noise=20), motion_energy=0.05)
    fast = perceptual_embedding(frames(noise=20), motion_energy=0.95)
    assert cosine(still, fast) < 0.999


def test_cosine_tolerates_missing_vectors():
    """A segment indexed by an older analyser should degrade to 'no signal',
    not break matching."""
    assert cosine(None, [1.0] * EMBEDDING_DIM) == 0.0
    assert cosine([1.0, 2.0], [1.0] * EMBEDDING_DIM) == 0.0
    assert cosine([], []) == 0.0


# ---------------------------------------------------------------------------
# backend selection
# ---------------------------------------------------------------------------


def test_heuristic_backend_always_available():
    assert HeuristicBackend().available()


def test_heuristic_backend_does_not_claim_trustworthy_subjects():
    """It exists so the pipeline runs everywhere, not to pretend at semantics."""
    b = HeuristicBackend()
    assert b.produces_trustworthy_subjects is False
    r = b.label(frames(noise=20), {})
    assert r.trusted_subject is SubjectClass.ANY


def test_selection_falls_back_when_weights_are_missing(monkeypatch):
    monkeypatch.setenv("REELSEDITS_SEMANTIC_BACKEND", "auto")
    monkeypatch.setattr(VlmBackend, "available", lambda self: False)
    monkeypatch.setattr(ClipBackend, "available", lambda self: False)
    assert get_backend().name == "heuristic"


def test_explicit_request_for_unavailable_backend_degrades(monkeypatch):
    """Asking for a VLM that is not installed must degrade with a warning, not
    crash the analysis."""
    monkeypatch.setattr(VlmBackend, "available", lambda self: False)
    assert get_backend("vlm").name == "heuristic"


def test_vlm_does_not_claim_availability_without_cached_weights(monkeypatch):
    """Downloading several GB during a user's first analysis would present as
    a hang, so availability requires weights already on disk."""
    monkeypatch.delenv("REELSEDITS_VLM_MODEL", raising=False)
    monkeypatch.setattr(VlmBackend, "_weights_cached", lambda self: False)
    assert VlmBackend().available() is False


# ---------------------------------------------------------------------------
# VLM output parsing
# ---------------------------------------------------------------------------


def test_vlm_parses_well_formed_json():
    r = VlmBackend()._parse("""
      Here you go:
      {"subject_class": "mechanical_detail", "camera_height": "low",
       "narrative_role": "detail", "confidence": 0.86, "has_face": false,
       "description": "a wheel in motion"}
    """)
    assert r.subject_class is SubjectClass.MECHANICAL_DETAIL
    assert r.camera_height is CameraHeight.LOW
    assert r.narrative_role is NarrativeRole.DETAIL
    assert r.subject_confidence == pytest.approx(0.86)
    assert r.trusted_subject is SubjectClass.MECHANICAL_DETAIL


def test_vlm_coerces_out_of_vocabulary_values_to_any():
    """Constrained decoding should prevent this, but a model that emits
    'motorbike' must not poison the vocabulary — near-synonyms are exactly what
    silently destroys matching (docs/04 stage 4)."""
    r = VlmBackend()._parse('{"subject_class": "motorbike", "confidence": 0.9}')
    assert r.subject_class is SubjectClass.ANY


def test_vlm_survives_unparseable_output():
    r = VlmBackend()._parse("I'm sorry, I can't help with that.")
    assert r.subject_class is SubjectClass.ANY
    assert r.subject_confidence == 0.0


def test_vlm_confidence_is_clamped():
    r = VlmBackend()._parse('{"subject_class": "vehicle", "confidence": 4.2}')
    assert r.subject_confidence == 1.0


# ---------------------------------------------------------------------------
# THE POINT: cross-domain matching
# ---------------------------------------------------------------------------


def test_cross_domain_transfer_activates_with_subjects():
    """A car-derived slot must be fillable by motorcycle footage.

    The mechanism is SUBJECT_BRIDGES: `vehicle` and `mechanical_detail` stand in
    for one another because they play the same editorial role. That table is
    inert while every subject is ANY, which is exactly the state the pipeline
    was in before the semantic stage existed.

    This asserts the bridge scores meaningfully higher than an unrelated
    subject, which is what makes a car reference render onto a motorcycle.
    """
    from reelsedits_matcher.scoring import subject_compat

    # The reference slot came from a car wheel close-up.
    slot_wants = [SubjectClass.MECHANICAL_DETAIL]

    exhaust = subject_compat(slot_wants, SubjectClass.MECHANICAL_DETAIL)  # exact
    whole_bike = subject_compat(slot_wants, SubjectClass.VEHICLE)         # bridged
    a_meal = subject_compat(slot_wants, SubjectClass.FOOD)                # unrelated
    unknown = subject_compat(slot_wants, SubjectClass.ANY)                # no label

    assert exhaust > whole_bike > a_meal, (
        f"bridge inactive: exact={exhaust} bridged={whole_bike} unrelated={a_meal}"
    )
    # And the bridge must beat "we have no idea", or labelling gains nothing.
    assert whole_bike > unknown


def test_subjects_change_the_matcher_ranking():
    """End to end: with subject labels, the right clip wins a slot it would
    otherwise lose on structure alone."""
    from reelsedits_common import Slot, SlotRequirements
    from reelsedits_matcher import Segment, fit

    slot = Slot(
        index=0, t_in_ms=0, t_out_ms=1000, importance=0.8,
        requirements=SlotRequirements(
            shot_scale=ShotScale.CLOSE,
            subject_class=[SubjectClass.MECHANICAL_DETAIL],
            motion_energy=0.5,
        ),
    )

    def seg(sid, subject):
        return Segment(
            id=sid, asset_id=sid, t_in_ms=0, t_out_ms=6000,
            usable_in_ms=0, usable_out_ms=6000,
            shot_scale=ShotScale.CLOSE, camera_motion=__import__(
                "reelsedits_common.enums", fromlist=["CameraMotion"]
            ).CameraMotion.STATIC,
            subject_class=subject, motion_energy=0.5, quality=0.8,
        )

    exhaust = fit(slot, seg("exhaust", SubjectClass.MECHANICAL_DETAIL))[0]
    plate = fit(slot, seg("plate", SubjectClass.FOOD))[0]
    unlabelled = fit(slot, seg("unknown", SubjectClass.ANY))[0]

    assert exhaust > unlabelled > plate, (
        "subject labels must reorder candidates; got "
        f"exhaust={exhaust:.3f} unlabelled={unlabelled:.3f} plate={plate:.3f}"
    )


def test_stub_backend_flows_through_the_analyser(monkeypatch, media):
    """The backend is genuinely pluggable: a stub reaches the blueprint."""
    from reelsedits_analyzer.visual import analyze_visual

    stub = StubBackend({"default": (SubjectClass.VEHICLE, 0.95)})
    v = analyze_visual(media["reference"], semantic_backend=stub)

    assert stub.calls == len(v.shots), "every shot must be labelled"
    assert v.semantic_backend == "stub"
    assert all(s.subject_class == "vehicle" for s in v.shots)
    assert all(len(s.embedding) == EMBEDDING_DIM for s in v.shots)


def test_heuristic_backend_records_that_matching_is_degraded(media):
    """The user should be told when cross-domain matching is off, not left to
    wonder why a good clip was ignored."""
    from reelsedits_analyzer.visual import analyze_visual

    v = analyze_visual(media["reference"], semantic_backend=HeuristicBackend())
    joined = " ".join(v.notes).lower()
    assert "cross-domain" in joined or "subject classes are unset" in joined
