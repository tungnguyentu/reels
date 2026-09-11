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
DEFAULT_FLOOR = 0.19  # see U2's checkpoint: peaks of the SMOOTHED scores on the reference
# recording were 0.218-0.317 for present subjects and 0.120-0.164 for absent ones; 0.19 is
# the midpoint. Phrasing moves the scale -- a long descriptive query scores lower than a
# short one -- so the CLI reports the observed peak when nothing clears this.
SMOOTH_FRAMES = 3
CEILING_PERCENTILE = 99  # robust top, ignoring a single freak frame
ENTER_FRACTION = 0.70  # a range must peak this far up the range to qualify
EXIT_FRACTION = 0.45  # ...and stays open until it falls below here
FLAT_CURVE_SPAN = 1e-6  # below this the query discriminates nothing

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


def score(index: Index, query: str, encoder: TextEncoder | None = None) -> np.ndarray:
    """The smoothed similarity curve for a query, one value per keyframe."""
    return smooth(index.embeddings @ (encoder or encode_text)(query))


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


def _thresholds(scores: np.ndarray) -> tuple[float, float] | None:
    """Enter/exit as fractions of this query's own dynamic range, or None if it is flat.

    Not percentiles of the score distribution: percentiles describe how many frames
    match, so on a recording where most of the session matches, both ends land inside the
    matching band and hysteresis has no room left to work. The minimum is the honest
    baseline -- the worst-matching frame is a genuine non-match reference at any match
    ratio -- while the 99th percentile keeps a single freak frame from setting the top.
    Normalising against that range stays per-query rather than absolute (KTD6).
    """
    baseline = float(scores.min())
    ceiling = float(np.percentile(scores, CEILING_PERCENTILE))
    span = ceiling - baseline
    if span < FLAT_CURVE_SPAN:
        return None  # every frame scores alike; there is no moment to find
    return baseline + ENTER_FRACTION * span, baseline + EXIT_FRACTION * span


def _stride(timestamps: np.ndarray) -> float:
    """How much wall time one keyframe stands for. Used to give the last frame its duration."""
    if len(timestamps) < 2:
        return 0.0
    return float(np.median(np.diff(timestamps)))


def _windows(
    scores: np.ndarray,
    timestamps: np.ndarray,
    run: tuple[int, int],
    *,
    stride: float,
    duration: float,
    min_seconds: float,
    max_seconds: float,
) -> list[Candidate]:
    """Chop one run into consecutive candidates, each at most max_seconds of real time.

    Length is measured in seconds rather than keyframe counts. On a variable-GOP
    recording a calm stretch has long gaps between keyframes, so a fixed frame count
    spans far more wall time than the operator asked for -- which is how a
    `--max-seconds 45` run produces an 88-second clip.
    """
    run_start, run_end = run
    out: list[Candidate] = []
    first = run_start
    while first <= run_end:
        last = first
        while last + 1 <= run_end and _end_at(timestamps, last + 1, stride, duration) - timestamps[first] <= max_seconds:
            last += 1
        start = float(timestamps[first])
        end = min(_end_at(timestamps, last, stride, duration), start + max_seconds)
        if end - start >= min_seconds:
            out.append(Candidate(start, end, float(scores[first : last + 1].max())))
        first = last + 1
    return out


def _end_at(timestamps: np.ndarray, i: int, stride: float, duration: float) -> float:
    """When the moment shown by keyframe i stops, clamped to the end of the recording."""
    end = float(timestamps[i]) + stride
    return min(end, duration) if duration > 0 else end


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
    scores = score(index, query, encoder)
    if scores.size == 0 or scores.max() < floor:
        return []

    thresholds = _thresholds(scores)
    if thresholds is None:
        return []
    enter, exit_ = thresholds

    stride = _stride(index.timestamps)
    found: list[Candidate] = []
    for run in _runs_by_hysteresis(scores, enter, exit_):
        found.extend(_windows(
            scores, index.timestamps, run,
            stride=stride, duration=index.duration,
            min_seconds=min_seconds, max_seconds=max_seconds,
        ))

    found.sort(key=lambda c: c.peak, reverse=True)
    return found[:shortlist]
