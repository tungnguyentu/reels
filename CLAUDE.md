# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                                   # needs ffmpeg + ffprobe on PATH
uv run pytest -q                          # 115 tests, no network or API key needed
uv run pytest tests/test_search.py -q     # one file
uv run pytest -k hysteresis -q            # one test by name
uv run ruff check . --fix
```

Frontend (only needed for the UI):

```bash
cd web && npm install && npm run build    # npm, not pnpm -- see Gotchas
uv run reels ui --no-browser              # serves web/dist + the API on 127.0.0.1:8765
cd web && npm run dev                     # Vite dev server, proxies /api to port 8765
```

Running the tool itself:

```bash
export GEMINI_API_KEY=...                 # judge only; index/search need no key
uv run reels index recording.mp4
uv run reels clip recording.mp4 "a wide forest landscape" --shortlist 6
```

`REELS_JUDGE_BACKEND` picks the judge: `gemini` (API key) or `agy` (the Antigravity
CLI, which uses a signed-in Google account instead of a key). Unset, it auto-detects --
so the test suite pins it, or the suite would take a different path on a machine that
happens to have `agy` installed.

`REELS_AGY_MODEL` and `REELS_AGY_TIMEOUT` (seconds) tune the agy backend. agy prints
nothing while it works, so a stalled provider and a slow one look identical from outside;
a measured four-span batch answers in 35-60s, and one observed stall ran past 900s while
the identical call retried immediately took 36s. Shorten the timeout to fail fast.

`REELS_JUDGE_MODEL` overrides the vision model. The default is the `gemini-flash-latest`
alias rather than a pinned id, because a pinned version goes stale silently.

## Architecture

A linear pipeline, one module per stage, plus two layers that sit on top of it:

```
index.py -> search.py -> judge.py -> render.py
                                         ^
                     variants.py --------'     several treatments of one moment
                                      packaging.py --> post-ready export bundle
        cli.py --+-- api.py -- web/            two front ends over one pipeline
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

**The judge prompt names no particular game.** It is shown Call of Duty as readily as
Minecraft, and naming one tells the model something false about the other -- during a live
run agy identified the footage as Warzone unprompted while the prompt insisted it was
Minecraft. The reject rules stay generic for the same reason: "a storage or crafting
screen", not "a chest or furnace".

**The division of labour between CLIP and the vision model is the central design fact.**
CLIP owns the score curve and therefore the clip boundaries; the vision model owns only
accept/reject and where a vertical crop should sit. Boundaries come from per-frame signal,
never from a model reading timestamps off thumbnails. Widening the model's role reverses
this.

**Appeal ranks; it never gates.** The judge also returns `appeal` (0-10) and a one-line
`hook`, and accepted spans are ordered by it so a truncated run keeps the best moments
rather than the earliest. This does not widen the model's role over boundaries: CLIP still
decides where every range starts and stops, and appeal only reorders what CLIP already
found. Keep it out of the accept/reject decision — the model has seen no retention data
for this account, so the score is a prior about what reads as interesting, not a
prediction, and used as a gate it would quietly discard usable footage. A missing score is
`None`, not `0`: a model that never answers must leave the ranking untouched.

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

**The agy backend batches; the Gemini one does not.** `agy` is an agent, not an API
client: each invocation re-reads its harness context, so one image costs ~287k tokens and
~114s while six cost 84k and 28s. `agy.judge_ranges()` therefore writes a batch of spans'
frames into one temp dir and asks for all their verdicts at once, binding each verdict to
its span by an explicit `range` index rather than by reply order. An unanswered span is
raised as a named error, never returned as a rejection -- "the model did not answer" and
"the footage is unusable" must not collapse into the same outcome. `judge_all()` in
`judge.py` returns `Verdict | ReelsError` per candidate rather than raising, which is what
preserves the CLI's per-range error isolation.

**The UI inverts the judge's role, and that is the point.** `api.py` keeps search and
judging as separate endpoints: search and thumbnails are free and need no key, judging is
opt-in per candidate. The judge is the least reliable part of the pipeline, so in the UI
it is advice beside a thumbnail rather than a gate. Do not fold judging back into search.

**`variants.py` sits above `render.py`.** A `Treatment` (reframe mode, focal point, track,
trim) becomes a synthetic `Verdict`, so `render()` needs no knowledge of variants. Track
resolution and the treatment cap are checked before the first encode, because each
treatment is a full re-encode and failing on the fourth of five wastes minutes.

**`packaging.py` sits above `render.py`.** It must not import `index.py`. Shared frame access
is in `frames.py`. Packaging samples source frames when the render sidecar can resolve them,
then falls back to the clip. Covers use a vendored font and JPEG, because JPEG can reduce
quality to stay under 2 MB while PNG cannot.

**Every path arriving from the browser goes through `resolve()` in `api.py`**, which
checks membership in a configured root *after* resolving symlinks rather than inspecting
the string for `..`. Long work runs on a small pool with polled job status; any escaping
exception becomes a failed job, because a stranded `running` status leaves the browser
polling forever.

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
- **Use npm for `web/`, not pnpm.** pnpm 10+ blocks esbuild's postinstall behind an
  interactive approval, moved that setting out of `package.json`, and then fails the
  build on a no-op install. npm runs it, and contributors are likelier to have it.
- `CropPreview` derives the 9:16 window width from the thumbnail's own natural aspect
  ratio, so it is correct for any source without asking the server for dimensions.
- `ffmpeg` reads stdin, so it eats the remaining lines of a `while read` loop. Pass
  `-nostdin` in any shell loop that pipes a list into it.
- `scripts/kill-highlights.py` is deliberately outside `reels/`: it is pixel detection on
  a game HUD, not semantic search, and it shares no code with the pipeline. CLIP cannot do
  this job — the killfeed is smaller than its 224px input and a shooter's similarity curve
  is flat because every frame is shooting. Measured on one 314s match: the detector found
  all 17 kills spread across the whole recording, while `reels clip --all` returned tiles
  covering 4–154s, because a flat curve puts everything over the threshold.
- **`/api/kills` shells out to that script, and must keep shelling out.** Importing it
  would pull HUD pixel matching into the package it was separated from. It is exposed as
  its own UI action rather than as a search backend for the same reason: a query cannot
  reach these moments, and folding it into search would imply it can. The script's
  `--json` exists for this caller; under it, no kills is exit 0 with an empty list.
