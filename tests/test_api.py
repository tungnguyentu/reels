"""V2 -- the local HTTP API behind the review UI."""
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from reels import api as api_mod
from reels import index as index_mod
from reels import judge as judge_mod
from reels.api import Settings, create_app


@pytest.fixture
def client(tmp_path, fixture_video, fake_encoder, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    shutil.copy(fixture_video, library / fixture_video.name)
    music = tmp_path / "music"
    music.mkdir()
    (music / "tune.mp3").write_bytes(b"not really audio")
    monkeypatch.setattr(index_mod, "encode_images", fake_encoder)
    app = create_app(Settings(library=library, music_dir=music, out_dir=tmp_path / "out"))
    return TestClient(app), library / fixture_video.name


def wait(c, job_id, timeout=120.0):
    """Poll on a wall-clock budget. A tight loop elapses in under a second while a real
    index takes a couple, so a count-based bound just fails on fast machines."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = c.get(f"/api/jobs/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} still running after {timeout}s")


def test_library_reports_indexed_state(client):
    c, video = client
    listed = c.get("/api/library").json()
    assert [v["name"] for v in listed] == [video.name]
    assert listed[0]["indexed"] is False


def test_search_before_indexing_is_a_conflict_not_a_crash(client):
    c, video = client
    assert c.post("/api/search", json={"video": video.name, "query": "forest"}).status_code == 409


def test_index_then_search_returns_candidates(client, monkeypatch):
    c, video = client
    import numpy as np

    from reels import search as search_mod
    monkeypatch.setattr(search_mod, "encode_text", lambda q: np.ones(8, dtype="float32"))

    job = c.post("/api/index", json={"video": video.name}).json()["job"]
    assert wait(c, job)["status"] == "done"
    assert c.get("/api/library").json()[0]["indexed"] is True

    body = c.post("/api/search", json={
        "video": video.name, "query": "forest", "floor": 0.0, "min_seconds": 0.0,
    }).json()
    assert body["candidates"]
    assert body["peak"] > 0
    assert all(x["end"] > x["start"] for x in body["candidates"])


def test_search_reports_the_peak_when_nothing_clears_the_floor(client, monkeypatch):
    """The UI needs the number to tell 'not here' from 'just under the line'."""
    c, video = client
    import numpy as np

    from reels import search as search_mod
    monkeypatch.setattr(search_mod, "encode_text", lambda q: np.ones(8, dtype="float32"))
    wait(c, c.post("/api/index", json={"video": video.name}).json()["job"])

    body = c.post("/api/search", json={"video": video.name, "query": "x", "floor": 99.0}).json()
    assert body["candidates"] == []
    assert body["peak"] < body["floor"]


def test_thumbnails_are_jpeg(client):
    c, video = client
    r = c.get("/api/thumb", params={"video": video.name, "t": 10.0})
    assert r.status_code == 200
    assert r.content.startswith(b"\xff\xd8")


@pytest.mark.parametrize("attempt", ["../../../etc/passwd", "/etc/passwd", "..%2f..%2fetc/passwd"])
def test_paths_outside_the_library_are_refused(client, attempt):
    """The browser can ask for any path; membership is checked after resolving."""
    c, _ = client
    assert c.get("/api/thumb", params={"video": attempt, "t": 1.0}).status_code in (400, 404)
    assert c.post("/api/search", json={"video": attempt, "query": "x"}).status_code in (400, 404)


def test_media_route_will_not_serve_outside_the_output_dir(client):
    c, _ = client
    assert c.get("/media/clip", params={"name": "../../etc/passwd"}).status_code in (400, 404)


def test_judge_failure_becomes_a_verdict_row_not_a_dead_job(client, monkeypatch):
    """One unjudgeable range must not sink the others -- same contract as the CLI."""
    c, video = client
    from reels import ReelsError

    calls = {"n": 0}

    def flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ReelsError("GEMINI_API_KEY is not set.")
        return judge_mod.Verdict(True, "vista", 0.4, "crop")

    monkeypatch.setattr(judge_mod, "judge", flaky)
    job = c.post("/api/judge", json={
        "video": video.name, "query": "forest",
        "ranges": [{"start": 10, "end": 26}, {"start": 30, "end": 46}],
    }).json()["job"]
    rows = wait(c, job)["result"]
    assert rows[0]["accepted"] is None and "GEMINI_API_KEY" in rows[0]["reason"]
    assert rows[1]["accepted"] is True


def test_a_failing_render_reports_the_reason_rather_than_hanging(client):
    c, video = client
    job = c.post("/api/render", json={
        "video": video.name, "query": "forest",
        "range": {"start": 10.0, "end": 26.0},
        "treatments": [{"reframe": "crop", "focal_x": 0.5, "track": "nope.mp3"}],
    }).json()["job"]
    body = wait(c, job)
    assert body["status"] == "failed"
    assert "no such track" in body["error"]


def test_packaging_covers_and_export_work_without_a_key(client, monkeypatch):
    c, video = client
    out_dir = c.app.state.reels.settings.out_dir
    out_dir.mkdir()
    clip = out_dir / "clip.mp4"
    shutil.copy(video, clip)
    monkeypatch.delenv(judge_mod.API_KEY_ENV, raising=False)

    covers_job = c.post("/api/package/covers", json={
        "clip": clip.name, "cover_text": "Forest view", "count": 1,
    }).json()["job"]
    covers = wait(c, covers_job)
    assert covers["status"] == "done"
    cover = covers["result"][0]
    export_job = c.post("/api/package/export", json={
        "clip": clip.name, "title": "Forest view", "description": "", "cover": cover,
        "titles": [{"title": "Forest view", "cover_text": "Canopy", "reason": "frame", "frame_index": 0}],
        "covers": [cover],
    }).json()["job"]
    exported = wait(c, export_job)
    assert exported["status"] == "done", exported["error"]
    bundle = out_dir / "clip.package"
    assert (bundle / "README.md").is_file()
    assert "Forest view" in (bundle / "alternatives" / "titles.md").read_text(encoding="utf-8")


def test_unknown_job_is_404(client):
    c, _ = client
    assert c.get("/api/jobs/deadbeef").status_code == 404


def test_config_exposes_defaults_and_judge_availability(client, monkeypatch):
    c, _ = client
    monkeypatch.delenv(judge_mod.API_KEY_ENV, raising=False)
    body = c.get("/api/config").json()
    assert body["judge_available"] is False
    assert body["reframe_modes"] == ["crop", "pillarbox"]
    assert body["defaults"]["shortlist"] > 0


def test_treatment_focal_x_is_bounded_by_the_schema(client):
    c, video = client
    r = c.post("/api/render", json={
        "video": video.name, "query": "f", "range": {"start": 1.0, "end": 5.0},
        "treatments": [{"reframe": "crop", "focal_x": 4.2}],
    })
    assert r.status_code == 422


def test_preview_encodes_a_small_clip_and_caches_it(client):
    c, video = client
    r = c.get("/media/preview", params={"video": video.name, "start": 10.0, "end": 20.0})
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    first = len(r.content)
    again = c.get("/media/preview", params={"video": video.name, "start": 10.0, "end": 20.0})
    assert len(again.content) == first  # served from cache, byte-identical


def test_preview_length_is_capped(client):
    """A candidate cannot ask the server to transcode the whole recording."""
    from reels.api import PREVIEW_MAX_SECONDS

    c, video = client
    r = c.get("/media/preview", params={"video": video.name, "start": 0.0, "end": 99999.0})
    assert r.status_code == 200
    assert PREVIEW_MAX_SECONDS <= 60.0


def test_preview_refuses_paths_outside_the_library(client):
    c, _ = client
    r = c.get("/media/preview", params={"video": "../../../etc/passwd", "start": 0.0, "end": 5.0})
    assert r.status_code in (400, 404)


def test_a_signed_in_agy_counts_as_an_available_judge(client, monkeypatch):
    """The UI greys its judge button on this flag. A key is not the only way in."""
    from reels import agy
    from reels import judge as judge_mod

    c, _ = client
    monkeypatch.delenv(judge_mod.API_KEY_ENV, raising=False)
    monkeypatch.setenv(judge_mod.BACKEND_ENV, "agy")
    monkeypatch.setattr(agy, "available", lambda: True)
    body = c.get("/api/config").json()
    assert body["judge_available"] is True
    assert body["judge_backend"] == "agy"

    monkeypatch.setattr(agy, "available", lambda: False)
    assert c.get("/api/config").json()["judge_available"] is False


def test_the_ui_judges_a_batch_in_one_backend_call(client, monkeypatch):
    """Each agy invocation re-reads its harness context, so per-range calls cost ~6x."""
    from reels import judge as judge_mod
    from reels.judge import Verdict

    c, video = client
    calls = []

    def judge_all(v, candidates, query):
        calls.append(len(candidates))
        return [Verdict(True, "ok", 0.5, "crop") for _ in candidates]

    monkeypatch.setattr(judge_mod, "judge_all", judge_all)
    ranges = [{"start": 10.0, "end": 26.0}, {"start": 40.0, "end": 56.0}]
    job = c.post("/api/judge", json={"video": video.name, "query": "trees",
                                     "ranges": ranges}).json()["job"]
    out = wait(c, job)["result"]
    assert calls == [2], "two ranges must reach the backend as one batch"
    assert [r["accepted"] for r in out] == [True, True]


def test_finding_no_kills_is_a_result_not_a_failed_job(client):
    """The fixture is forest footage: no killfeed. That is an answer, not an error."""
    c, video = client
    job = c.post("/api/kills", json={"video": video.name}).json()["job"]
    done = wait(c, job, timeout=300.0)
    assert done["status"] == "done", done.get("error")
    assert done["result"] == {"kills": [], "clip": None}


def test_a_broken_detector_run_becomes_a_failed_job_with_its_reason(client, monkeypatch):
    c, video = client

    def boom(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "ffmpeg: no such filter\n")

    monkeypatch.setattr(api_mod.subprocess, "run", boom)
    job = c.post("/api/kills", json={"video": video.name}).json()["job"]
    done = wait(c, job)
    assert done["status"] == "failed"
    assert "no such filter" in done["error"]


def test_kill_detection_stays_inside_the_configured_roots(client):
    c, video = client
    assert c.post("/api/kills", json={"video": "../../etc/passwd"}).status_code == 400
    assert c.post("/api/kills",
                  json={"video": video.name, "music": "../../etc/passwd"}).status_code == 400
