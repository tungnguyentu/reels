"""Vision-model gate over candidate ranges (U3).

CLIP finds frames that match the words. It cannot tell a landscape from a close-up of
the same foliage, and it happily matches a forest visible behind an inventory screen.
A vision model looks at each candidate and answers both questions, plus where in the
2.49:1 frame a 9:16 window should sit. See KTD3 and KTD4 in the plan.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import ReelsError
from .frames import grab_frame
from .search import Candidate

BACKEND_ENV = "REELS_JUDGE_BACKEND"  # "gemini" (default) or "agy"
API_KEY_ENV = "GEMINI_API_KEY"
MODEL_ENV = "REELS_JUDGE_MODEL"
DEFAULT_MODEL = "gemini-flash-latest"  # an alias, not a pinned id: a hardcoded version
# goes stale silently and the failure only shows up as a dead run months later.
FRAMES_PER_RANGE = 3
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 4.0  # seconds before the first retry, doubling after that
# Codes worth another attempt: throttling, and the server-side capacity errors a free
# tier sees constantly ("this model is currently experiencing high demand").
TRANSIENT_CODES = frozenset({429, 500, 502, 503, 504})
TRANSIENT_MARKERS = ("429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "RATE LIMIT")

CROP = "crop"
PILLARBOX = "pillarbox"
DEFAULT_FOCAL_X = 0.5  # the only place this default lives

Asker = Callable[[Sequence[bytes], str], dict]

PROMPT = """These frames are sampled from one continuous span of gameplay footage.
The creator is looking for: "{query}"

Judge whether this span is usable as a vertical short-form video clip.

Reject it if any of these hold:
- A full-screen UI overlay covers the frame: an inventory, crafting or storage screen, a
  pause menu, map, scoreboard, shop, killcam, or a level-up or match-summary banner.
  Reject on this regardless of what the background shows.
- The shot is a near-field close-up. The camera is pressed against a wall, a surface, or
  foliage, with no sense of distance and no horizon.
- The camera motion is erratic: spinning, jerking, or whipping around.

Accept it only if the span actually shows what the creator asked for, with a sense of
depth or a visible horizon, and steady camera movement.

The source frame is much wider than it is tall. If you accept, also report:
- focal_x: where the subject sits horizontally, as a fraction of frame width from the
  left edge. 0.5 means dead centre.
- reframe: "crop" if a tall narrow window around focal_x still contains the subject and
  reads as a complete shot. "pillarbox" if the scene needs the full width to make sense
  and cropping it would throw away the subject.
- appeal: 0-10, how likely a scroller is to stop on this and keep watching. Judge what
  is actually visible: a decisive action, a near miss, a reversal, an unusual sight, or
  motion that resolves into something. Score low for anything a viewer has seen a
  thousand times, anything that takes several seconds to become interesting, and
  anything whose payoff happened off-screen or before this span.
- hook: one short phrase naming what would hold someone in the first second. If nothing
  would, say so plainly rather than inventing something.

Respond with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "accepted": {"type": "boolean"},
        "reason": {"type": "string"},
        "focal_x": {"type": "number"},
        "reframe": {"type": "string", "enum": [CROP, PILLARBOX]},
        "appeal": {"type": "integer", "minimum": 0, "maximum": 10},
        "hook": {"type": "string"},
    },
    "required": ["accepted", "reason"],
}


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    reason: str
    focal_x: float | None = None
    reframe: str | None = None
    # How likely a scroller is to stop on this, 0-10, and what would hold them. This
    # ranks accepted spans; it never gates them. The model has seen no retention data
    # for this account, so it is a prior about what reads as interesting, not a
    # prediction -- treated as a gate it would quietly discard usable footage.
    appeal: int | None = None
    hook: str | None = None


def sample_frames(video: Path, candidate: Candidate, count: int = FRAMES_PER_RANGE) -> list[bytes]:
    """Evenly spaced frames from inside the range, avoiding both boundaries."""
    times = np.linspace(candidate.start, candidate.end, count + 2)[1:-1]
    return [grab_frame(Path(video), float(t)) for t in times]


def _is_transient(exc: Exception) -> bool:
    """Whether a provider error is worth another attempt rather than a real failure.

    Not just throttling. A live run against a free-tier key lost two of six ranges to
    `503 UNAVAILABLE -- this model is currently experiencing high demand`, which is as
    temporary as a 429 and far more common. Matched structurally where the client
    exposes a code, and by status name otherwise, so a client version that renames its
    exception classes does not silently turn a retryable blip into a dropped candidate.
    """
    if (getattr(exc, "code", None) or getattr(exc, "status_code", None)) in TRANSIENT_CODES:
        return True
    text = str(exc).upper()
    return any(marker in text for marker in TRANSIENT_MARKERS)


def _retrying(call: Callable[[], object], *, sleep=time.sleep) -> object:
    """Retry a transient provider failure with exponential backoff.

    A free-tier key allows roughly 10-15 requests per minute, one query can ask about
    twenty ranges, and the shared free capacity returns 503 under load. Both are the
    expected case rather than an exceptional one. Without this the range is recorded as
    errored and its footage discarded as if it were unusable. Only transient failures
    retry -- a bad key or a malformed request still surfaces at once.
    """
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return call()
        except Exception as exc:
            if attempt == RETRY_ATTEMPTS - 1 or not _is_transient(exc):
                raise
            sleep(RETRY_BACKOFF * (2**attempt))
    raise AssertionError("unreachable")  # pragma: no cover


