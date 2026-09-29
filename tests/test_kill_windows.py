"""Clip windows for the kill reel.

The script sits outside the package on purpose, so it is loaded by path rather than
imported. Only the pure window logic is exercised here -- detection needs real pixels.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "kill-highlights.py"


@pytest.fixture(scope="module")
def kh():
    spec = importlib.util.spec_from_file_location("kill_highlights", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves its own module through sys.modules, and
    # finds None there for a module loaded by path alone.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def kills(kh, *starts):
    return [kh.Kill(start=s, end=s + 1.0) for s in starts]


def test_a_streak_becomes_one_continuous_shot(kh):
    """Observed on a real match: a triple kill 5s apart was cut as three windows that
    overlapped and dropped 0.75s between each, so the streak played as three fragments."""
    spans = kh.windows(kills(kh, 366.25, 371.5, 376.75), pre=4.0, post=2.0, duration=1464.0)
    assert len(spans) == 1
    start, end = spans[0]
    assert start == pytest.approx(362.25) and end == pytest.approx(378.75)


def test_distant_kills_stay_separate(kh):
    spans = kh.windows(kills(kh, 100.0, 400.0), pre=4.0, post=2.0, duration=600.0)
    assert len(spans) == 2


def test_no_footage_is_dropped_between_merged_windows(kh):
    """The merged span must cover every second the separate windows would have."""
    starts = (50.0, 54.0, 58.0)
    spans = kh.windows(kills(kh, *starts), pre=4.0, post=2.0, duration=600.0)
    assert len(spans) == 1
    for s in starts:
        assert spans[0][0] <= s - 4.0 and spans[0][1] >= s + 2.0


def test_a_kill_in_the_opening_seconds_is_not_a_stub(kh):
    """Clamping start to 0 used to leave a short clip; the window extends forward."""
    spans = kh.windows(kills(kh, 1.5), pre=4.0, post=2.0, duration=600.0)
    assert spans[0][0] == 0.0
    assert spans[0][1] - spans[0][0] == pytest.approx(6.0), "full length, not 3.5s"


def test_a_kill_at_the_very_end_is_not_a_stub(kh):
    spans = kh.windows(kills(kh, 599.0), pre=4.0, post=2.0, duration=600.0)
    assert spans[0][1] == 600.0
    assert spans[0][1] - spans[0][0] == pytest.approx(6.0)


def test_a_recording_shorter_than_one_window_is_not_extended_past_itself(kh):
    spans = kh.windows(kills(kh, 2.0), pre=4.0, post=2.0, duration=3.0)
    assert spans[0][0] >= 0.0 and spans[0][1] <= 3.0


def test_gap_zero_still_merges_windows_that_actually_overlap(kh):
    """merge_gap=0 means "only when they touch", not "never": two windows that overlap
    must still become one, or the reel replays the overlapping seconds."""
    spans = kh.windows(kills(kh, 366.25, 371.5), pre=4.0, post=2.0, duration=1464.0,
                       merge_gap=0.0)
    assert len(spans) == 1, "these windows overlap by 0.75s"

    # Far enough apart that the windows do not touch: gap 0 leaves them alone.
    apart = kh.windows(kills(kh, 100.0, 110.0), pre=4.0, post=2.0, duration=600.0,
                       merge_gap=0.0)
    assert len(apart) == 2


def test_the_edge_threshold_scales_to_a_smaller_capture(kh):
    """The banner zone is a fraction of the frame, so it shrinks with the source while a
    fixed threshold does not. Unscaled, a 720p match reported 8 banners out of 30."""
    assert kh.scaled_edges(150, 1080) == 150
    assert kh.scaled_edges(150, 720) == 100
    assert kh.scaled_edges(150, 2160) == 300


def test_an_unreadable_height_leaves_the_threshold_alone(kh):
    assert kh.scaled_edges(150, 0) == 150
    assert kh.scaled_edges(150, -1) == 150


def test_scaling_never_reaches_zero(kh):
    """A threshold of 0 would mark every frame hot and detect one endless banner."""
    assert kh.scaled_edges(150, 1) >= 1


def test_only_the_default_is_scaled(kh):
    """A number measured on the operator's own 720p footage must not be scaled again:
    --min-edges 90 quietly becoming 60 is worse than no scaling at all."""
    import inspect

    src = inspect.getsource(kh.detect)
    assert "if min_edges is None" in src, "an explicit value must bypass scaling"
    assert "scaled_edges(MIN_EDGES, h)" in src, "the default is what gets scaled"


def spans_of(total, n=4):
    each = total / n
    return [(i * each * 2, i * each * 2 + each) for i in range(n)]


def test_target_picks_the_factor_that_fits(kh):
    assert kh.speed_for(spans_of(204.0), target=90.0, speed=1.0) == pytest.approx(204 / 90)


def test_a_target_never_slows_footage_down_to_pad_it(kh):
    """A target is a ceiling on length. Stretching a short reel to reach it is not what
    was asked, and would make every kill drag."""
    assert kh.speed_for(spans_of(40.0), target=90.0, speed=1.0) == 1.0


def test_an_explicit_speed_is_used_when_no_target_is_given(kh):
    assert kh.speed_for(spans_of(204.0), target=None, speed=1.5) == 1.5


def test_a_useless_target_falls_back_rather_than_dividing_by_zero(kh):
    assert kh.speed_for(spans_of(204.0), target=0.0, speed=1.0) == 1.0
    assert kh.speed_for([], target=90.0, speed=1.0) == 1.0


def test_atempo_is_chained_past_what_one_stage_accepts(kh):
    """atempo errors out above 2.0 rather than clamping, so a big factor must be stages."""
    chain = kh.atempo_chain(2.2769)
    assert chain.count("atempo=") == 2
    product = 1.0
    for stage in chain.split(","):
        product *= float(stage.split("=")[1])
    assert product == pytest.approx(2.2769, rel=1e-4)


def test_every_atempo_stage_is_one_ffmpeg_accepts(kh):
    for speed in (1.0, 1.9, 2.0, 2.27, 4.0, 7.5, 0.6):
        for stage in kh.atempo_chain(speed).split(","):
            assert 0.5 <= float(stage.split("=")[1]) <= 2.0, (speed, stage)


def test_a_chain_multiplies_back_to_the_factor_asked_for(kh):
    for speed in (1.0, 1.5, 2.27, 4.0, 9.0):
        product = 1.0
        for stage in kh.atempo_chain(speed).split(","):
            product *= float(stage.split("=")[1])
        assert product == pytest.approx(speed, rel=1e-4)
