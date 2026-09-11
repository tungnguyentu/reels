"""Query scoring and candidate range derivation (U2).

Two mechanisms doing different jobs (KTD6). The absolute floor answers "does this
recording contain the thing at all" -- without it the percentiles admit the top few
percent of frames on every query, so a hopeless query would still shortlist candidates
and spend judge calls. Hysteresis answers "where does this moment start and stop".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .index import Index, encode_text

DEFAULT_MIN_SECONDS = 15.0
DEFAULT_MAX_SECONDS = 45.0
DEFAULT_SHORTLIST = 20
DEFAULT_FLOOR = 0.19  # see U2's checkpoint: present queries peak 0.218-0.317 on the
# reference recording, absent ones 0.120-0.164; this is the midpoint of that gap.
SMOOTH_FRAMES = 3
BASELINE_PERCENTILE = 10  # robust floor of this query's own range
CEILING_PERCENTILE = 99  # robust top, ignoring a single freak frame
ENTER_FRACTION = 0.70  # a range must peak this far up the range to qualify
EXIT_FRACTION = 0.45  # ...and stays open until it falls below here

TextEncoder = Callable[[str], np.ndarray]


@dataclass(frozen=True)
class Candidate:
    start: float  # seconds into the source
    end: float
    peak: float  # highest smoothed score inside the range

    @property
    def duration(self) -> float:
        return self.end - self.start


def smooth(scores: np.ndarray, frames: int = SMOOTH_FRAMES) -> np.ndarray:
    """Moving average that keeps the array length, so indices still map to timestamps."""
    if frames <= 1 or len(scores) < frames:
        return scores.astype(np.float64)
    kernel = np.ones(frames) / frames
    padded = np.pad(scores.astype(np.float64), (frames // 2, frames - 1 - frames // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _runs_by_hysteresis(values: np.ndarray, enter: float, exit_: float) -> list[tuple[int, int]]:
    """Maximal runs above the exit threshold that peak above the enter threshold.

    A single dipped frame mid-shot stays inside its run, so one moment does not split in two.
    """
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, value in enumerate(values):
        if value >= exit_:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(values) - 1))
    return [r for r in runs if values[r[0] : r[1] + 1].max() >= enter]


def _best_window(values: np.ndarray, start: int, end: int, max_frames: int) -> tuple[int, int]:
    """The highest-scoring window of max_frames inside an over-long run."""
    if end - start + 1 <= max_frames:
        return start, end
    best_start, best_total = start, -np.inf
    for i in range(start, end - max_frames + 2):
        total = values[i : i + max_frames].sum()
        if total > best_total:
            best_total, best_start = total, i
    return best_start, best_start + max_frames - 1


def _thresholds(scores: np.ndarray) -> tuple[float, float]:
    """Enter/exit as fractions of this query's own dynamic range.

    Not percentiles of the score distribution: percentiles describe how many frames
    match, so on a recording where a third of the session matches, the 90th percentile
    sits inside the matching band and hysteresis has no room left to work. Normalising
    against the range instead keeps the thresholds meaningful whether 2% or 40% of a
    session matches, and stays per-query rather than absolute (KTD6).
    """
    baseline = float(np.percentile(scores, BASELINE_PERCENTILE))
    ceiling = float(np.percentile(scores, CEILING_PERCENTILE))
    span = ceiling - baseline
    return baseline + ENTER_FRACTION * span, baseline + EXIT_FRACTION * span


def _stride(timestamps: np.ndarray) -> float:
    """How much wall time one keyframe stands for. Used to give the last frame its duration."""
    if len(timestamps) < 2:
        return 0.0
    return float(np.median(np.diff(timestamps)))


def candidates(
    index: Index,
    query: str,
    *,
    encoder: TextEncoder | None = None,
    min_seconds: float = DEFAULT_MIN_SECONDS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    shortlist: int = DEFAULT_SHORTLIST,
    floor: float = DEFAULT_FLOOR,
) -> list[Candidate]:
    """Ranked candidate ranges for a query, or an empty list when nothing matches at all."""
    query_vector = (encoder or encode_text)(query)
    scores = smooth(index.embeddings @ query_vector)
    if scores.size == 0 or scores.max() < floor:
        return []

    enter, exit_ = _thresholds(scores)
    stride = _stride(index.timestamps)
    min_frames = max(1, round(min_seconds / stride)) if stride else 1
    max_frames = max(min_frames, round(max_seconds / stride)) if stride else len(scores)

    found: list[Candidate] = []
    for run_start, run_end in _runs_by_hysteresis(scores, enter, exit_):
        if run_end - run_start + 1 < min_frames:
            continue
        first, last = _best_window(scores, run_start, run_end, max_frames)
        found.append(
            Candidate(
                start=float(index.timestamps[first]),
                end=float(index.timestamps[last] + stride),
                peak=float(scores[first : last + 1].max()),
            )
        )

    found.sort(key=lambda c: c.peak, reverse=True)
    return found[:shortlist]
