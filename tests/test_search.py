"""U2 -- query scoring and candidate range derivation. Covers AE2."""
import numpy as np
import pytest

from reels import search
from reels.index import Index


def make_index(scores, stride=2.0):
    """An index whose single-dimension embeddings dot to exactly `scores` against [1.0]."""
    scores = np.asarray(scores, dtype=np.float32).reshape(-1, 1)
    timestamps = np.arange(len(scores), dtype=np.float64) * stride
    return Index(scores, timestamps)


def unit_query(_query):
    return np.array([1.0], dtype=np.float32)


def run(index, **kw):
    kw.setdefault("floor", 0.0)
    kw.setdefault("min_seconds", 0.0)
    return search.candidates(index, "anything", encoder=unit_query, **kw)


def test_matching_span_is_returned():
    idx = make_index([0.1] * 20 + [0.9] * 15 + [0.1] * 20)
    found = run(idx)
    assert found
    assert found[0].start <= 40.0 <= found[0].end


def test_range_ends_where_score_falls_not_at_fixed_offset():
    """Covers AE2. The peak sits early; the range still ends when the score drops."""
    idx = make_index([0.1] * 10 + [0.9] + [0.7] * 14 + [0.1] * 10)
    found = run(idx)
    assert found[0].start == pytest.approx(20.0)
    # The peak is at t=20; the range runs on to the end of the 0.7 plateau at t=48.
    assert found[0].end > 45.0


def test_single_dipped_frame_does_not_split_a_range():
    dipped = [0.1] * 10 + [0.9] * 7 + [0.55] + [0.9] * 7 + [0.1] * 10
    assert len(run(make_index(dipped))) == 1


def test_range_shorter_than_minimum_is_dropped():
    idx = make_index([0.1] * 20 + [0.9] * 3 + [0.1] * 20)
    assert run(idx, min_seconds=15.0) == []


def test_overlong_range_is_truncated_to_its_best_window():
    idx = make_index([0.1] * 5 + [0.7] * 20 + [0.95] * 10 + [0.7] * 20 + [0.1] * 5)
    found = run(idx, max_seconds=20.0)
    assert found[0].duration == pytest.approx(20.0)
    assert found[0].start <= 50.0 <= found[0].end  # the 0.95 plateau starts at t=50


def test_thresholds_are_per_query_not_absolute():
    """Two recordings whose score scales differ by 10x both yield ranges (KTD6)."""
    shape = [0.01] * 20 + [0.09] * 15 + [0.01] * 20
    assert run(make_index(shape))
    assert run(make_index([s * 10 for s in shape]))


def test_peak_below_absolute_floor_returns_empty():
    """The percentile pair always admits the top few percent; the floor is what rejects."""
    idx = make_index([0.10] * 20 + [0.18] * 15 + [0.10] * 20)
    assert search.candidates(idx, "x", encoder=unit_query, floor=0.22, min_seconds=0.0) == []
    assert search.candidates(idx, "x", encoder=unit_query, floor=0.05, min_seconds=0.0)


def test_empty_index_returns_empty_not_raises():
    assert run(Index(np.zeros((0, 1), np.float32), np.zeros(0))) == []


def test_shortlist_caps_the_returned_count():
    scores = ([0.1] * 5 + [0.9] * 8) * 10
    found = run(make_index(scores), shortlist=3)
    assert len(found) == 3
    assert found == sorted(found, key=lambda c: c.peak, reverse=True)


def test_default_floor_sits_between_present_and_absent_queries():
    """Calibrated at U2's checkpoint against the reference recording.

    Present queries peaked 0.2183-0.3172 there; absent ones 0.1204-0.1637. A floor
    outside that gap either rejects real matches or admits queries with nothing behind them.
    """
    assert 0.1637 < search.DEFAULT_FLOOR < 0.2183
