"""API contract tests.

These drive the app exactly as the browser does: upload, analyse, index, check
coverage, render, swap, download. If these pass, the product works end to end
for a real user.
"""

from __future__ import annotations

import pytest

from .conftest import build_project, wait_for_job

# ---------------------------------------------------------------------------
# ops
# ---------------------------------------------------------------------------


def test_health_and_ready(client):
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/readyz").json()
    assert ready["status"] == "ok"
    # Every artefact records the version that produced it; the app must be able
    # to say what versions it is running.
    assert set(ready["versions"]) >= {"analyzer", "renderer", "matcher"}


def test_ui_is_served(client):
    body = client.get("/").text
    assert "ReelsEdits" in body
    assert "<video" in body, "the result step needs a player"


# ---------------------------------------------------------------------------
# uploads
# ---------------------------------------------------------------------------


def test_upload_stores_and_registers(client, small_media):
    project = client.post("/v1/projects", json={"name": "u"}).json()
    with open(small_media["reference"], "rb") as f:
        r = client.post("/v1/assets",
                        files={"file": ("reference.mp4", f, "video/mp4")},
                        data={"kind": "reference", "project_id": project["id"]})
    assert r.status_code == 201
    asset = r.json()
    assert asset["bytes"] > 1000
    assert asset["status"] == "uploaded"


def test_unsupported_format_rejected_with_a_fix(client):
    r = client.post("/v1/assets",
                    files={"file": ("notes.txt", b"hello", "text/plain")},
                    data={"kind": "clip"})
    assert r.status_code == 415
    body = r.json()
    assert body["type"].endswith("unsupported-media")
    # An error the caller cannot act on is a support ticket.
    assert "fix" in body


# ---------------------------------------------------------------------------
# reference analysis
# ---------------------------------------------------------------------------


def test_analysis_produces_a_blueprint(client, small_media):
    project = client.post("/v1/projects", json={"name": "a"}).json()
    with open(small_media["reference"], "rb") as f:
        ref = client.post("/v1/assets",
                          files={"file": ("reference.mp4", f, "video/mp4")},
                          data={"kind": "reference", "project_id": project["id"]}).json()

    r = client.post(f"/v1/projects/{project['id']}/reference", json={"asset_id": ref["id"]})
    assert r.status_code == 202
    job = r.json()
    assert job["events_url"].endswith("/events")

    out = wait_for_job(client, job["job_id"])["output"]
    assert out["blueprint_id"]
    assert out["cache_hit"] is False
    assert out["shots"] >= 2


def test_second_identical_reference_hits_the_cache(client, small_media):
    """A cache hit costs ~0.4% of a miss. This is the load-bearing number in
    the cost model (docs/14 §6), so it gets a test rather than a footnote."""
    first = build_project(client, small_media, index=False)
    blueprint_id = client.get(f"/v1/projects/{first}").json()["blueprint_id"]

    project = client.post("/v1/projects", json={"name": "again"}).json()
    with open(small_media["reference"], "rb") as f:
        ref = client.post("/v1/assets",
                          files={"file": ("reference.mp4", f, "video/mp4")},
                          data={"kind": "reference", "project_id": project["id"]}).json()
    job = client.post(f"/v1/projects/{project['id']}/reference",
                      json={"asset_id": ref["id"]}).json()
    out = wait_for_job(client, job["job_id"])["output"]

    assert out["cache_hit"] is True
    assert out["blueprint_id"] == blueprint_id


def test_style_card_describes_without_showing(client, small_media):
    """The style card is where the user decides whether to trust the system.
    It must contain the derived description and no reference frames."""
    pid = build_project(client, small_media, index=False)
    bid = client.get(f"/v1/projects/{pid}").json()["blueprint_id"]

    card = client.get(f"/v1/blueprints/{bid}/style-card").json()
    assert len(card["summary"]) > 40
    assert card["bpm"] > 20
    assert card["slot_count"] >= 2
    assert "offset_mean_ms" in card["pacing"]
    # Cuts land ahead of the beat; a card reporting 0 means we quantised away
    # the thing that makes edits feel tight.
    assert card["pacing"]["offset_mean_ms"] < 0

    # No media, no thumbnails, no frames — only numbers and prose.
    blob = str(card).lower()
    assert "base64" not in blob
    assert "thumbnail" not in blob


def test_style_card_flags_approximate_subsystems(client, small_media):
    pid = build_project(client, small_media, index=False)
    bid = client.get(f"/v1/projects/{pid}").json()["blueprint_id"]
    card = client.get(f"/v1/blueprints/{bid}/style-card").json()
    # v0 does no speed inference and must say so rather than claim confidence.
    assert "speed" in card["low_confidence_subsystems"]


