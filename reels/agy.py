"""Judge backend that shells out to the Antigravity CLI (`agy`).

Why this exists: `agy` needs no API key of its own -- it uses the Google account you
signed into once -- so a machine with `agy` can run the judge without provisioning
`GEMINI_API_KEY`.

Why it batches. `agy` is an agent, not an API client: every invocation carries its
harness context, so a single image costs ~287k input tokens and ~114s. Six images in one
call cost 84k and 28s. The per-range shape the direct API uses would take six minutes for
a twenty-range query; batching brings that to well under one.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import ReelsError
from .judge import CROP, DEFAULT_FOCAL_X, PILLARBOX, PROMPT, Verdict, _appeal, sample_frames
from .search import Candidate

BINARY = os.environ.get("REELS_AGY_BIN", "agy")
MODEL_ENV = "REELS_AGY_MODEL"
TIMEOUT_ENV = "REELS_AGY_TIMEOUT"
DEFAULT_MODEL = "gemini-3.8-flash-low"
BATCH_RANGES = 8  # ranges per call; keeps one call's image count and wall time sane
# agy prints nothing while it works, so a stalled provider looks identical to a slow one.
# A measured batch of four spans answers in 35-60s; the default leaves generous headroom,
# and REELS_AGY_TIMEOUT shortens it when you would rather fail fast than wait.
DEFAULT_TIMEOUT_SECONDS = 900


def timeout_seconds() -> float:
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        raise ReelsError(f"{TIMEOUT_ENV} must be a number of seconds, not {raw!r}") from None
    if value <= 0:
        raise ReelsError(f"{TIMEOUT_ENV} must be greater than zero, not {value:g}")
    return value

SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "range": {"type": "integer"},
                    "accepted": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "focal_x": {"type": "number"},
                    "reframe": {"type": "string", "enum": [CROP, PILLARBOX]},
                    "appeal": {"type": "integer", "minimum": 0, "maximum": 10},
                    "hook": {"type": "string"},
                },
                "required": ["range", "accepted", "reason"],
            },
        }
    },
    "required": ["verdicts"],
}

BATCH_PROMPT = """{base}

This directory holds frames from {count} candidate spans of one recording, named
`r<NN>_<k>.jpg` where `<NN>` is the span number. Read every file, judge each span from
its own frames, and return exactly one verdict per span with `range` set to that span's
number. Judge all {count} spans -- do not stop early."""


def available() -> bool:
    return shutil.which(BINARY) is not None


def _run(workdir: Path, prompt: str, schema_path: Path) -> dict:
    cmd = [
        BINARY, "--sandbox", "--dangerously-skip-permissions",
        "--add-dir", str(workdir),
        "--model", os.environ.get(MODEL_ENV, DEFAULT_MODEL),
        "--json-schema", str(schema_path),
        "--output-format", "json",
        "--print", prompt,
    ]
    limit = timeout_seconds()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=limit, check=False, cwd=workdir)
    except FileNotFoundError as exc:
        raise ReelsError(f"{BINARY} is not on PATH: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ReelsError(
            f"{BINARY} did not answer within {limit:g}s -- it prints nothing while working, "
            f"so this is usually the provider stalling rather than the batch being too big; "
            f"retrying often succeeds. {TIMEOUT_ENV} sets the limit."
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise ReelsError(f"{BINARY} failed: {detail[-1] if detail else proc.returncode}")

    body = proc.stdout.replace("\r", "").strip()
    try:
        envelope = json.loads(body)
    except json.JSONDecodeError as exc:
        # The commonest cause is a login having lapsed: agy prints prose, not JSON.
        hint = " -- run `agy` once to sign in" if "auth" in body.lower() else ""
        raise ReelsError(f"{BINARY} returned unreadable output{hint}: {body[:200]}") from exc
    out = envelope.get("structured_output")
    if not isinstance(out, dict):
        raise ReelsError(f"{BINARY} returned no structured output: {body[:200]}")
    return out


def _to_verdict(row: object) -> Verdict:
    if not isinstance(row, dict) or "accepted" not in row:
        raise ReelsError(f"{BINARY} returned an incomplete verdict: {row!r}")
    reason = str(row.get("reason", "")).strip() or "no reason given"
    if not row["accepted"]:
        return Verdict(False, reason)
    focal_x, reframe = row.get("focal_x"), row.get("reframe")
    if reframe not in (CROP, PILLARBOX):
        reframe = CROP if isinstance(focal_x, (int, float)) else PILLARBOX
    if focal_x is None:
        focal_x = DEFAULT_FOCAL_X
    elif not isinstance(focal_x, (int, float)) or isinstance(focal_x, bool):
        raise ReelsError(f"{BINARY} returned a non-numeric focal point: {row!r}")
    appeal, hook = _appeal(row)
    return Verdict(True, reason, float(min(1.0, max(0.0, focal_x))), reframe, appeal, hook)


def judge_ranges(video: Path, candidates: list[Candidate], query: str) -> list[Verdict]:
    """One verdict per candidate, in order. Batched; see the module docstring."""
    if not candidates:
        return []
    verdicts: list[Verdict | None] = [None] * len(candidates)

    for offset in range(0, len(candidates), BATCH_RANGES):
        chunk = candidates[offset : offset + BATCH_RANGES]
        workdir = Path(tempfile.mkdtemp(prefix="reels-agy-"))
        try:
            for local, candidate in enumerate(chunk):
                for k, jpeg in enumerate(sample_frames(video, candidate)):
                    (workdir / f"r{local:02d}_{k}.jpg").write_bytes(jpeg)
            (workdir / "schema.json").write_text(json.dumps(SCHEMA))
            out = _run(
                workdir,
                BATCH_PROMPT.format(base=PROMPT.format(query=query), count=len(chunk)),
                workdir / "schema.json",
            )
            rows = out.get("verdicts")
            if not isinstance(rows, list):
                raise ReelsError(f"{BINARY} returned no verdict list: {out!r}")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                idx = row.get("range")
                if isinstance(idx, int) and 0 <= idx < len(chunk):
                    verdicts[offset + idx] = _to_verdict(row)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    missing = [i for i, v in enumerate(verdicts) if v is None]
    if missing:
        # Better a named gap than a silent one: the caller reports these as errored
        # ranges rather than treating an unanswered span as unusable footage.
        raise ReelsError(
            f"{BINARY} returned no verdict for {len(missing)} of {len(candidates)} spans "
            f"(indices {missing[:5]}) -- try a smaller --shortlist"
        )
    return [v for v in verdicts if v is not None]
