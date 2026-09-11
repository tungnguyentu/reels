"""U1 — keyframe index. Covers AE5."""
import os

import numpy as np
import pytest

from reels import ReelsError
from reels import index as idx


def test_embedding_count_matches_keyframe_count(fixture_video, keyframe_count, fake_encoder):
    built = idx.build_index(fixture_video, encoder=fake_encoder)
    assert idx.index_path(fixture_video).is_file()
    assert len(built.embeddings) == keyframe_count
    assert len(built.timestamps) == keyframe_count


def test_timestamps_are_monotonic_and_within_duration(fixture_video, fake_encoder):
    built = idx.build_index(fixture_video, encoder=fake_encoder)
    assert np.all(np.diff(built.timestamps) > 0)
    assert built.timestamps[0] >= 0.0
    assert built.timestamps[-1] < 90.0


def test_reindexing_does_no_extraction(fixture_video, fake_encoder, monkeypatch):
    """Covers AE5. A second query against an indexed recording extracts and embeds nothing."""
    idx.build_index(fixture_video, encoder=fake_encoder)

    def explode(*_args, **_kwargs):
        raise AssertionError("re-extracted an already-indexed recording")

    monkeypatch.setattr(idx, "extract_keyframes", explode)
    monkeypatch.setattr(idx, "keyframe_timestamps", explode)
    reloaded = idx.build_index(fixture_video, encoder=explode)
    assert len(reloaded.embeddings) > 0


def test_modified_source_triggers_reindex(fixture_video, fake_encoder):
    idx.build_index(fixture_video, encoder=fake_encoder)
    assert idx.is_fresh(fixture_video)
    # Set the mtime explicitly: touch() lands in the same filesystem clock tick as the
    # index write, which is ambiguous rather than newer.
    later = idx.index_path(fixture_video).stat().st_mtime_ns + 1_000_000_000
    os.utime(fixture_video, ns=(later, later))
    assert not idx.is_fresh(fixture_video)


def test_missing_input_errors_and_writes_no_index(tmp_path, fake_encoder):
    missing = tmp_path / "nope.mp4"
    with pytest.raises(ReelsError, match="not a file"):
        idx.build_index(missing, encoder=fake_encoder)
    assert not idx.index_path(missing).exists()


def test_count_mismatch_fails_loudly(fixture_video, fake_encoder, monkeypatch):
    """A short timestamp list must not be paired off against the frames by truncation."""
    real = idx.keyframe_timestamps
    monkeypatch.setattr(idx, "keyframe_timestamps", lambda v, start=0.0: real(v, start)[:-3])
    with pytest.raises(ReelsError, match="mismatch"):
        idx.build_index(fixture_video, encoder=fake_encoder)
    assert not idx.index_path(fixture_video).exists()


def test_real_clip_embeddings_are_l2_normalised(fixture_video):
    """Cosine similarity is a plain dot product downstream, so the real encoder must normalise."""
    built = idx.build_index(fixture_video)
    norms = np.linalg.norm(built.embeddings, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-4)
    assert built.embeddings.dtype == np.float32


def test_missing_index_names_the_index_command(tmp_path):
    with pytest.raises(ReelsError, match="reels index"):
        idx.load_index(tmp_path / "never-indexed.mp4")


def test_changing_the_clip_checkpoint_invalidates_the_cache(fixture_video, fake_encoder, monkeypatch):
    """Embeddings from another checkpoint live in a different space: scoring against them
    returns confident nonsense rather than an error, so the cache must not survive."""
    idx.build_index(fixture_video, encoder=fake_encoder)
    assert idx.is_fresh(fixture_video)
    monkeypatch.setattr(idx, "PRETRAINED", "some-other-checkpoint")
    assert not idx.is_fresh(fixture_video)


def test_resized_source_with_preserved_mtime_invalidates_the_cache(fixture_video, fake_encoder):
    """cp -p and archive extraction preserve mtime, so size is what catches a swap."""
    idx.build_index(fixture_video, encoder=fake_encoder)
    stamp = idx.index_path(fixture_video).stat().st_mtime_ns
    fixture_video.write_bytes(fixture_video.read_bytes() + b"\x00" * 64)
    os.utime(fixture_video, ns=(stamp, stamp))
    assert not idx.is_fresh(fixture_video)


def test_force_reindexes_a_current_cache(fixture_video, fake_encoder, monkeypatch):
    idx.build_index(fixture_video, encoder=fake_encoder)
    calls = []
    real = idx.extract_keyframes
    monkeypatch.setattr(idx, "extract_keyframes", lambda v, d: calls.append(1) or real(v, d))
    idx.build_index(fixture_video, encoder=fake_encoder, force=True)
    assert calls


def test_index_records_the_source_duration(fixture_video, fake_encoder):
    built = idx.build_index(fixture_video, encoder=fake_encoder)
    assert built.duration == pytest.approx(90.0, abs=0.5)


def test_unreadable_keyframe_time_is_a_reels_error_not_a_traceback(fixture_video, monkeypatch):
    monkeypatch.setattr(idx, "run_tool", lambda *a, **k: "1.0\nN/A\n2.0\n")
    with pytest.raises(ReelsError, match="unreadable keyframe time"):
        idx.keyframe_timestamps(fixture_video)


def test_binary_mode_failures_still_carry_ffmpeg_stderr(tmp_path):
    """judge.py grabs JPEG bytes with text=False; that branch decodes stderr separately."""
    from reels import run_tool

    with pytest.raises(ReelsError, match="reading a frame"):
        run_tool(
            ["ffmpeg", "-v", "error", "-i", str(tmp_path / "absent.mp4"), "-f", "image2", "-"],
            text=False, what="reading a frame",
        )
