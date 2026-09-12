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


def test_pillarbox_verdict_needs_no_focal_point_and_defaults_to_centre(fixture_video):
    verdict = judge_mod.judge(
        fixture_video, SPAN, "forest",
        ask=asker({"accepted": True, "reason": "needs full width", "reframe": "pillarbox"}),
    )
    assert verdict.reframe == judge_mod.PILLARBOX
    assert verdict.focal_x == judge_mod.DEFAULT_FOCAL_X


@pytest.mark.parametrize("given,expected", [(0.0, 0.0), (1.0, 1.0), (-0.4, 0.0), (1.8, 1.0)])
def test_focal_x_boundaries_are_clamped_not_defaulted(fixture_video, given, expected):
    """0.0 is a legitimate focal point -- the far left edge. A falsy-zero fallback would
    silently recentre the crop and throw away the subject the model actually pointed at."""
    verdict = judge_mod.judge(
        fixture_video, SPAN, "forest",
        ask=asker({"accepted": True, "reason": "ok", "focal_x": given, "reframe": "crop"}),
    )
    assert verdict.focal_x == expected


@pytest.mark.parametrize("mode", ["crop", "pillarbox"])
def test_non_numeric_focal_x_is_a_reels_error_on_both_paths(fixture_video, mode):
    """On the pillarbox path this used to reach the clamp and raise a bare TypeError,
    which escapes the per-range handler and discards every range already accepted."""
    with pytest.raises(ReelsError, match="non-numeric"):
        judge_mod.judge(
            fixture_video, SPAN, "forest",
            ask=asker({"accepted": True, "reason": "ok", "focal_x": "left", "reframe": mode}),
        )


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


class Throttled(Exception):
    """Stands in for the client's rate-limit error, which exposes a numeric code."""

    code = 429


def test_a_transient_failure_is_retried_and_succeeds():
    """A live free-tier run lost two of six ranges to 503 UNAVAILABLE. A temporary
    provider blip must not discard the range as if the footage were unusable."""
    attempts = []
    naps = []

    def call():
        attempts.append(1)
        if len(attempts) < 3:
            raise Throttled("429 RESOURCE_EXHAUSTED")
        return "ok"

    assert judge_mod._retrying(call, sleep=naps.append) == "ok"
    assert len(attempts) == 3
    assert naps == [judge_mod.RETRY_BACKOFF, judge_mod.RETRY_BACKOFF * 2]


def test_a_persistent_outage_gives_up_after_the_attempt_budget():
    attempts = []

    def call():
        attempts.append(1)
        raise Throttled("429")

    with pytest.raises(Throttled):
        judge_mod._retrying(call, sleep=lambda _s: None)
    assert len(attempts) == judge_mod.RETRY_ATTEMPTS


def test_a_real_failure_is_not_retried():
    """Retrying a malformed request or a bad key just multiplies the wait."""
    attempts = []

    def call():
        attempts.append(1)
        raise ValueError("400 INVALID_ARGUMENT")

    with pytest.raises(ValueError):
        judge_mod._retrying(call, sleep=lambda _s: None)
    assert len(attempts) == 1


class Unavailable(Exception):
    """503 from shared free-tier capacity. A live run lost two of six ranges to this."""

    code = 503


@pytest.mark.parametrize("exc", [
    Throttled("boom"),                        # structural: code attribute
    Unavailable("boom"),
    RuntimeError("429 Too Many Requests"),    # textual: status in the message
    RuntimeError("RESOURCE_EXHAUSTED"),
    RuntimeError("rate limit exceeded"),
    RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand."),
])
def test_transient_failures_are_recognised_structurally_and_textually(exc):
    assert judge_mod._is_transient(exc)


@pytest.mark.parametrize("exc", [ValueError("400 bad request"), ValueError("401 unauthorized")])
def test_real_failures_are_not_mistaken_for_transient_ones(exc):
    assert not judge_mod._is_transient(exc)
