"""The agy judge backend: batching, parsing, and failure shapes."""
import json
import subprocess

import pytest

from reels import ReelsError, agy
from reels.judge import CROP, PILLARBOX
from reels.search import Candidate

SPANS = [Candidate(10.0, 26.0, 0.3), Candidate(40.0, 56.0, 0.3), Candidate(60.0, 76.0, 0.3)]


REAL_RUN = subprocess.run


def fake_run(payload, returncode=0, stdout=None):
    """Stand in for the agy subprocess, capturing what it was asked.

    Anything that is not agy -- the ffmpeg calls that cut the frames agy is shown -- is
    delegated to the real subprocess.run, so these tests exercise real frame extraction
    from the fixture and only the model call is stubbed.
    """
    calls = []

    def run(cmd, **kw):
        if not cmd or cmd[0] != agy.BINARY:
            return REAL_RUN(cmd, **kw)
        calls.append(cmd)
        body = stdout if stdout is not None else json.dumps({"structured_output": payload})
        return subprocess.CompletedProcess(cmd, returncode, body, "")

    run.calls = calls
    return run


def verdicts(*rows):
    return {"verdicts": list(rows)}


def test_one_call_judges_many_ranges(fixture_video, monkeypatch):
    """agy carries its harness context per invocation, so per-range calls cost ~6x."""
    run = fake_run(verdicts(
        {"range": 0, "accepted": True, "reason": "vista", "focal_x": 0.3, "reframe": "crop"},
        {"range": 1, "accepted": False, "reason": "inventory screen"},
        {"range": 2, "accepted": True, "reason": "wide", "reframe": "pillarbox"},
    ))
    monkeypatch.setattr(subprocess, "run", run)
    out = agy.judge_ranges(fixture_video, SPANS, "forest")
    assert len(run.calls) == 1, "three ranges must not become three agy invocations"
    assert [v.accepted for v in out] == [True, False, True]
    assert out[0].focal_x == 0.3 and out[0].reframe == CROP
    assert out[2].reframe == PILLARBOX


def test_verdicts_are_returned_in_candidate_order(fixture_video, monkeypatch):
    """The model may answer out of order; `range` is what binds a verdict to its span."""
    monkeypatch.setattr(subprocess, "run", fake_run(verdicts(
        {"range": 2, "accepted": True, "reason": "third", "focal_x": 0.9, "reframe": "crop"},
        {"range": 0, "accepted": True, "reason": "first", "focal_x": 0.1, "reframe": "crop"},
        {"range": 1, "accepted": False, "reason": "second"},
    )))
    out = agy.judge_ranges(fixture_video, SPANS, "forest")
    assert [v.reason for v in out] == ["first", "second", "third"]


def test_ranges_beyond_the_batch_size_use_more_calls(fixture_video, monkeypatch):
    many = SPANS * 4  # 12 spans, batch size 8
    rows = [{"range": i, "accepted": False, "reason": "no"} for i in range(agy.BATCH_RANGES)]
    run = fake_run(verdicts(*rows))
    monkeypatch.setattr(subprocess, "run", run)
    out = agy.judge_ranges(fixture_video, many, "forest")
    assert len(run.calls) == 2, "12 spans at batch 8 should be two calls"
    assert len(out) == len(many), "every span needs a verdict across batch boundaries"


def test_a_missing_verdict_is_named_not_silently_dropped(fixture_video, monkeypatch):
    """A span with no answer must not be mistaken for a span judged unusable."""
    monkeypatch.setattr(subprocess, "run", fake_run(verdicts(
        {"range": 0, "accepted": True, "reason": "ok", "focal_x": 0.5, "reframe": "crop"},
    )))
    with pytest.raises(ReelsError, match="no verdict for 2 of 3"):
        agy.judge_ranges(fixture_video, SPANS, "forest")


def test_a_lapsed_login_is_reported_as_such(fixture_video, monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        fake_run(None, stdout="Error: authentication required. Run 'agy'."))
    with pytest.raises(ReelsError, match="sign in"):
        agy.judge_ranges(fixture_video, SPANS[:1], "forest")


def test_a_nonzero_exit_surfaces(fixture_video, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(None, returncode=1, stdout="boom"))
    with pytest.raises(ReelsError, match="failed"):
        agy.judge_ranges(fixture_video, SPANS[:1], "forest")


def test_output_without_structured_data_is_an_error(fixture_video, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(None, stdout=json.dumps({"status": "ok"})))
    with pytest.raises(ReelsError, match="no structured output"):
        agy.judge_ranges(fixture_video, SPANS[:1], "forest")


def test_non_numeric_focal_point_is_an_error(fixture_video, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(verdicts(
        {"range": 0, "accepted": True, "reason": "ok", "focal_x": "left", "reframe": "crop"},
    )))
    with pytest.raises(ReelsError, match="non-numeric"):
        agy.judge_ranges(fixture_video, SPANS[:1], "forest")


def test_the_invocation_is_sandboxed_and_schema_bound(fixture_video, monkeypatch):
    run = fake_run(verdicts({"range": 0, "accepted": False, "reason": "no"}))
    monkeypatch.setattr(subprocess, "run", run)
    agy.judge_ranges(fixture_video, SPANS[:1], "forest")
    cmd = run.calls[0]
    # --dangerously-skip-permissions is required in headless mode; --sandbox is what keeps
    # that from also handing over terminal access.
    assert "--sandbox" in cmd and "--dangerously-skip-permissions" in cmd
    assert "--json-schema" in cmd and "--output-format" in cmd


def test_no_candidates_makes_no_call(fixture_video, monkeypatch):
    run = fake_run(verdicts())
    monkeypatch.setattr(subprocess, "run", run)
    assert agy.judge_ranges(fixture_video, [], "forest") == []
    assert not run.calls


def test_the_timeout_is_configurable(fixture_video, monkeypatch):
    """A stalled provider is indistinguishable from a slow one, so the wait is tunable."""
    monkeypatch.setenv(agy.TIMEOUT_ENV, "30")
    seen = {}

    def run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps({"structured_output": verdicts(
                {"range": 0, "accepted": False, "reason": "no"})}), "")

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
                        run(cmd, **kw) if cmd[0] == agy.BINARY else REAL_RUN(cmd, **kw))
    agy.judge_ranges(fixture_video, SPANS[:1], "forest")
    assert seen["timeout"] == 30


def test_an_unusable_timeout_is_rejected(monkeypatch):
    monkeypatch.setenv(agy.TIMEOUT_ENV, "soon")
    with pytest.raises(ReelsError, match="number of seconds"):
        agy.timeout_seconds()
    monkeypatch.setenv(agy.TIMEOUT_ENV, "0")
    with pytest.raises(ReelsError, match="greater than zero"):
        agy.timeout_seconds()


def test_a_timeout_says_retrying_may_be_enough(fixture_video, monkeypatch):
    """Observed live: one batch stalled past 900s, the identical call then took 36s."""
    def run(cmd, **kw):
        if cmd[0] != agy.BINARY:
            return REAL_RUN(cmd, **kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ReelsError, match="retrying"):
        agy.judge_ranges(fixture_video, SPANS[:1], "forest")


def test_the_prompt_names_no_particular_game(fixture_video):
    """It is shown Call of Duty as readily as Minecraft; naming one misleads the model."""
    from reels.judge import PROMPT

    filled = PROMPT.format(query="a kill")
    for word in ("minecraft", "crafting table", "furnace", "block"):
        assert word not in filled.lower(), f"{word!r} presumes one game"
