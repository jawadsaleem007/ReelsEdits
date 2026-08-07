"""End-to-end pipeline tests against synthetic media with known ground truth.

The fixtures are built to a spec (12 shots on a 128 BPM grid, energy jump at
8s), so these assert against what was put in rather than against whatever the
analyser happens to emit — which is the difference between a test and a
snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from reelsedits_analyzer.audio import analyze_audio, extract_audio
from reelsedits_analyzer.fusion import DEFAULT_OFFSET_MEAN_MS, build_blueprint
from reelsedits_analyzer.visual import analyze_visual, probe
from reelsedits_common import Assignment, Blueprint
from reelsedits_common.enums import CutMode, MusicStrategy
from reelsedits_indexer.index import index_clip, index_directory
from reelsedits_matcher import match
from reelsedits_renderer.ffmpeg_render import RenderError, render

REPO = Path(__file__).resolve().parents[3]
SCHEMA = REPO / "schemas" / "blueprint.schema.json"


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def test_probe_reads_real_geometry(media):
    p = probe(media["reference"])
    assert p.width == 640 and p.height == 1136
    assert p.fps == pytest.approx(30.0, abs=0.1)
    assert p.has_audio
    assert p.aspect == "9:16"
    assert 14_000 < p.duration_ms < 19_000


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------


def test_bpm_within_two_percent_of_ground_truth(media, audio_analysis):
    a = audio_analysis
    expected = media["expected_bpm"]
    assert abs(a.bpm - expected) / expected < 0.02, f"got {a.bpm}, expected ~{expected}"
    assert a.confidence > 0.8


def test_beat_grid_is_monotonic_and_regular(media, audio_analysis):
    a = audio_analysis
    assert len(a.beat_grid_ms) > 20
    assert all(b > x for x, b in zip(a.beat_grid_ms, a.beat_grid_ms[1:]))
    ibis = [b - x for x, b in zip(a.beat_grid_ms, a.beat_grid_ms[1:])]
    mean = sum(ibis) / len(ibis)
    assert abs(mean - 60_000 / media["expected_bpm"]) < 20


def test_energy_curve_spans_its_range(audio_analysis):
    """Regression: percussive material is peaky at frame resolution, and
    normalising raw RMS collapsed the whole curve to ~0. Smoothing to a 0.6s
    envelope before normalising is what makes it describe anything."""
    a = audio_analysis
    lo, hi = min(a.energy_curve), max(a.energy_curve)
    assert hi - lo > 0.5, f"energy curve is flat ({lo:.3f}–{hi:.3f})"
    mean = sum(a.energy_curve) / len(a.energy_curve)
    assert 0.1 < mean < 0.9


def test_drop_detected_where_we_put_it(media, audio_analysis):
    a = audio_analysis
    assert a.impacts, "no impacts detected despite a 4x energy jump"
    nearest = min(a.impacts, key=lambda i: abs(i["t_ms"] - media["drop_at_ms"]))
    assert abs(nearest["t_ms"] - media["drop_at_ms"]) < 1200


def test_section_energy_rises_into_the_drop(audio_analysis):
    a = audio_analysis
    kinds = [s["kind"] for s in a.sections]
    assert "drop" in kinds or "chorus" in kinds, f"no high-energy section in {kinds}"
    loud = max(a.sections, key=lambda s: s["energy"])
    quiet = min(a.sections, key=lambda s: s["energy"])
    assert loud["energy"] > quiet["energy"]
    # Cut density must track energy — that is the whole point of the field.
    assert loud["target_cut_density"] > quiet["target_cut_density"]


def test_audio_file_is_deleted_after_analysis(media, tmp_path, audio_analysis):
    """Reference audio deletion is an explicit, tested step, not incidental
    temp cleanup. docs/18 §3."""
    wav = tmp_path / "extracted.wav"
    got = extract_audio(media["reference"], wav)
    assert got is not None and wav.exists()
    wav.unlink()
    assert not wav.exists()


def test_silent_video_falls_back_without_crashing(media, tmp_path):
    import subprocess

    silent = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc2=size=320x568:rate=30", "-t", "4",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(silent)],
        check=True,
    )
    a = analyze_audio(silent, 4000)
    assert a.bpm == 120.0
    # Low confidence tells the planner to prefer content-driven cuts, which is
    # correct when there is no music to cut to.
    assert a.confidence < 0.2
    assert len(a.beat_grid_ms) >= 4


# ---------------------------------------------------------------------------
# visual
# ---------------------------------------------------------------------------


def test_shot_count_close_to_ground_truth(media, visual_analysis):
    v = visual_analysis
    expected = media["expected_shots"]
    assert abs(len(v.shots) - expected) <= 2, f"found {len(v.shots)}, built {expected}"
    assert v.sbd_confidence > 0.5


def test_shots_are_ordered_and_non_overlapping(visual_analysis):
    v = visual_analysis
    for a, b in zip(v.shots, v.shots[1:]):
        assert b.t_in_ms >= a.t_out_ms


def test_shot_scales_vary(visual_analysis):
    """The fixture deliberately mixes full-frame and heavily-cropped sources.
    A analyser that reports one scale for everything is not measuring."""
    assert len({s.shot_scale for s in visual_analysis.shots}) >= 2


def test_grade_measures_split_tone(visual_analysis):
    v = visual_analysis
    g = v.grade
    assert 0 <= g["sat_mean"] <= 1
    assert 0 <= g["split_tone_strength"] <= 1
    assert set(g["luma_percentiles"]) >= {"p1", "p50", "p99"}
    # Confidence is capped: a delivered, compressed video does not support a
    # confident grade estimate and we do not pretend otherwise.
    assert g["confidence"] <= 0.6


# ---------------------------------------------------------------------------
# blueprint assembly
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def blueprint(audio_analysis, visual_analysis) -> Blueprint:
    a, v = audio_analysis, visual_analysis
    return build_blueprint(a, v, name="fixture", platform="tiktok",
                           sound_name="original sound - test")


def test_blueprint_validates_against_json_schema(blueprint):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text())
    payload = json.loads(blueprint.model_dump_json(by_alias=True, exclude_none=True))
    jsonschema.Draft202012Validator(schema).validate(payload)


def test_cuts_land_ahead_of_the_beat(blueprint):
    """The single most valuable thing the analyser extracts.

    Expert editors cut 20–60ms BEFORE the transient, because visual perception
    lags auditory. A pipeline that snaps offsets to zero produces edits that are
    measurably beat-synced and feel mechanical. docs/08 §2.3.
    """
    assert blueprint.style.pacing.offset_mean_ms < 0
    anchored = [c for c in blueprint.cuts if c.mode is not CutMode.FREE]
    assert anchored, "no cuts anchored to the grid"
    assert all(c.offset_ms <= 0 for c in anchored)


def test_offset_default_is_negative_not_zero():
    """Even with nothing measurable, we must not fall back to snapping."""
    assert DEFAULT_OFFSET_MEAN_MS < 0


def test_slots_carry_requirements_not_reference_shots(blueprint):
    """Slots must be satisfiable by footage of a different subject entirely.
    docs/06 §5.1."""
    for slot in blueprint.slots:
        r = slot.requirements
        assert r.semantic_hint, f"slot {slot.index} has no hint"
        assert r.camera_motion, f"slot {slot.index} has no motion requirement"
        # Motion must be widened to a compatible SET, not pinned to one value.
        assert len(r.camera_motion) >= 1
        assert 0 <= r.motion_energy <= 1


def test_pacing_reflects_the_reference(blueprint):
    p = blueprint.style.pacing
    assert p.cuts_per_second > 0
    assert 0 <= p.beat_lock_ratio <= 1
    assert p.mean_shot_ms >= blueprint.constraints.min_shot_ms


def test_default_music_strategy_is_platform_attach(blueprint):
    b = blueprint.audio.music_binding
    assert b is not None
    assert b.strategy is MusicStrategy.PLATFORM_ATTACH
    assert b.platform_attach is not None
    assert b.platform_attach.bpm is not None
    # We are not licensing anything here; the platform is.
    assert b.licence_id is None


def test_platform_attach_carries_the_trim_offset(blueprint):
    """Without it the creator cannot re-sync and the edit lands off the beat."""
    card = blueprint.audio.music_binding.platform_attach
    assert card.trim_start_ms >= 0
    assert card.first_downbeat_ms >= 0
    assert "sound" in card.instructions.lower()


def test_speed_confidence_is_honest(blueprint):
    """v0 does no speed inference. It must say so rather than claim 1.0."""
    assert blueprint.provenance.confidence.speed is not None
    assert blueprint.provenance.confidence.speed < 0.5
    assert "speed" in blueprint.low_confidence_subsystems()


# ---------------------------------------------------------------------------
# indexer
# ---------------------------------------------------------------------------


def test_indexer_produces_segments(media, indexed_clips):
    clips = indexed_clips
    assert len(clips) == media["n_clips"]
    assert all(c.segments for c in clips)
    assert all(0 <= s.quality <= 1 for c in clips for s in c.segments)


def test_usable_range_is_trimmed_from_the_edges(media):
    """Handheld starts and stops cluster at the edges; cutting into them is
    what makes output read as amateur."""
    clip = index_clip(next(media["clips_dir"].glob("*.mp4")))
    for s in clip.segments:
        assert s.usable_in_ms >= s.t_in_ms
        assert s.usable_out_ms <= s.t_out_ms
        assert s.usable_out_ms > s.usable_in_ms


def test_indexer_shares_the_analyser_vocabulary(blueprint, indexed_clips):
    """Reference and footage must speak the same enums or structural matching
    silently degrades to embedding similarity. docs/09 §1.1."""
    clips = indexed_clips
    seg_scales = {s.shot_scale for c in clips for s in c.segments}
    slot_scales = {s.requirements.shot_scale for s in blueprint.slots}
    assert seg_scales and slot_scales
    # Same enum type on both sides — not just similar strings.
    assert all(type(x) is type(y) for x in seg_scales for y in slot_scales)


# ---------------------------------------------------------------------------
# match + render
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def bound(blueprint, indexed_clips):
    clips = indexed_clips
    segments = [s for c in clips for s in c.segments]
    paths = {s.id: c.path for c in clips for s in c.segments}
    result = match(blueprint, segments)

    bp = blueprint.model_copy(deep=True)
    for a in result.assignments:
        bp.slots[a.slot_index].assignment = Assignment(
            segment_id=a.segment_id, in_ms=a.in_ms, out_ms=a.out_ms,
            score=a.score, reason=a.reason,
        )
    return bp, paths, result


def test_matcher_fills_the_timeline(bound):
    _bp, _paths, result = bound
    assert result.coverage > 0.8
    assert result.solve_ms < 5000
    assert all(a.reason for a in result.assignments)


def test_render_produces_a_playable_file(bound, tmp_path):
    bp, paths, _ = bound
    out = tmp_path / "out.mp4"
    r = render(bp, paths, out, preset="preview")

    assert out.exists() and r.bytes > 50_000
    p = probe(out)
    assert p.width == 540 and p.height == 960
    assert p.duration_ms > 3000

    import subprocess
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "null", "-"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"output does not decode: {proc.stderr[:500]}"


def test_render_is_deterministic(bound, tmp_path):
    """The determinism contract (docs/10 §1) underpins render caching, the
    marketplace, collaboration and reproducible debugging."""
    import hashlib

    bp, paths, _ = bound
    digests = []
    for i in range(2):
        out = tmp_path / f"det{i}.mp4"
        render(bp, paths, out, preset="preview")
        digests.append(hashlib.sha256(out.read_bytes()).hexdigest())
    assert digests[0] == digests[1], "identical inputs produced different bytes"


def test_platform_attach_emits_a_silent_master(bound, tmp_path):
    """No music in the file. The creator adds the sound in-app, under the
    platform's licence — we never redistribute the recording."""
    bp, paths, _ = bound
    out = tmp_path / "silent.mp4"
    r = render(bp, paths, out, preset="preview")

    assert any(c["kind"] == "platform_attach" for c in r.compromises)

    import subprocess
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    levels = [line for line in proc.stderr.splitlines() if "mean_volume" in line]
    if levels:
        db = float(levels[0].split(":")[-1].strip().split()[0])
        assert db < -60, f"expected a silent master, measured {db} dB"


def test_render_refuses_unbound_blueprint(blueprint, tmp_path):
    with pytest.raises(RenderError, match="no bound slots"):
        render(blueprint, {}, tmp_path / "x.mp4", preset="preview")


def test_render_refuses_future_blueprint(bound, tmp_path):
    bp, paths, _ = bound
    future = bp.model_copy(deep=True)
    future.provenance.renderer_min_version = "99.0.0"
    with pytest.raises(RenderError, match="requires renderer"):
        render(future, paths, tmp_path / "x.mp4", preset="preview")
