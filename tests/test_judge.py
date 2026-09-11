"""U3 -- vision-model shot judge. Covers AE1, AE3."""
import pytest

from reels import ReelsError
from reels import judge as judge_mod
from reels.search import Candidate

SPAN = Candidate(start=10.0, end=40.0, peak=0.31)


def asker(payload):
    def ask(_frames, _prompt):
        return payload
    return ask


def test_inventory_overlay_is_rejected_with_a_named_reason(fixture_video):
    """Covers AE1. Forest behind a chest UI is still a rejection."""
    verdict = judge_mod.judge(
        fixture_video, SPAN, "forest landscape",
        ask=asker({"accepted": False, "reason": "a large chest inventory screen covers the frame"}),
    )
    assert not verdict.accepted
    assert "inventory" in verdict.reason


def test_accepted_span_returns_focal_x_in_range(fixture_video):
    """Covers AE3."""
    verdict = judge_mod.judge(
        fixture_video, SPAN, "forest landscape",
        ask=asker({"accepted": True, "reason": "wide vista", "focal_x": 0.31, "reframe": "crop"}),
    )
    assert verdict.accepted
    assert 0.0 <= verdict.focal_x <= 1.0


def test_out_of_range_focal_x_is_clamped(fixture_video):
    verdict = judge_mod.judge(
        fixture_video, SPAN, "forest",
        ask=asker({"accepted": True, "reason": "ok", "focal_x": 1.8, "reframe": "crop"}),
    )
    assert verdict.focal_x == 1.0


def test_accepted_span_returns_exactly_one_reframe_mode(fixture_video):
    verdict = judge_mod.judge(
        fixture_video, SPAN, "forest",
        ask=asker({"accepted": True, "reason": "needs full width", "reframe": "pillarbox"}),
    )
    assert verdict.reframe in (judge_mod.CROP, judge_mod.PILLARBOX)


def test_near_field_closeup_is_rejected(fixture_video):
    verdict = judge_mod.judge(
        fixture_video, SPAN, "forest landscape",
        ask=asker({"accepted": False, "reason": "camera pressed into foliage, no horizon"}),
    )
    assert not verdict.accepted


def test_missing_api_key_names_the_variable(fixture_video, monkeypatch):
    monkeypatch.delenv(judge_mod.API_KEY_ENV, raising=False)
    with pytest.raises(ReelsError, match=judge_mod.API_KEY_ENV):
        judge_mod.judge(fixture_video, SPAN, "forest")


def test_request_failure_surfaces_and_is_not_a_rejection(fixture_video):
    def boom(_frames, _prompt):
        raise ReelsError("the vision model request failed: 503")

    with pytest.raises(ReelsError, match="503"):
        judge_mod.judge(fixture_video, SPAN, "forest", ask=boom)


@pytest.mark.parametrize("payload", [
    {"reason": "no verdict field"},
    {"accepted": True, "reason": "accepted with no reframe mode"},
    {"accepted": True, "reason": "crop with no focal point", "reframe": "crop"},
    "not a dict at all",
])
def test_malformed_response_is_an_error_not_a_rejection(fixture_video, payload):
    with pytest.raises(ReelsError):
        judge_mod.judge(fixture_video, SPAN, "forest", ask=asker(payload))


def test_sample_frames_reads_inside_the_range(fixture_video):
    frames = judge_mod.sample_frames(fixture_video, Candidate(10.0, 40.0, 0.3), count=3)
    assert len(frames) == 3
    assert all(f.startswith(b"\xff\xd8") for f in frames)  # JPEG magic
