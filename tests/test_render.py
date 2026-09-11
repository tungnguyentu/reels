"""U4 -- reframe, music, encode. Covers AE2, AE3, AE4."""
import json
import subprocess
from pathlib import Path

import pytest

from reels import ReelsError
from reels import render as rnd
from reels.judge import CROP, PILLARBOX, Verdict
from reels.search import Candidate


def probe(path, entries, stream="v:0"):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", entries,
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def make_track(path, seconds=6.0, freq=440):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         str(path), "-y"], check=True,
    )
    return path


@pytest.fixture
def music_dir(tmp_path):
    folder = tmp_path / "music"
    folder.mkdir()
    make_track(folder / "short.mp3", seconds=6.0)
    return folder


# --- library selection -------------------------------------------------------------

def test_music_dir_holding_recordings_selects_only_audio(tmp_path, fixture_video):
    folder = tmp_path / "mixed"
    folder.mkdir()
    make_track(folder / "tune.mp3")
    (folder / "recording.mp4").write_bytes(fixture_video.read_bytes())
    assert [p.name for p in rnd.music_tracks(folder)] == ["tune.mp3"]


def test_m4a_only_directory_still_yields_a_track(tmp_path):
    folder = tmp_path / "m4a"
    folder.mkdir()
    make_track(folder / "only.m4a")
    assert len(rnd.music_tracks(folder)) == 1


def test_empty_and_missing_music_directories_error_clearly(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ReelsError, match="no audio files"):
        rnd.music_tracks(empty)
    with pytest.raises(ReelsError, match="not found"):
        rnd.music_tracks(tmp_path / "absent")


def test_track_choice_is_deterministic_and_spreads(tmp_path):
    tracks = [Path(f"{i}.mp3") for i in range(10)]
    # Same seed, same track -- a re-render of one span keeps its music.
    assert rnd.pick_track(tracks, "a|b|1.0") == rnd.pick_track(tracks, "a|b|1.0")
    # Different seeds spread over the library. Any two seeds may collide by chance,
    # so assert the spread rather than a single inequality.
    chosen = {rnd.pick_track(tracks, f"clip|forest|{i}").name for i in range(50)}
    assert len(chosen) >= 7


# --- reframe geometry --------------------------------------------------------------

def test_crop_window_is_nine_by_sixteen_and_clamped_at_the_edges():
    for focal in (0.0, 0.05, 0.5, 0.95, 1.0):
        width, x = rnd.crop_window(3410, 1372, focal)
        assert x >= 0 and x + width <= 3410
        assert abs(width / 1372 - 9 / 16) < 0.01


def test_crop_window_centres_on_the_focal_point_when_it_fits():
    width, x = rnd.crop_window(3410, 1372, 0.5)
    assert abs((x + width / 2) - 1705) < 2


def test_pillarbox_filter_scales_the_full_width_into_the_frame():
    graph = rnd.video_filter(Verdict(True, "wide", None, PILLARBOX), 3410, 1372)
    assert f"scale={rnd.OUTPUT_WIDTH}:-2" in graph
    assert "gblur" in graph


def test_reframe_without_a_mode_is_an_error():
    with pytest.raises(ReelsError):
        rnd.video_filter(Verdict(True, "?", 0.5, None), 3410, 1372)


def test_output_path_never_overwrites(tmp_path, fixture_video):
    first = rnd.output_path(tmp_path, fixture_video, "forest landscape", 12.0)
    first.write_bytes(b"x")
    second = rnd.output_path(tmp_path, fixture_video, "forest landscape", 12.0)
    assert second != first and not second.exists()


# --- real renders ------------------------------------------------------------------

def test_crop_render_is_1080x1920_with_audio(fixture_video, music_dir, tmp_path):
    out = rnd.render(
        fixture_video, Candidate(10.0, 15.0, 0.3), Verdict(True, "vista", 0.3, CROP),
        "forest landscape", music_dir=music_dir, out_dir=tmp_path / "out",
    )
    stream = probe(out, "stream=width,height")["streams"][0]
    assert (int(stream["width"]), int(stream["height"])) == (rnd.OUTPUT_WIDTH, rnd.OUTPUT_HEIGHT)
    assert probe(out, "stream=codec_type", stream="a:0")["streams"]


def test_pillarbox_render_is_1080x1920(fixture_video, music_dir, tmp_path):
    out = rnd.render(
        fixture_video, Candidate(10.0, 15.0, 0.3), Verdict(True, "wide", None, PILLARBOX),
        "forest", music_dir=music_dir, out_dir=tmp_path / "out",
    )
    stream = probe(out, "stream=width,height")["streams"][0]
    assert (int(stream["width"]), int(stream["height"])) == (rnd.OUTPUT_WIDTH, rnd.OUTPUT_HEIGHT)


def test_output_duration_matches_the_range(fixture_video, music_dir, tmp_path):
    """Covers AE2 -- no partial frames at either boundary."""
    out = rnd.render(
        fixture_video, Candidate(10.0, 18.0, 0.3), Verdict(True, "v", 0.5, CROP),
        "forest", music_dir=music_dir, out_dir=tmp_path / "out",
    )
    duration = float(probe(out, "format=duration")["format"]["duration"])
    assert abs(duration - 8.0) < 1.0 / rnd.OUTPUT_FPS + 0.1


def test_clip_longer_than_the_track_still_has_continuous_audio(fixture_video, music_dir, tmp_path):
    """Covers AE4. The only track is 6s; the clip is 12s."""
    out = rnd.render(
        fixture_video, Candidate(10.0, 22.0, 0.3), Verdict(True, "v", 0.5, CROP),
        "forest", music_dir=music_dir, out_dir=tmp_path / "out",
    )
    audio = float(probe(out, "stream=duration", stream="a:0")["streams"][0]["duration"])
    assert audio > 11.0


def test_rendered_audio_does_not_clip(fixture_video, music_dir, tmp_path):
    out = rnd.render(
        fixture_video, Candidate(10.0, 15.0, 0.3), Verdict(True, "v", 0.5, CROP),
        "forest", music_dir=music_dir, out_dir=tmp_path / "out",
    )
    stats = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    ).stderr
    peak = next(ln for ln in stats.splitlines() if "max_volume" in ln)
    assert float(peak.split("max_volume:")[1].strip().split()[0]) <= 0.0


def test_zero_length_range_is_rejected(fixture_video, music_dir, tmp_path):
    with pytest.raises(ReelsError, match="zero-length"):
        rnd.render(
            fixture_video, Candidate(10.0, 10.0, 0.3), Verdict(True, "v", 0.5, CROP),
            "forest", music_dir=music_dir, out_dir=tmp_path / "out",
        )
