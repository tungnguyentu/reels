"""V1 -- several treatments of one moment."""
import subprocess

import pytest

from reels import ReelsError
from reels.judge import CROP, PILLARBOX, Verdict
from reels.search import Candidate
from reels.variants import MAX_TREATMENTS, Treatment, default_treatments, render_variants

SPAN = Candidate(start=10.0, end=26.0, peak=0.3)


@pytest.fixture
def music_dir(tmp_path):
    folder = tmp_path / "music"
    folder.mkdir()
    for name, freq in (("alpha.mp3", 440), ("beta.mp3", 660)):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=20",
             str(folder / name), "-y"], check=True,
        )
    return folder


def test_defaults_offer_both_reframe_paths():
    """The crop/pillarbox call is the one the judge got wrong half the time, so both ship."""
    modes = {t.reframe for t in default_treatments()}
    assert modes == {CROP, PILLARBOX}


def test_defaults_lead_with_the_judge_s_choice():
    assert default_treatments(Verdict(True, "wide", None, PILLARBOX))[0].reframe == PILLARBOX
    assert default_treatments(Verdict(True, "vista", 0.3, CROP))[0].reframe == CROP


def test_defaults_carry_the_judge_s_focal_point():
    assert default_treatments(Verdict(True, "v", 0.8, CROP))[0].focal_x == 0.8


def test_a_treatment_can_trim_within_the_candidate(fixture_video):
    trimmed = Treatment(start=14.0, end=20.0).span(SPAN)
    assert (trimmed.start, trimmed.end) == (14.0, 20.0)


def test_a_treatment_cannot_widen_past_the_candidate():
    widened = Treatment(start=0.0, end=999.0).span(SPAN)
    assert (widened.start, widened.end) == (SPAN.start, SPAN.end)


def test_a_treatment_that_trims_to_nothing_is_an_error():
    with pytest.raises(ReelsError, match="nothing"):
        Treatment(start=25.0, end=11.0).span(SPAN)


def test_each_treatment_produces_its_own_file(fixture_video, music_dir, tmp_path):
    out = tmp_path / "out"
    written = render_variants(
        fixture_video, SPAN, "forest", default_treatments(),
        music_dir=music_dir, out_dir=out,
    )
    assert len(written) == 2
    assert len({p.name for p in written}) == 2, "variants must not overwrite each other"
    assert all(p.stat().st_size > 0 for p in written)


def test_an_explicit_track_is_honoured(fixture_video, music_dir, tmp_path, monkeypatch):
    seen = []
    from reels import variants as var
    monkeypatch.setattr(var, "render", lambda *a, **k: seen.append(k["track"]) or tmp_path / "x.mp4")
    render_variants(
        fixture_video, SPAN, "forest",
        [Treatment(track="beta.mp3"), Treatment(track="alpha.mp3")],
        music_dir=music_dir, out_dir=tmp_path,
    )
    assert [p.name for p in seen] == ["beta.mp3", "alpha.mp3"]


def test_an_unknown_track_fails_before_any_encode(fixture_video, music_dir, tmp_path):
    out = tmp_path / "out"
    with pytest.raises(ReelsError, match="no such track"):
        render_variants(
            fixture_video, SPAN, "forest", [Treatment(track="nope.mp3")],
            music_dir=music_dir, out_dir=out,
        )
    assert not out.exists() or not list(out.glob("*.mp4"))


def test_a_missing_music_dir_fails_before_any_encode(fixture_video, tmp_path):
    with pytest.raises(ReelsError, match="music directory not found"):
        render_variants(
            fixture_video, SPAN, "forest", default_treatments(),
            music_dir=tmp_path / "absent", out_dir=tmp_path / "out",
        )


def test_too_many_treatments_is_refused(fixture_video, music_dir, tmp_path):
    """Each treatment is a full re-encode; past the cap a run stops being interactive."""
    with pytest.raises(ReelsError, match="at most"):
        render_variants(
            fixture_video, SPAN, "forest", [Treatment()] * (MAX_TREATMENTS + 1),
            music_dir=music_dir, out_dir=tmp_path / "out",
        )


def test_no_treatments_is_refused(fixture_video, music_dir, tmp_path):
    with pytest.raises(ReelsError, match="no treatments"):
        render_variants(fixture_video, SPAN, "forest", [], music_dir=music_dir, out_dir=tmp_path)
