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
    monkeypatch.setattr(idx, "keyframe_timestamps", lambda v: real(v)[:-3])
    with pytest.raises(ReelsError, match="mismatch"):
        idx.build_index(fixture_video, encoder=fake_encoder)
    assert not idx.index_path(fixture_video).exists()


def test_real_clip_embeddings_are_l2_normalised(fixture_video):
    """Cosine similarity is a plain dot product downstream, so the real encoder must normalise."""
    built = idx.build_index(fixture_video)
    norms = np.linalg.norm(built.embeddings, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-4)
    assert built.embeddings.dtype == np.float32