def test_music_defaults_to_platform_attach(client, small_media):
    pid = build_project(client, small_media, index=False)
    bid = client.get(f"/v1/projects/{pid}").json()["blueprint_id"]
    music = client.get(f"/v1/blueprints/{bid}/style-card").json()["music"]

    assert music["strategy"] == "platform_attach"
    assert music["platform_attach"]["trim_start_ms"] >= 0
    # We are not licensing anything in this mode; the platform is.
    assert "licence_id" not in music or music["licence_id"] is None


# ---------------------------------------------------------------------------
# indexing & coverage
# ---------------------------------------------------------------------------


def test_indexing_produces_segments(client, small_media):
    pid = build_project(client, small_media)
    project = client.get(f"/v1/projects/{pid}").json()
    clips = [a for a in project["assets"] if a["kind"] == "clip"]
    assert len(clips) == 5
    assert all(a["status"] == "ready" for a in clips)
    assert sum(a["segments"] for a in clips) >= 5


def test_index_without_footage_explains_what_to_do(client):
    project = client.post("/v1/projects", json={"name": "empty"}).json()
    r = client.post(f"/v1/projects/{project['id']}/index")
    assert r.status_code == 400
    assert "fix" in r.json()


def test_coverage_names_the_missing_shot(client, small_media):
    """'You need a shot with strong left-to-right motion' sends someone out to
    film. 'Insufficient footage' makes them leave (docs/08 §9)."""
    pid = build_project(client, small_media)
    cov = client.get(f"/v1/projects/{pid}/coverage").json()

    assert 0 <= cov["overall"] <= 1
    assert cov["verdict"] in ("good", "degraded", "insufficient")
    assert cov["segment_count"] > 0
    for gap in cov["gaps"]:
        assert gap["message"].startswith("You need")
        assert gap["slots"]


def test_coverage_requires_a_blueprint(client):
    project = client.post("/v1/projects", json={"name": "bare"}).json()
    r = client.get(f"/v1/projects/{project['id']}/coverage")
    assert r.status_code == 400
    assert "fix" in r.json()


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


@pytest.fixture()
def rendered(client, small_media):
    pid = build_project(client, small_media)
    job = client.post(f"/v1/projects/{pid}/render",
                      json={"preset": "preview", "acknowledge_degradation": True}).json()
    out = wait_for_job(client, job["job_id"])["output"]
    return pid, out


def test_render_produces_a_downloadable_video(client, rendered):
    _pid, out = rendered
    assert out["render_id"]
    assert out["bytes"] > 10_000

    r = client.get(out["url"])
    assert r.status_code == 200
    body = r.content
    assert len(body) > 10_000
    # A real MP4: 'ftyp' box in the first 32 bytes.
    assert b"ftyp" in body[:32], "downloaded file is not an MP4"


def test_render_is_cached_when_nothing_changed(client, rendered):
    """Determinism is what makes this safe: identical inputs produce identical
    bytes, so returning the previous render is correct rather than a guess."""
    pid, first = rendered
    job = client.post(f"/v1/projects/{pid}/render",
                      json={"preset": "preview", "acknowledge_degradation": True}).json()
    second = wait_for_job(client, job["job_id"])["output"]

    assert second["cache_hit"] is True
    assert second["render_id"] == first["render_id"]


def test_render_reports_the_platform_attach_note(client, rendered):
    _pid, out = rendered
    kinds = [c["kind"] for c in out["degradation"]["compromises"]]
    assert "platform_attach" in kinds


def test_render_metadata_is_retrievable(client, rendered):
    _pid, out = rendered
    r = client.get(f"/v1/renders/{out['render_id']}").json()
    assert r["width"] == 540 and r["height"] == 960
    assert r["assignment"], "the assignment must be inspectable"
    assert all("reason" in a for a in r["assignment"])


def test_quota_is_enforced(client, small_media, monkeypatch):
    """Checked and incremented transactionally so concurrent requests cannot
    race past the limit."""
    from app import main

    monkeypatch.setitem(main.RENDER_QUOTAS, "pro", 1)
    pid = build_project(client, small_media)

    first = client.post(f"/v1/projects/{pid}/render",
                        json={"acknowledge_degradation": True})
    assert first.status_code == 202
    wait_for_job(client, first.json()["job_id"])

    second = client.post(f"/v1/projects/{pid}/render",
                         json={"acknowledge_degradation": True})
    assert second.status_code == 402
    assert "fix" in second.json()


