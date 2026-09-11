"""Vision-model gate over candidate ranges (U3).

CLIP finds frames that match the words. It cannot tell a landscape from a close-up of
the same foliage, and it happily matches a forest visible behind an inventory screen.
A vision model looks at each candidate and answers both questions, plus where in the
2.49:1 frame a 9:16 window should sit. See KTD3 and KTD4 in the plan.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import ReelsError
from .search import Candidate

API_KEY_ENV = "GEMINI_API_KEY"
MODEL_ENV = "REELS_JUDGE_MODEL"
DEFAULT_MODEL = "gemini-2.5-flash"
FRAMES_PER_RANGE = 3
SAMPLE_WIDTH = 768

CROP = "crop"
PILLARBOX = "pillarbox"

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
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={SAMPLE_WIDTH}:-2", "-f", "image2", "-c:v", "mjpeg", "-"],
        capture_output=True, check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise ReelsError(f"could not read a frame at {at:.1f}s from {video.name}")
    return proc.stdout


def sample_frames(video: Path, candidate: Candidate, count: int = FRAMES_PER_RANGE) -> list[bytes]:
    """Evenly spaced frames from inside the range, avoiding both boundaries."""
    times = np.linspace(candidate.start, candidate.end, count + 2)[1:-1]
    return [_grab_frame(Path(video), float(t)) for t in times]


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
    try:
        response = client.models.generate_content(
            model=os.environ.get(MODEL_ENV, DEFAULT_MODEL),
            contents=[*parts, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=SCHEMA
            ),
        )
    except Exception as exc:
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
    if reframe == CROP and not isinstance(focal_x, (int, float)):
        raise ReelsError(f"the vision model chose a crop without a focal point: {payload!r}")
    return Verdict(True, reason, float(min(1.0, max(0.0, focal_x or 0.5))), reframe)


def judge(video: Path, candidate: Candidate, query: str, *, ask: Asker | None = None) -> Verdict:
    frames = sample_frames(video, candidate)
    return _to_verdict((ask or _ask_gemini)(frames, PROMPT.format(query=query)))
