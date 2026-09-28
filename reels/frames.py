"""Shared single-frame video access."""

from __future__ import annotations

from pathlib import Path

from . import ReelsError, run_tool

SAMPLE_WIDTH = 768


def grab_frame(video: Path, at: float, *, width: int = SAMPLE_WIDTH) -> bytes:
    """Return one JPEG at ``at`` seconds."""
    jpeg = run_tool(
        ["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-f", "image2", "-c:v", "mjpeg", "-"],
        text=False, what=f"reading a frame at {at:.1f}s from {video.name}",
    )
    if not jpeg:
        raise ReelsError(f"no frame at {at:.1f}s in {video.name}")
    return jpeg
