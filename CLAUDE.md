# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                                   # needs ffmpeg + ffprobe on PATH
uv run pytest -q                          # 88 tests, no network or API key needed
uv run pytest tests/test_search.py -q     # one file
uv run pytest -k hysteresis -q            # one test by name
uv run ruff check . --fix
```

Running the tool itself:

```bash
export GEMINI_API_KEY=...                 # judge only; index/search need no key
uv run reels index recording.mp4
uv run reels clip recording.mp4 "a wide forest landscape" --shortlist 6
```

`REELS_JUDGE_MODEL` overrides the vision model. The default is the `gemini-flash-latest`
alias rather than a pinned id, because a pinned version goes stale silently.

## Architecture

A five-stage pipeline, one module per stage, with a linear dependency chain:

```
cli.py → index.py → search.py → judge.py → render.py
```

**`index.py` is the write path and owns the CLIP model.** `search.py` importing
`encode_text` from it is deliberate, not a layering mistake — both encoders share one
`lru_cache`d `_load_model()`, and splitting them would load a ~600 MB checkpoint twice in
a first-run `clip` invocation. The `index.py` / `search.py` split is write-path vs
read-path, and is a structure pin: do not merge them.

**Indexing reads existing keyframes; it never decodes the video.** `ffprobe` and `ffmpeg`
are two separate passes over the same file, paired **by ordinal** with a count-mismatch
guard, both reading stream `v:0`. The image2 muxer carries no timestamps into the written
JPEGs, which is why the timestamp pass exists at all. Timestamps have the stream's
`start_time` subtracted, because `ffmpeg -ss` counts from there rather than from absolute
PTS.

**The division of labour between CLIP and the vision model is the central design fact.**
CLIP owns the score curve and therefore the clip boundaries; the vision model owns only
accept/reject and where a vertical crop should sit. Boundaries come from per-frame signal,
never from a model reading timestamps off thumbnails. Widening the model's role reverses
this.

**Two thresholds in `search.py` do different jobs.** The absolute floor answers "is this
subject in the recording at all"; hysteresis answers "where does this moment start and
stop". The floor is the *only* reason an empty result is reachable — any relative
threshold admits its own top few percent on every query, so removing it means a hopeless
query still shortlists candidates and spends API calls. Hysteresis thresholds are
fractions of the score range measured from the **minimum**, not from a low percentile;
percentiles collapse when most of a recording matches.

**Clip lengths are enforced in seconds, never keyframe counts.** A fixed frame count spans
different wall time on a variable-GOP recording.

**Cache freshness binds more than mtime.** `is_fresh()` also checks source size (`cp -p`
and archive extraction preserve mtime) and the model name plus pretrained tag (embeddings
from another checkpoint live in a different space and score as confident nonsense rather
than failing).

**`run_tool()` in `__init__.py` wraps every ffmpeg/ffprobe call.** `check=False` plus a
manual returncode check is deliberate: it converts failures into a `ReelsError` carrying
ffmpeg's last stderr line, which `check=True` would discard.

`docs/DESIGN.md` carries the measurements behind each threshold, and is explicit about
which decisions are known-weak. Read it before changing a constant.

## Testing

Tests run against `tests/fixtures/forest-90s.mp4` — a real 90-second capture with exactly
45 keyframes at 2.0s intervals. Synthetic video would not exercise the keyframe assumption
the indexing design rests on.

Three injection seams keep tests off the network and GPU: `build_index(encoder=...)`,
`candidates(encoder=...)`, and `judge(ask=...)`. Only
`test_real_clip_embeddings_are_l2_normalised` loads the real model, downloading weights on
first run.

Error paths and cleanup paths are tested, not just happy paths — several exist because a
real failure was observed, so check the test before deleting a guard that looks redundant.

## Gotchas

- `np.savez` appends `.npz` to any path lacking it, which silently defeats a
  `.npz.partial` staging write. `index.py` writes through an open file handle instead.
- Lazy `import torch` / `import open_clip` inside functions is intentional — it keeps
  `--help` and CLI error paths fast.
- Transient provider failures retry with backoff, and that means `503 UNAVAILABLE` as well
  as `429`. A live free-tier run lost candidates to 503 when only 429 was matched.
- `crop_window()` returns four values (`w, h, x, y`), not two.
