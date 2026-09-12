# Contributing

Contributions welcome. This is a small tool with a narrow job, so the most useful thing you
can do before writing code is read `docs/DESIGN.md` — most thresholds and structural
choices have a measurement behind them, and a few are documented as known-wrong with the
reason.

## Setup

```bash
uv sync                      # needs Python 3.11+ and ffmpeg/ffprobe on PATH
uv run pytest                # 88 tests, no network or API key required
uv run ruff check .
```

Tests stub CLIP and the vision API, except one case that loads the real CLIP model to check
embeddings are normalised. That one downloads weights on first run.

## What's most useful

In rough order of how much it would help:

- **A local vision-model backend** behind the same interface as `reels/judge.py`, so the
  tool runs offline. The judge is the only hosted dependency.
- **Floor normalisation.** The absolute score floor is phrasing-sensitive and is the
  weakest number in the codebase — `docs/DESIGN.md` explains why and sketches the fix.
- **Better judge prompting.** Accept/reject quality is the difference between a tool you
  trust and one you double-check. If you have footage where it decides badly, that's a
  useful issue even without a patch.
- **Other sources.** Nothing here is Minecraft-specific; it was just the footage on hand.
  Drone, dashcam, and timelapse have the same no-speech problem.

## Conventions

- **Tests are the argument.** A behaviour change needs a test that fails without it. If a
  bug came from real footage, the test should encode the actual observed values.
- **Comments explain why, not what.** The codebase leans on this — if a constant has a
  number behind it, the number goes in the comment.
- **Don't simplify away a guard.** Validation at the ffmpeg and API boundaries, and the
  cleanup paths that stop a truncated file being left behind, exist because each one was a
  real failure. If one looks redundant, check the tests before removing it.
- Match the surrounding style. `ruff check .` must pass; there is no separate formatter.

## Reporting a bug

The useful details are the ffprobe output for the recording (`ffprobe -v error
-show_entries stream=width,height,r_frame_rate,codec_name -of default=nw=1 file.mp4`), the
exact query, and what the tool printed. Judge disagreements are worth reporting too — say
what it accepted or rejected and what you'd have chosen.

Do not paste API keys into issues.
