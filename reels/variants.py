"""Several treatments of one moment (V1).

A candidate range is one moment; how it becomes a clip is a separate set of choices.
The judge has an opinion about reframing, but it is an opinion -- it was right about
half the time in testing -- so the same span is rendered several ways and the choice
stays with the person posting it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from . import ReelsError
from .judge import CROP, DEFAULT_FOCAL_X, PILLARBOX, Verdict
from .render import music_tracks, pick_track, render
from .search import Candidate

MAX_TREATMENTS = 8  # each one is a full re-encode; past this a run stops being interactive


@dataclass(frozen=True)
class Treatment:
    """One way of turning a candidate into a clip."""

    reframe: str = CROP
    focal_x: float = DEFAULT_FOCAL_X
    track: str | None = None  # filename within the music dir; None picks deterministically
    start: float | None = None  # None uses the candidate's own bounds
    end: float | None = None

    @property
    def label(self) -> str:
        where = "" if self.reframe == PILLARBOX else f" @{self.focal_x:.2f}"
        return f"{self.reframe}{where}"

    def verdict(self) -> Verdict:
        return Verdict(True, f"treatment: {self.label}", self.focal_x, self.reframe)

    def span(self, candidate: Candidate) -> Candidate:
        start = candidate.start if self.start is None else max(self.start, candidate.start)
        end = candidate.end if self.end is None else min(self.end, candidate.end)
        if end <= start:
            raise ReelsError(f"treatment {self.label} trims the range to nothing")
        return replace(candidate, start=start, end=end)


def default_treatments(verdict: Verdict | None = None) -> list[Treatment]:
    """Both reframe paths, so the crop/pillarbox choice is made by eye rather than by model.

    When the judge has given a focal point, its crop leads; otherwise the frame centre does.
    """
    focal = DEFAULT_FOCAL_X if verdict is None or verdict.focal_x is None else verdict.focal_x
    leads_with_crop = verdict is None or verdict.reframe != PILLARBOX
    crop = Treatment(reframe=CROP, focal_x=focal)
    pillarbox = Treatment(reframe=PILLARBOX)
    return [crop, pillarbox] if leads_with_crop else [pillarbox, crop]


def render_variants(
    video: Path,
    candidate: Candidate,
    query: str,
    treatments: list[Treatment],
    *,
    music_dir: Path,
    out_dir: Path,
) -> list[Path]:
    """Render each treatment of one candidate. Order is preserved."""
    if not treatments:
        raise ReelsError("no treatments to render")
    if len(treatments) > MAX_TREATMENTS:
        raise ReelsError(f"at most {MAX_TREATMENTS} treatments at a time, got {len(treatments)}")

    tracks = music_tracks(music_dir)  # validated once, before any encode starts
    by_name = {p.name: p for p in tracks}
    written = []
    for treatment in treatments:
        if treatment.track is not None and treatment.track not in by_name:
            raise ReelsError(f"no such track in {music_dir}: {treatment.track}")
        span = treatment.span(candidate)
        chosen = (
            by_name[treatment.track]
            if treatment.track is not None
            else pick_track(tracks, f"{video.name}|{query}|{span.start}")
        )
        written.append(render(
            video, span, treatment.verdict(), f"{query} {treatment.label}",
            music_dir=music_dir, out_dir=out_dir, track=chosen,
        ))
    return written
