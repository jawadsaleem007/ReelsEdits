"""API test fixtures.

Deliberately *small* media. The pipeline tests in `services/analyzer/tests` use
a 16-second reference and assert analysis accuracy against known ground truth;
these tests assert the API contract, so they use a 4-second reference and three
short clips and run in seconds. A test suite nobody waits for is a test suite
nobody runs.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

SMALL_SHOTS = [
    ("testsrc2=size=320x568:rate=25", 1.2, ""),
    ("smptebars=size=320x568:rate=25", 1.0, ""),
    ("rgbtestsrc=size=320x568:rate=25", 1.0, "hue=h=120"),
    ("mandelbrot=size=320x568:rate=25", 1.4, ""),
]

#: Five clips, not three. The swap tests need at least two viable candidates per
#: slot to exercise a real preference pair — with three clips the matcher's hard
#: constraints leave one candidate and the test silently skips, which is worse
#: than no test because it looks green.
SMALL_CLIPS = [
    ("wide", "testsrc2=size=360x640:rate=25", 3.0, "hue=h=30"),
    ("close", "rgbtestsrc=size=360x640:rate=25", 3.0, "crop=120:213:120:213,scale=360:640"),
    ("busy", "mandelbrot=size=360x640:rate=25", 3.0, "hue=h=200"),
    ("calm", "smptebars=size=360x640:rate=25", 3.0, "hue=h=90"),
    ("life", "life=size=360x640:rate=25:mold=8", 3.0, "hue=h=280"),
]


def _ff(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="session")
def small_media(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("apimedia")
    clips = root / "clips"
    clips.mkdir()

    parts = []
    for i, (src, dur, extra) in enumerate(SMALL_SHOTS):
        p = root / f"_s{i}.mp4"
        _ff(["-f", "lavfi", "-i", src, "-t", str(dur),
             "-vf", extra or "null", "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", "-r", "25", str(p)])
        parts.append(p)

    listing = root / "list.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    silent = root / "_silent.mp4"
    _ff(["-f", "concat", "-safe", "0", "-i", str(listing),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(silent)])

    # A 110 BPM click so beat tracking has something real to find.
    tone = root / "click.wav"
    _ff(["-f", "lavfi", "-i", "sine=frequency=60:duration=5",
         "-af", "atempo=1.0", str(tone)])

    reference = root / "reference.mp4"
    _ff(["-i", str(silent), "-i", str(tone), "-c:v", "copy", "-c:a", "aac",
         "-shortest", str(reference)])

    for name, src, dur, extra in SMALL_CLIPS:
        _ff(["-f", "lavfi", "-i", src, "-t", str(dur), "-vf", extra or "null",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-r", "25", str(clips / f"{name}.mp4")])

    return {"reference": reference, "clips": sorted(clips.glob("*.mp4")), "root": root}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient with an isolated database and storage root.

    `get_settings` is lru_cached, so the cache is cleared before the app's
    lifespan runs or every test would share the first test's database.
    """
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REELSEDITS_ENVIRONMENT", "local")
    monkeypatch.setenv("REELSEDITS_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("REELSEDITS_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("REELSEDITS_WORKER_CONCURRENCY", "2")

    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()


def wait_for_job(client, job_id: str, timeout: float = 60.0) -> dict:
    """Poll a job to completion.

    Returns the terminal payload; raises with the recorded error on failure so
    the test reports *why* rather than just timing out.
    """
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = client.get(f"/v1/jobs/{job_id}").json()
        if last["state"] == "complete":
            return last
        if last["state"] == "failed":
            raise AssertionError(f"job {job_id} failed: {last.get('error')}")
        time.sleep(0.4)
    raise AssertionError(f"job {job_id} timed out in stage {last.get('stage')!r}")


def build_project(client, media, *, index: bool = True) -> str:
    """Create a project, attach and analyse the reference, index the clips."""
    project = client.post("/v1/projects", json={"name": "test"}).json()
    pid = project["id"]

    with open(media["reference"], "rb") as f:
        ref = client.post("/v1/assets",
                          files={"file": ("reference.mp4", f, "video/mp4")},
                          data={"kind": "reference", "project_id": pid}).json()

    job = client.post(f"/v1/projects/{pid}/reference", json={"asset_id": ref["id"]}).json()
    wait_for_job(client, job["job_id"])

    for path in media["clips"]:
        with open(path, "rb") as f:
            client.post("/v1/assets",
                        files={"file": (path.name, f, "video/mp4")},
                        data={"kind": "clip", "project_id": pid})

    if index:
        job = client.post(f"/v1/projects/{pid}/index").json()
        wait_for_job(client, job["job_id"])

    return pid
