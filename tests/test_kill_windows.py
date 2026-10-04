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


def test_skip_drops_only_what_was_named(kh):
    ks = kills(kh, 10.0, 20.0, 30.0)
    kept = kh.drop_near(ks, [20.0])
    assert [k.start for k in kept] == [10.0, 30.0]


def test_skip_tolerates_a_timestamp_read_off_a_contact_sheet(kh):
    """The times come from a tile label rounded to 0.1s, not from the detector."""
    ks = kills(kh, 110.75)
    assert kh.drop_near(ks, [110.5]) == []


def test_skip_does_not_reach_a_neighbouring_kill(kh):
    """Kills 2.5s apart are common in a streak; skipping one must not take the next."""
    ks = kills(kh, 100.0, 102.5)
    kept = kh.drop_near(ks, [100.0])
    assert [k.start for k in kept] == [102.5]


def test_no_skip_list_changes_nothing(kh):
    ks = kills(kh, 10.0, 20.0)
    assert kh.drop_near(ks, []) == ks


def hot_reel(path, seconds=4):
    """A tiny vertical reel whose audio is full-scale noise: the worst case for peaks."""
    import subprocess

    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"color=c=black:s=108x192:r=30:d={seconds}", "-f", "lavfi", "-i",
         f"anoisesrc=amplitude=1.0:duration={seconds}", "-c:v", "libx264", "-c:a", "aac",
         "-shortest", str(path), "-y"], check=True)
    return path


def test_normalize_brings_a_hot_reel_under_the_platform_peak_limit(kh, tmp_path, true_peak):
    """Measured on real reels before this existed: +1.3, +1.4 and +3.2 dBTP, limit -1."""
    reel = hot_reel(tmp_path / "reel.mp4")
    assert true_peak(reel) > -1.0, "the fixture must start out over the limit"
    assert kh.normalize(reel) is True
    assert true_peak(reel) <= -1.0


def test_normalize_leaves_the_picture_alone(kh, tmp_path):
    import json
    import subprocess

    reel = hot_reel(tmp_path / "reel.mp4")
    kh.normalize(reel)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,width,height", "-of", "json", str(reel)],
        capture_output=True, text=True, check=True).stdout
    stream = json.loads(out)["streams"][0]
    assert (stream["codec_name"], stream["width"], stream["height"]) == ("h264", 108, 192)
    assert not list(tmp_path.glob("*.norm.*")), "the staging file must not be left behind"


def test_a_missing_tool_leaves_the_reel_untouched_and_says_so(kh, tmp_path, monkeypatch, capsys):
    reel = hot_reel(tmp_path / "reel.mp4")
    before = reel.read_bytes()
    monkeypatch.setattr(kh, "LOUDNESS_TOOL", tmp_path / "absent.py")
    assert kh.normalize(reel) is False
    assert reel.read_bytes() == before
    assert "NOT normalised" in capsys.readouterr().err


def test_a_failing_tool_leaves_the_reel_untouched_and_cleans_up(kh, tmp_path, monkeypatch, capsys):
    reel = hot_reel(tmp_path / "reel.mp4")
    before = reel.read_bytes()
    broken = tmp_path / "broken.py"
    broken.write_text("import sys; sys.stderr.write('ffmpeg: no such filter\\n'); sys.exit(1)\n")
    monkeypatch.setattr(kh, "LOUDNESS_TOOL", broken)
    assert kh.normalize(reel) is False
    assert reel.read_bytes() == before
    assert "no such filter" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.norm.*"))
