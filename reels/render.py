"""Cut, reframe, music bed, encode (U4).

Two reframe paths, chosen per clip by the judge (KTD5). A 9:16 window over a 2.49:1
source keeps about 22% of the width, which is right for a distant vista and destroys a
scene that needs its full width -- so a wide scene is pillarboxed instead of cropped.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import ReelsError
from .judge import CROP, PILLARBOX, Verdict
from .search import Candidate

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
OUTPUT_FPS = 30  # inside the range both Instagram Reels and YouTube Shorts accept
FADE_SECONDS = 1.5
BLUR_SIGMA = 40
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".opus", ".aac"}


def music_tracks(directory: Path) -> list[Path]:
    """Audio files only.

    The music library shares a directory with the source recordings, so a plain media
    glob would pick a multi-gigabyte .mp4 as the bed.
    """
    folder = Path(directory)
    if not folder.is_dir():
        raise ReelsError(f"music directory not found: {folder}")
    tracks = sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS)
    if not tracks:
        raise ReelsError(f"no audio files in {folder} (looked for {', '.join(sorted(AUDIO_EXTENSIONS))})")
    return tracks


def pick_track(tracks: list[Path], seed: str) -> Path:
    """Deterministic, so re-rendering the same span gives the same music."""
    digest = hashlib.sha256(seed.encode()).digest()
    return tracks[int.from_bytes(digest[:8], "big") % len(tracks)]


def probe_size(video: Path) -> tuple[int, int]:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "json", str(video)],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise ReelsError(f"could not read dimensions of {Path(video).name}")
    stream = json.loads(proc.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def crop_window(source_width: int, source_height: int, focal_x: float) -> tuple[int, int]:
    """A 9:16 window centred on focal_x, clamped inside the frame. Returns (width, x)."""
    width = min(source_width, round(source_height * OUTPUT_WIDTH / OUTPUT_HEIGHT))
    width -= width % 2
    centre = focal_x * source_width
    x = round(centre - width / 2)
    return width, max(0, min(x, source_width - width))


def video_filter(verdict: Verdict, source_width: int, source_height: int) -> str:
    if verdict.reframe == PILLARBOX:
        return (
            f"[0:v]split[fg_src][bg_src];"
            f"[fg_src]scale={OUTPUT_WIDTH}:-2[fg];"
            f"[bg_src]scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},gblur=sigma={BLUR_SIGMA}[bg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        )
    if verdict.reframe != CROP:
        raise ReelsError(f"cannot reframe without a mode: {verdict!r}")
    width, x = crop_window(source_width, source_height, verdict.focal_x or 0.5)
    return (
        f"[0:v]crop={width}:{source_height}:{x}:0,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}[v]"
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "clip"


def output_path(out_dir: Path, video: Path, query: str, start: float) -> Path:
    """Named from source, query and start, with a numeric suffix so a re-render never overwrites."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{_slug(Path(video).stem)}-{_slug(query)}-{round(start)}s"
    candidate = out_dir / f"{stem}.mp4"
    counter = 2
    while candidate.exists():
        candidate = out_dir / f"{stem}-{counter}.mp4"
        counter += 1
    return candidate


def render(
    video: Path,
    candidate: Candidate,
    verdict: Verdict,
    query: str,
    *,
    music_dir: Path,
    out_dir: Path,
) -> Path:
    video = Path(video)
    duration = candidate.duration
    if duration <= 0:
        raise ReelsError(f"cannot render a zero-length range from {video.name}")

    track = pick_track(music_tracks(music_dir), f"{video.name}|{query}|{candidate.start}")
    source_width, source_height = probe_size(video)
    fade_start = max(0.0, duration - FADE_SECONDS)
    destination = output_path(Path(out_dir), video, query, candidate.start)

    proc = subprocess.run(
        ["ffmpeg", "-v", "error",
         "-ss", f"{candidate.start:.3f}", "-t", f"{duration:.3f}", "-i", str(video),
         "-stream_loop", "-1", "-i", str(track),
         "-filter_complex", video_filter(verdict, source_width, source_height),
         "-map", "[v]", "-map", "1:a",
         "-af", f"afade=t=out:st={fade_start:.3f}:d={FADE_SECONDS},alimiter=limit=0.9",
         "-t", f"{duration:.3f}",
         "-r", str(OUTPUT_FPS), "-c:v", "libx264", "-crf", "20", "-preset", "medium",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         str(destination), "-y"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        destination.unlink(missing_ok=True)
        detail = proc.stderr.strip().splitlines()
        raise ReelsError(f"rendering failed: {detail[-1] if detail else proc.returncode}")
    return destination
