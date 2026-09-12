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

from . import ReelsError, run_tool
from .search import Candidate

API_KEY_ENV = "GEMINI_API_KEY"
MODEL_ENV = "REELS_JUDGE_MODEL"
DEFAULT_MODEL = "gemini-flash-latest"  # an alias, not a pinned id: a hardcoded version
# goes stale silently and the failure only shows up as a dead run months later.
FRAMES_PER_RANGE = 3
SAMPLE_WIDTH = 768
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

PROMPT = """These frames are sampled from one continuous span of Minecraft gameplay footage.
The creator is looking for: "{query}"

Judge whether this span is usable as a vertical short-form video clip.

Reject it if any of these hold:
- A game UI overlay covers the frame: inventory, chest, crafting table, furnace, pause
  menu, map, or advancement screen. Reject on this regardless of what the background shows.
- The shot is a near-field close-up. The camera is pressed against a wall, a block, or
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

Respond with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "accepted": {"type": "boolean"},
        "reason": {"type": "string"},
        "focal_x": {"type": "number"},
        "reframe": {"type": "string", "enum": [CROP, PILLARBOX]},
    },
    "required": ["accepted", "reason"],
}


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    reason: str
    focal_x: float | None = None
    reframe: str | None = None


def _grab_frame(video: Path, at: float) -> bytes:
    jpeg = run_tool(
        ["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={SAMPLE_WIDTH}:-2", "-f", "image2", "-c:v", "mjpeg", "-"],
        text=False, what=f"reading a frame at {at:.1f}s from {video.name}",
    )
    if not jpeg:
        raise ReelsError(f"no frame at {at:.1f}s in {video.name}")
    return jpeg


def sample_frames(video: Path, candidate: Candidate, count: int = FRAMES_PER_RANGE) -> list[bytes]:
    """Evenly spaced frames from inside the range, avoiding both boundaries."""
    times = np.linspace(candidate.start, candidate.end, count + 2)[1:-1]
    return [_grab_frame(Path(video), float(t)) for t in times]


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


def _to_verdict(payload: object) -> Verdict:
    if not isinstance(payload, dict) or "accepted" not in payload:
        raise ReelsError(f"the vision model returned an incomplete verdict: {payload!r}")
    reason = str(payload.get("reason", "")).strip() or "no reason given"
    if not payload["accepted"]:
        return Verdict(False, reason)

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
    return Verdict(True, reason, float(min(1.0, max(0.0, focal_x))), reframe)


def judge(video: Path, candidate: Candidate, query: str, *, ask: Asker | None = None) -> Verdict:
    frames = sample_frames(video, candidate)
    return _to_verdict((ask or _ask_gemini)(frames, PROMPT.format(query=query)))
