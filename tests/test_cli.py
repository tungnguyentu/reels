"""U5 -- CLI wiring and the empty-result path. Covers F1, F2, F3, AE5, AE6."""
import subprocess

import pytest

from reels import cli, search
from reels import index as index_mod
from reels import judge as judge_mod
from reels import render as render_mod
from reels.judge import CROP, Verdict


@pytest.fixture
def stub_pipeline(monkeypatch, fake_encoder):
    """Everything below the CLI stubbed, so these tests exercise wiring and not ffmpeg."""
    monkeypatch.setattr(index_mod, "encode_images", fake_encoder)
    monkeypatch.setattr(search, "encode_text", lambda q: __import__("numpy").ones(8, dtype="float32"))


@pytest.fixture
def music_dir(tmp_path):
    folder = tmp_path / "music"
    folder.mkdir()
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         str(folder / "tune.mp3"), "-y"], check=True,
    )
    return folder


def clip_args(video, music_dir, out_dir, query="forest landscape", **overrides):
    argv = ["clip", str(video), query, "--music-dir", str(music_dir), "--out-dir", str(out_dir)]
    for key, value in overrides.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    return argv


def test_clip_indexes_an_unindexed_recording_then_produces_a_clip(
    fixture_video, music_dir, tmp_path, stub_pipeline, monkeypatch
):
    """Covers F1."""
    monkeypatch.setattr(
        judge_mod, "judge",
        lambda *a, **k: Verdict(True, "wide vista", 0.5, CROP),
    )
    rendered = []
    monkeypatch.setattr(
        render_mod, "render",
        lambda *a, **k: rendered.append(1) or (tmp_path / "out.mp4"),
    )
    assert not index_mod.index_path(fixture_video).exists()
    assert cli.main(clip_args(fixture_video, music_dir, tmp_path, floor=0.0, min_seconds=0.0)) == 0
    assert index_mod.index_path(fixture_video).is_file()
    assert rendered


def test_clip_on_an_indexed_recording_skips_indexing(
    fixture_video, music_dir, tmp_path, stub_pipeline, monkeypatch
):
    """Covers F2, AE5."""
    index_mod.build_index(fixture_video)

    def explode(*_a, **_k):
        raise AssertionError("re-indexed an already-indexed recording")

    monkeypatch.setattr(index_mod, "extract_keyframes", explode)
    monkeypatch.setattr(judge_mod, "judge", lambda *a, **k: Verdict(False, "inventory screen"))
    assert cli.main(clip_args(fixture_video, music_dir, tmp_path, floor=0.0, min_seconds=0.0)) == 0


def test_every_candidate_rejected_writes_nothing_and_explains(
    fixture_video, music_dir, tmp_path, stub_pipeline, monkeypatch, capsys
):
    """Covers F3, AE6."""
    monkeypatch.setattr(
        judge_mod, "judge",
        lambda *a, **k: Verdict(False, "a large chest inventory screen covers the frame"),
    )
    out_dir = tmp_path / "out"
    assert cli.main(clip_args(fixture_video, music_dir, out_dir, floor=0.0, min_seconds=0.0)) == 0
    assert not out_dir.exists() or not list(out_dir.glob("*.mp4"))
    assert "inventory" in capsys.readouterr().err


def test_empty_shortlist_never_calls_the_judge(
    fixture_video, music_dir, tmp_path, stub_pipeline, monkeypatch, capsys
):
    """The floor rejects before any API call is spent."""
    def explode(*_a, **_k):
        raise AssertionError("called the judge with no candidates")

    monkeypatch.setattr(judge_mod, "judge", explode)
    assert cli.main(clip_args(fixture_video, music_dir, tmp_path, floor=1.5)) == 0
    assert "no candidates" in capsys.readouterr().err


def test_one_failed_range_does_not_discard_the_others(
    fixture_video, music_dir, tmp_path, stub_pipeline, monkeypatch, capsys
):
    from reels import ReelsError

    calls = {"n": 0}

    def flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ReelsError("the vision model request failed: 503")
        return Verdict(True, "vista", 0.5, CROP)

    monkeypatch.setattr(judge_mod, "judge", flaky)
    monkeypatch.setattr(render_mod, "render", lambda *a, **k: tmp_path / "out.mp4")
    argv = clip_args(fixture_video, music_dir, tmp_path, floor=0.0, min_seconds=0.0, shortlist=3)
    assert cli.main(argv) == 0
    assert "could not be judged" in capsys.readouterr().err


def test_unreadable_input_exits_nonzero_with_a_clear_error(tmp_path, music_dir, capsys):
    missing = tmp_path / "nope.mp4"
    assert cli.main(clip_args(missing, music_dir, tmp_path)) == 1
    assert "not a file" in capsys.readouterr().err


def test_index_command_reports_and_is_idempotent(fixture_video, stub_pipeline, capsys):
    assert cli.main(["index", str(fixture_video)]) == 0
    assert "indexed" in capsys.readouterr().err
    assert cli.main(["index", str(fixture_video)]) == 0
    assert "already indexed" in capsys.readouterr().err


def test_every_candidate_erroring_exits_nonzero(
    fixture_video, music_dir, tmp_path, stub_pipeline, monkeypatch, capsys
):
    """An unset API key or no network is a failed run, not a recording that happens to
    contain nothing -- exiting 0 tells a caller the clip search genuinely came up empty."""
    from reels import ReelsError

    def no_key(*_a, **_k):
        raise ReelsError("GEMINI_API_KEY is not set.")

    monkeypatch.setattr(judge_mod, "judge", no_key)
    assert cli.main(clip_args(fixture_video, music_dir, tmp_path, floor=0.0, min_seconds=0.0)) == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().err


def test_missing_music_dir_fails_before_any_judge_call(
    fixture_video, tmp_path, stub_pipeline, monkeypatch, capsys
):
    def explode(*_a, **_k):
        raise AssertionError("spent a vision-model call before validating the music dir")

    monkeypatch.setattr(judge_mod, "judge", explode)
    argv = clip_args(fixture_video, tmp_path / "absent", tmp_path, floor=0.0, min_seconds=0.0)
    assert cli.main(argv) == 1
    assert "music directory not found" in capsys.readouterr().err


def test_index_force_rebuilds(fixture_video, stub_pipeline, capsys):
    assert cli.main(["index", str(fixture_video)]) == 0
    capsys.readouterr()
    assert cli.main(["index", str(fixture_video), "--force"]) == 0
    err = capsys.readouterr().err
    assert "indexed" in err and "already indexed" not in err


def test_empty_result_reports_the_observed_peak(
    fixture_video, music_dir, tmp_path, stub_pipeline, capsys
):
    """'Nothing matched' is only trustworthy if it says how close it came -- the floor is
    sensitive to query phrasing, so the operator needs the number to judge it."""
    assert cli.main(clip_args(fixture_video, music_dir, tmp_path, floor=1.5)) == 0
    assert "below the 1.5 floor" in capsys.readouterr().err


def test_ui_command_builds_an_app_and_serves_on_loopback(tmp_path, monkeypatch):
    """Never bind a public interface: this serves local media by path."""
    served = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(kw))
    assert cli.main([
        "ui", "--no-browser", "--library", str(tmp_path),
        "--music-dir", str(tmp_path), "--out-dir", str(tmp_path), "--port", "9999",
    ]) == 0
    assert served["host"] == "127.0.0.1"
    assert served["port"] == 9999