def _ask_gemini(frames: Sequence[bytes], prompt: str) -> dict:
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        raise ReelsError(
            f"{API_KEY_ENV} is not set. Create a key at https://aistudio.google.com/apikey "
            f"and export it before running `reels clip`."
        )
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ReelsError(f"the Gemini client is not installed: {exc}") from exc

    client = genai.Client(api_key=api_key)
    parts = [types.Part.from_bytes(data=f, mime_type="image/jpeg") for f in frames]
    def request():
        return client.models.generate_content(
            model=os.environ.get(MODEL_ENV, DEFAULT_MODEL),
            contents=[*parts, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=SCHEMA
            ),
        )

    try:
        response = _retrying(request)
    except Exception as exc:
        if _is_transient(exc):
            raise ReelsError(
                f"the vision model was still unavailable after {RETRY_ATTEMPTS} attempts: "
                f"{exc}. Free-tier capacity is shared and rate limited; try again shortly, "
                f"or --shortlist 8 to ask for less at once."
            ) from exc
        raise ReelsError(f"the vision model request failed: {exc}") from exc

    try:
        return json.loads(response.text)
    except (AttributeError, TypeError, json.JSONDecodeError) as exc:
        raise ReelsError(f"the vision model returned something unreadable: {exc}") from exc


def _appeal(payload: dict) -> tuple[int | None, str | None]:
    """The appeal score and hook, or (None, None) when the model left them out.

    Missing is not zero: a backend or model that never answers this must leave the
    ranking untouched rather than sort every span to the bottom.
    """
    raw = payload.get("appeal")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, None
    hook = str(payload.get("hook", "")).strip() or None
    return int(min(10, max(0, round(raw)))), hook


def _to_verdict(payload: object) -> Verdict:
    if not isinstance(payload, dict) or "accepted" not in payload:
        raise ReelsError(f"the vision model returned an incomplete verdict: {payload!r}")
    reason = str(payload.get("reason", "")).strip() or "no reason given"
    if not payload["accepted"]:
        # Appeal is read on this branch too. It ranks rather than gates, so dropping it
        # here would sink every rejected span to the bottom of any appeal ordering --
        # accept/reject deciding the ranking through the back door.
        appeal, hook = _appeal(payload)
        return Verdict(False, reason, None, None, appeal, hook)

    focal_x, reframe = payload.get("focal_x"), payload.get("reframe")
    if reframe not in (CROP, PILLARBOX):
        raise ReelsError(f"the vision model accepted a span without a usable reframe mode: {payload!r}")
    if focal_x is None:
        if reframe == CROP:
            raise ReelsError(f"the vision model chose a crop without a focal point: {payload!r}")
        focal_x = DEFAULT_FOCAL_X
    elif not isinstance(focal_x, (int, float)) or isinstance(focal_x, bool):
        # Checked on both paths: a non-numeric focal_x used to reach the clamp on the
        # pillarbox branch and raise a bare TypeError, losing every range already judged.
        raise ReelsError(f"the vision model returned a non-numeric focal point: {payload!r}")
    appeal, hook = _appeal(payload)
    return Verdict(True, reason, float(min(1.0, max(0.0, focal_x))), reframe, appeal, hook)


def judge(video: Path, candidate: Candidate, query: str, *, ask: Asker | None = None) -> Verdict:
    frames = sample_frames(video, candidate)
    return _to_verdict((ask or _ask_gemini)(frames, PROMPT.format(query=query)))


def backend() -> str:
    """Which judge backend to use.

    Explicit choice wins. Otherwise prefer the direct API when a key exists -- it is far
    cheaper per range than the agent CLI -- and fall back to `agy`, which needs no key of
    its own because it uses the Google account you signed into once.
    """
    chosen = os.environ.get(BACKEND_ENV, "").strip().lower()
    if chosen in ("gemini", "agy"):
        return chosen
    if os.environ.get(API_KEY_ENV):
        return "gemini"
    from . import agy
    return "agy" if agy.available() else "gemini"


def available() -> bool:
    """Whether any judge can actually run, which is what the UI greys its button on.

    A key is not the only way in: the agy backend authenticates with a signed-in Google
    account, so keying off GEMINI_API_KEY alone reports "no judge" on a machine where
    judging works fine.
    """
    if backend() == "agy":
        from . import agy
        return agy.available()
    return bool(os.environ.get(API_KEY_ENV))


def judge_all(video: Path, candidates: Sequence[Candidate], query: str):
    """One entry per candidate, in order: a Verdict, or the ReelsError for that span.

    Errors are returned rather than raised so one unjudgeable span cannot discard the
    spans that succeeded -- the same contract the CLI has always had. Batched where the
    backend benefits: `agy` carries its whole harness context per invocation, so judging
    ranges one at a time costs it roughly six times as long.
    """
    if not candidates:
        return []
    if backend() == "agy":
        from . import agy
        try:
            return list(agy.judge_ranges(Path(video), list(candidates), query))
        except ReelsError as exc:
            return [exc] * len(candidates)  # agy answers a batch or none of it
    out = []
    for candidate in candidates:
        try:
            out.append(judge(video, candidate, query))
        except ReelsError as exc:
            out.append(exc)
    return out