# ---------------------------------------------------------------------------
# swapping -- the training signal
# ---------------------------------------------------------------------------


def test_alternatives_explain_their_ranking(client, rendered):
    """A user who can see why we ranked something makes a better-informed
    correction, and a better-informed correction is a better training label."""
    pid, _ = rendered
    alts = client.get(f"/v1/projects/{pid}/slots/0/alternatives").json()

    assert alts["alternatives"], "no candidates for slot 0"
    for a in alts["alternatives"]:
        assert a["reason"]
        assert a["breakdown"]
        assert 0 <= a["score"] <= 1
    ranks = [a["rank"] for a in alts["alternatives"]]
    assert ranks == sorted(ranks)


def _swappable_slot(client, pid: str) -> tuple[int, list[dict]]:
    """Find a slot with a real choice.

    Which *particular* slot has alternatives depends on how the synthetic
    fixture's shot scales happen to fall, and pinning that would make the test
    fragile for no benefit. What matters is that swapping works at all.
    """
    render = client.get(f"/v1/projects/{pid}").json()
    n = len(render["coverage_report"]["per_slot"])
    for slot in range(n):
        alts = client.get(f"/v1/projects/{pid}/slots/{slot}/alternatives").json()["alternatives"]
        if len(alts) >= 2:
            return slot, alts
    raise AssertionError("no slot had two candidates; the fixture is too thin to test swapping")


def test_swap_records_a_preference_pair(client, rendered):
    """Every swap is a labelled preference pair from a domain expert at peak
    engagement. That log is the matcher's training signal and the thing
    competitors cannot buy (docs/09 §6) — so it is written from day one."""
    pid, _ = rendered
    slot, alts = _swappable_slot(client, pid)

    before = client.get("/v1/usage").json()["swap_events"]
    other = next(a for a in alts if a["rank"] > 1)

    r = client.patch(f"/v1/projects/{pid}/assignment",
                     json={"slot": slot, "segment_id": other["segment_id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["segment_id"] == other["segment_id"]
    assert body["rank"] == other["rank"]
    # Only the swapped slot and its transition neighbours are dirty — a swap
    # must not force a full re-render, or iteration becomes too expensive to do.
    assert len(body["dirty_range"]) == 2

    after = client.get("/v1/usage").json()["swap_events"]
    assert after == before + 1, "the swap was not logged"


def test_swapped_clip_is_used_in_the_next_render(client, rendered):
    """A swap the renderer ignores is worse than no swap at all."""
    pid, _ = rendered
    slot, alts = _swappable_slot(client, pid)
    other = next(a for a in alts if a["rank"] > 1)

    client.patch(f"/v1/projects/{pid}/assignment",
                 json={"slot": slot, "segment_id": other["segment_id"]})

    job = client.post(f"/v1/projects/{pid}/render",
                      json={"preset": "preview", "acknowledge_degradation": True}).json()
    out = wait_for_job(client, job["job_id"])["output"]
    assert out["cache_hit"] is False, "a swap must invalidate the render cache"

    assignment = client.get(f"/v1/renders/{out['render_id']}").json()["assignment"]
    used = next(a for a in assignment if a["slot"] == slot)
    assert used["segment_id"] == other["segment_id"]


def test_swap_rejects_a_segment_that_cannot_fill_the_slot(client, rendered):
    pid, _ = rendered
    r = client.patch(f"/v1/projects/{pid}/assignment",
                     json={"slot": 0, "segment_id": "seg_does_not_exist"})
    assert r.status_code == 400
    assert "fix" in r.json()


def test_swap_before_render_is_refused_clearly(client, small_media):
    pid = build_project(client, small_media)
    r = client.patch(f"/v1/projects/{pid}/assignment",
                     json={"slot": 0, "segment_id": "x"})
    assert r.status_code == 400
    assert "render" in r.json()["fix"].lower()


# ---------------------------------------------------------------------------
# tenancy & misc
# ---------------------------------------------------------------------------


def test_unknown_ids_are_404_not_500(client):
    assert client.get("/v1/projects/nope").status_code == 404
    assert client.get("/v1/renders/nope").status_code == 404
    assert client.get("/v1/jobs/nope").status_code == 404


def test_usage_reports_quota_and_swap_count(client, small_media):
    build_project(client, small_media, index=False)
    u = client.get("/v1/usage").json()
    assert u["plan"] == "pro"
    assert u["renders_quota"] > 0
    assert u["projects"] >= 1
    assert "swap_events" in u


def test_storage_rejects_path_traversal(client):
    r = client.get("/v1/files/../../etc/passwd")
    assert r.status_code in (400, 404)
