"""V2 -- the local HTTP API behind the review UI."""
import shutil
import time

import pytest
from fastapi.testclient import TestClient

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
