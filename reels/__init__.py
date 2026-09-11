"""Turn long Minecraft recordings into 9:16 Reels from a text query."""

from __future__ import annotations

import subprocess


class ReelsError(Exception):
    """A failure the user can act on. The CLI prints these without a traceback."""


def run_tool(cmd: list[str], *, text: bool = True, what: str | None = None):
    """Run ffmpeg or ffprobe, turning a failure into a ReelsError carrying its last stderr line.

    ffmpeg reports the useful part of a failure on the last line of stderr, so every
    call site wants the same treatment; `check=True` would throw that line away.
    """
    proc = subprocess.run(cmd, capture_output=True, text=text, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr if text else proc.stderr.decode(errors="replace")
        detail = stderr.strip().splitlines()
        raise ReelsError(f"{what or cmd[0]} failed: {detail[-1] if detail else proc.returncode}")
    return proc.stdout
