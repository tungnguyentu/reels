"""Keyframe extraction and CLIP embedding cache (U1).

Recordings already contain keyframes. Reading them is far cheaper than decoding the
video to a target frame rate, and 2-second granularity is finer than any Reel
boundary needs -- see KTD1 in the plan.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import ReelsError, run_tool

KEYFRAME_SIZE = 224
KEYFRAME_JPEG_QUALITY = "3"  # ffmpeg -q:v, 2-31 with 2 best
MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"

Encoder = Callable[[Sequence[Path]], np.ndarray]


@dataclass(frozen=True)
class Index:
    """One row per keyframe. Embeddings are L2-normalised, so cosine similarity is a dot product."""

    embeddings: np.ndarray  # (n, d) float32
    timestamps: np.ndarray  # (n,) float64 seconds from the start of playback
    duration: float = 0.0  # seconds; 0.0 when the source duration was unreadable


def index_path(video: Path | str) -> Path:
    return Path(f"{video}.reels.npz")


def probe_times(video: Path | str) -> tuple[float, float]:
    """(start_time, duration) of the video stream, in seconds.

    Keyframe timestamps come back as absolute presentation times, but `ffmpeg -ss`
    counts from the stream's start_time. On a container with a nonzero start_time the
    two disagree and every cut lands late, so the offset is removed at index time.
    """
    raw = run_tool([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=start_time", "-show_entries", "format=duration",
        "-of", "json", str(video),
    ])

    def number(value: object) -> float:
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0  # ffprobe reports "N/A" for some containers

    parsed = json.loads(raw)
    streams = parsed.get("streams") or [{}]
    return number(streams[0].get("start_time")), number(parsed.get("format", {}).get("duration"))


def is_fresh(video: Path | str) -> bool:
    """True when the cache can still be trusted for this source and this model.

    Size is checked alongside mtime because a restore or copy that preserves mtime
    (`cp -p`, `rsync -t`, archive extraction) would otherwise keep a stale index. The
    model identity is checked because embeddings from a different checkpoint live in a
    different space: scoring against them returns confident nonsense, not an error.
    """
    cache = index_path(video)
    source = Path(video)
    if not cache.is_file() or not source.is_file():
        return False
    if cache.stat().st_mtime_ns < source.stat().st_mtime_ns:
        return False
    try:
        with np.load(cache) as data:
            return (
                str(data["model"]) == MODEL_NAME
                and str(data["pretrained"]) == PRETRAINED
                and int(data["source_size"]) == source.stat().st_size
            )
    except (KeyError, OSError, ValueError, EOFError):
        return False  # unreadable or written by an older version -- rebuild


def keyframe_timestamps(video: Path, start_time: float = 0.0) -> np.ndarray:
    """Keyframe times relative to the start of playback.

    The image2 muxer carries no timestamps into the files extract_keyframes writes, so
    this is a separate pass and the two are paired by ordinal.
    """
    out = run_tool([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
        "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(video),
    ])
    times = []
    for line in out.splitlines():
        text = line.strip().rstrip(",")
        if not text:
            continue
        try:
            times.append(float(text))
        except ValueError:
            raise ReelsError(f"ffprobe reported an unreadable keyframe time in {video}: {text!r}") from None
    if not times:
        raise ReelsError(f"no keyframes found in {video}")
    return np.asarray(times, dtype=np.float64) - start_time


def extract_keyframes(video: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    run_tool([
        "ffmpeg", "-v", "error", "-skip_frame", "nokey", "-i", str(video),
        "-map", "0:v:0", "-vf", f"scale={KEYFRAME_SIZE}:{KEYFRAME_SIZE}",
        "-fps_mode", "passthrough", "-q:v", KEYFRAME_JPEG_QUALITY,
        str(dest / "%06d.jpg"), "-y",
    ])
    frames = sorted(dest.glob("*.jpg"))
    if not frames:
        raise ReelsError(f"no keyframes extracted from {video}")
    return frames


@lru_cache(maxsize=1)
def _load_model():
    """Cached: a first-run `reels clip` embeds images then encodes the query, and
    rebuilding the same checkpoint for the second call costs seconds and ~600 MB."""
    import open_clip
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=PRETRAINED, device=device
    )
    model.eval()
    return model, preprocess, device


def encode_images(paths: Sequence[Path], batch_size: int = 64) -> np.ndarray:
    import torch
    from PIL import Image

    model, preprocess, device = _load_model()
    out = []
    with torch.no_grad():
        for start in range(0, len(paths), batch_size):
            batch = torch.stack([
                preprocess(Image.open(p).convert("RGB")) for p in paths[start:start + batch_size]
            ]).to(device)
            feats = model.encode_image(batch)
            out.append((feats / feats.norm(dim=-1, keepdim=True)).cpu().numpy())
    return np.concatenate(out).astype(np.float32)


def encode_text(query: str) -> np.ndarray:
    """A single L2-normalised query vector, in the same space as encode_images."""
    import open_clip
    import torch

    model, _, device = _load_model()
    tokens = open_clip.tokenize([query]).to(device)
    with torch.no_grad():
        feats = model.encode_text(tokens)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats.cpu().numpy().astype(np.float32)[0]


def load_index(video: Path | str) -> Index:
    cache = index_path(video)
    if not cache.is_file():
        raise ReelsError(f"no index for {video} -- run `reels index` first")
    with np.load(cache) as data:
        duration = float(data["duration"]) if "duration" in data else 0.0
        return Index(data["embeddings"], data["timestamps"], duration)


def build_index(video: Path | str, *, encoder: Encoder | None = None, force: bool = False) -> Index:
    """Index a recording, or return the existing index when it is still current."""
    source = Path(video)
    if not source.is_file():
        raise ReelsError(f"not a file: {source}")
    if not force and is_fresh(source):
        return load_index(source)

    start_time, duration = probe_times(source)
    times = keyframe_timestamps(source, start_time)
    scratch = Path(tempfile.mkdtemp(prefix="reels-keyframes-"))
    try:
        frames = extract_keyframes(source, scratch)
        if len(frames) != len(times):
            raise ReelsError(
                f"keyframe count mismatch for {source.name}: ffprobe reported {len(times)} "
                f"timestamps but ffmpeg extracted {len(frames)} frames"
            )
        embeddings = (encoder or encode_images)(frames)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    cache = index_path(source)
    staging = cache.with_suffix(".npz.partial")
    try:
        # Write through a handle: np.savez appends ".npz" to a path that lacks it.
        with staging.open("wb") as handle:
            np.savez(
                handle, embeddings=embeddings, timestamps=times, duration=duration,
                model=MODEL_NAME, pretrained=PRETRAINED, source_size=source.stat().st_size,
            )
        staging.replace(cache)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return Index(embeddings, times, duration)
