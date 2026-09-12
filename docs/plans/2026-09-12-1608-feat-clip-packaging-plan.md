---
title: Clip Packaging Layer - Plan
type: feat
date: 2026-09-12
topic: clip-packaging
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Clip Packaging Layer - Plan

**Written to be handed to an implementer who has not worked on this repo.** Read
`docs/DESIGN.md` and `CLAUDE.md` first — they carry the measurements behind every existing
threshold and which decisions are settled. This plan adds a layer on top of the pipeline;
it does not change how clips are found.

## Goal Capsule

- **Objective:** After rendering a clip, the creator has everything needed to post it — a title they picked from real options, a cover image, and a description — without writing any of it themselves or leaving the tool.
- **Means:** A `packaging.py` module that turns a rendered clip plus the query that found it into title candidates, cover images and a description, written to an export folder beside the clip; driven from the existing React UI through new API endpoints (KTD1, KTD5).
- **Product authority:** This plan owns packaging and export for clips reels already produces. It does not change search, judging, or reframing. The only edit outside the packaging surface is U1's one-line query sidecar in `render.py`.
- **Execution profile:** Additive. One new library module, one small shared module, new endpoints in an existing FastAPI app, one new React panel. No migrations, no change to the index format.
- **Stop conditions:** Stop at U4's pre-registered checkpoint if titles from frames plus query are not usable — see U4 for the exact bar and for what ships on a partial pass. Everything after U4 rests on that premise.
- **Tail ownership:** The creator runs this locally and pastes the output into YouTube Shorts or Instagram Reels themselves. Nothing is published by this tool.

---

## Product Contract

### Summary

A packaging layer over clips reels has already rendered: ten title candidates scored and ranked, cover images built from real frames of the clip, and a description. Everything lands in a folder beside the clip as files you can open and copy. The model proposes; the creator picks.

### Problem Frame

Getting a postable clip out of a long recording is solved. What remains is the work after: a title that survives a phone's 50-character cut-off, a cover image, a description. It happens every time, it is the same shape every time, and it is the part most likely to get skipped — which is how a good clip gets posted as "recording 4 final.mp4".

Upstream research found a working version of this in OpenShorts (see Sources) and two techniques worth taking. It also found the reason a direct port fails twice over: their titles lean on a speech transcript that clips from this tool do not have, and their thumbnails are 16:9 because they package landscape uploads, where this tool produces vertical clips.

### Key Decisions

- **Cover images are built from real frames of the clip; generated backgrounds are opt-in.** *(session-settled: user-directed — chosen over generating every cover with an image model: a frame is free, always depicts footage that is actually in the clip, and removes the per-image cost, the cost estimate, and the risk of a cover showing something the clip does not contain.)* Governs R5, R6, R7.
- **Export a bundle; never publish.** *(session-settled: user-directed — chosen over the YouTube Data API and over upload-post.com: publishing costs an OAuth consent flow and a verification review, or a third-party account that sees the content and can change its terms. Pasting is already the creator's workflow.)* Governs R10, R11, R12.
- **Package clips, not candidates.** Packaging runs after the creator has chosen a clip. Generating titles for twenty candidates they will discard is nineteen wasted rounds.
- **The model proposes, the creator picks.** Ten titles, several covers, ranked but not chosen. The same principle the review UI applies to the judge: model taste is advice, never a gate. Governs R2, R6.
- **UI first, library underneath.** *(session-settled: user-directed — chosen over CLI-first and over both-at-once: choosing between cover images in a terminal is the wrong medium, and the UI already exists to extend.)* Governs R13, R14.

### Requirements

**Provenance**

- R1. A rendered clip records the query it was found by, so packaging can use it in a later session.

**Titles**

- R2. Given a clip and its query, the tool produces ten ranked title candidates, with the ranking's reasoning visible for at least the top two.
- R3. Every candidate fits YouTube's mobile display: the subject appears within the first 50 characters and no title exceeds 65.
- R4. Each title carries a matching short cover text (1–4 words) that complements it rather than repeating it.

**Covers**

- R5. Cover images are built from frames of the clip itself, with the cover text drawn by the tool.
- R6. Several covers are offered from different frames; the creator chooses, and may ask for another round.
- R7. Cover text stays legible over any frame, including a busy one.
- R8. Covers match the clip's own aspect ratio, so they suit the vertical surfaces the clip is destined for.
- R9. A cover is under 2 MB, so it is accepted as a custom thumbnail without further processing.

**Export**

- R10. Packaging a clip writes a bundle beside it containing the chosen title, the description, the chosen cover, and the alternatives.
- R11. The bundle is readable and copy-pastable without running the tool — plain files, not a database or a proprietary format.
- R12. An export never overwrites a previous one for the same clip.

**Surface**

- R13. Packaging is reachable from the UI for any clip the tool has rendered.
- R14. Long-running packaging work reports progress and failure the same way indexing and rendering already do.
- R15. Packaging failures never damage or remove the clip they were packaging.

**Honesty**

- R16. Titles, cover text and descriptions describe what the sampled frames show. A candidate the model cannot ground in a frame is not offered.
- R17. Generated text is marked as model-written in the bundle, so a reader later knows it was not observed by a person.
- R18. When optional image generation is used, the output records that the cover is a generated image rather than a frame of the clip.

### Key Flows

- F1. Package a rendered clip
  - **Trigger:** Creator has a clip in `out/` they intend to post.
  - **Steps:** Choose the clip in the UI; the tool samples frames and proposes ten titles; creator picks one; the tool builds covers from the best frames with the cover text drawn on; creator picks one; the tool writes the bundle.
  - **Outcome:** A folder beside the clip with everything needed to post.
  - **Covers R2, R5, R6, R10, R13.**

- F2. Another round after a poor proposal
  - **Trigger:** None of the ten titles, or none of the covers, is usable.
  - **Steps:** Creator asks for another round, optionally typing a steer in their own words.
  - **Outcome:** New candidates; the previous round is still on screen.
  - **Covers R6.**

- F3. Packaging without an API key
  - **Trigger:** Creator opens packaging with no `GEMINI_API_KEY` set.
  - **Steps:** Titles and description are unavailable and the UI says so. Cover building still works — it needs no model. The creator types their own title and exports.
  - **Outcome:** No dead end. Covers and export work offline.
  - **Covers R5, R14, R15.**

### Acceptance Examples

- AE1. **Covers R1.** Given a clip rendered in an earlier session, when it is packaged today, then the query it was found by is available without the creator retyping it.
- AE2. **Covers R3.** Given a generated set of titles, when each is measured, then none exceeds 65 characters and each one's subject appears within the first 50.
- AE3. **Covers R4.** Given a title and its cover text, when both are read, then the cover text is not contained in the title.
- AE4. **Covers R7.** Given a cover built over a visually busy frame, when the cover text is drawn, then it remains legible against that background.
- AE5. **Covers R8, R9.** Given any exported cover, when it is measured, then its aspect ratio matches the clip's and it is under 2 MB.
- AE6. **Covers R12.** Given a clip packaged yesterday, when it is packaged again, then yesterday's bundle still exists and is still readable.
- AE7. **Covers R15.** Given packaging fails partway, when the error surfaces, then the clip file and any previous bundle are untouched.
- AE8. **Covers R16.** Given the model proposes a title naming something absent from every sampled frame, when candidates are returned, then that candidate is not among them.
- AE9. **Covers R17.** Given a description is exported, when the bundle is read, then it is marked as model-written.

### Success Criteria

- The creator posts a clip using a tool-proposed title, unedited. Counted over the last ten posts, not felt.
- Packaging end to end takes less time than writing a title, making a cover and writing a description by hand would have. Compare like with like; a title alone is not the baseline.
- The creator stops opening an image editor to make covers.
- An implementer unfamiliar with this repo can build this without asking the author a question.

### Scope Boundaries

**Deferred for later**

- A CLI surface for packaging. The library layer is built to carry one; nothing here blocks it.
- Packaging several clips in one batch.
- Remembering which titles the creator picked, to steer later rounds.

**Outside this tool's identity**

- Publishing, scheduling and OAuth against any platform. Export is the boundary.
- Subtitles and burned-in captions — there is no speech to caption.
- Face detection and presenter-centric covers. The footage has no presenter.
- Analytics or anything that reads back from a platform.

### Dependencies / Assumptions

- Gemini is already a dependency (`google-genai`); packaging adds a text model. The optional image path (U9) adds an image model.
- **Titles will be weaker than transcript-driven tools produce.** Upstream reads a full transcript; this has frames plus the creator's own query. Known and accepted, not a defect to fix later.
- `GEMINI_API_KEY` is the same key the judge uses. No second credential.
- Cover building, bundle export, and the whole UI work without a key. Only titles and descriptions need one.
- The creator packages a handful of clips a week, so per-clip latency matters more than throughput.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **`packaging.py` sits beside `variants.py`, above `render.py`.** It consumes a clip path plus the query and returns plain dataclasses. It does not import `search.py`, `judge.py` or `index.py`, so it is testable without CLIP, without an index and without the vision judge. U1 makes this possible by extracting the shared frame grab; see KTD2. Governs R2, R5, R10.
- KTD2. **`grab_frame` moves to `reels/frames.py`, imported by both `judge.py` and `packaging.py`.** It currently lives in `judge.py`, which imports `search.py`, which imports `index.py` — so reusing it in place would drag the whole CLIP chain into packaging and break KTD1's testability claim. The function is a dozen lines and has no judge-specific logic. Governs R5.
- KTD3. **Frames come from the source recording at the clip's offsets, not from the rendered clip.** The rendered clip is reframed: in pillarbox mode most of the picture is a blurred duplicate, and in crop mode as little as 22% of the source width survives. Titling a clip from its own letterboxed output describes the blur bars. Fall back to the rendered clip when the source is not resolvable. Governs R2, R16.
- KTD4. **Frames, not video, go to the text model.** Gemini bills video at roughly 300 tokens per second, so a 45-second clip is ~13k tokens for what is a text task, and it grows with duration. Ten sampled JPEGs are a few thousand tokens and stay flat however long the clip runs. At the short end of the range the two are close; the property that matters is that frames do not grow. Governs R2.
- KTD5. **Titles take two calls: brainstorm, then critic.** One call asked to be creative *and* selective returns ten variations of one idea; a wide brainstorm followed by a separate scoring pass returns ten different angles. The critic also enforces grounding (R16). This is the highest-value technique from the upstream research. Governs R2, R3, R4, R16.
- KTD6. **A cover is a real frame with text drawn on it.** *(session-settled: user-directed.)* The tool picks visually distinct, sharp frames, then composites the cover text with Pillow. This is free, always depicts the actual clip, and removes the per-image cost, the cost estimate and the hallucinated-scene risk in one move. Generated backgrounds remain available as an opt-in (U9). Governs R5, R6, R7.
- KTD7. **The cover text always carries its own contrast.** A gameplay frame is busy everywhere; there is no reliable negative space. The compositor draws a scrim or a heavy stroke behind the text unconditionally rather than assuming a clean region exists. Governs R7.
- KTD8. **Covers match the clip's aspect ratio, read from the clip itself.** Not 1280×720. That number is correct for landscape YouTube uploads, which is what the upstream project packages; this tool renders 1080×1920 for vertical surfaces. Derive the dimensions from the clip rather than hardcoding either. Governs R8.
- KTD9. **The bundle is a directory of plain files, one per clip, named from the clip.** A JSON sidecar is unreadable at 2am and a database is unopenable. Markdown plus images can be read, copied from and diffed. Governs R10, R11, R12.
- KTD10. **Every model call is injectable, exactly as `judge(ask=...)` and `build_index(encoder=...)` already are.** This is the established seam in this repo and the only reason its suite runs with no network and no GPU. Packaging must not break that property.

### High-Level Technical Design

Where packaging sits. It reads a rendered clip and the source; nothing reads it.

```
index.py -> search.py -> judge.py --+
                                    |    frames.py  (shared frame grab, KTD2)
                                    v        |
                              render.py -----+--> out/clip.mp4 + clip.query
                                    ^        |
                variants.py --------'        v
                                      packaging.py --> out/clip.package/
        cli.py --+-- api.py -- web/
```

The packaging sequence. The creator's choice sits between the cheap step and the rest:

```
clip.mp4 + clip.query
   |
   +-- sample ~10 frames from the SOURCE at the clip's offsets   local, free
   |
   +-> [text] brainstorm ~25 titles across styles                one call
   +-> [text] critic: score, drop ungrounded, pick 10, pair text  one call
   |
   *** creator picks a title ***
   |
   +-- rank frames (sharpness, difference) and pick N            local, free
   +-- composite cover text with a scrim                         local, Pillow
   |
   +-> [text] description                                        one call
   |
   +-- write bundle                                              local
```

Three text calls total, no image calls, nothing that costs money per cover. U9 adds an
optional image path for creators who want a designed background.

Title constraints, directional:

```
MOBILE_CHARS = 50   # YouTube truncates around here on a phone
MAX_CHARS    = 65   # hard ceiling

drop a candidate when len(title) > MAX_CHARS
drop a candidate when its subject does not appear within MOBILE_CHARS
drop a candidate the critic cannot tie to a specific sampled frame   # R16
```

Frame ranking for covers, directional:

```
score = sharpness(frame)                      # variance of Laplacian, or similar
pick N frames maximising score while staying visually far apart
```

### Alternatives Considered

- **Generate every cover with an image model** (the upstream approach). Rejected as the default: it costs money per cover, forces a price estimate the API does not return, depends on the model honouring a "leave this side empty" instruction, and can produce a cover depicting something the clip does not contain. Kept as opt-in in U9.
- **Sample frames from the rendered clip** rather than the source. Rejected: the rendered clip is reframed, so the model would describe blur bars or a 22%-wide crop (KTD3).
- **Hardcode 1280×720 covers.** Rejected: correct for landscape uploads, wrong for this tool's vertical output (KTD8).

### Output Structure

```
reels/
  frames.py             # grab_frame, moved out of judge.py (KTD2)
  packaging.py          # titles, frame ranking, cover compositing, description, bundle
  assets/
    <headline>.ttf      # vendored, open-licensed; see U5
tests/
  test_frames.py
  test_packaging.py
web/src/components/
  PackagePanel.tsx
```

A bundle, written beside the clip:

```
out/
  <clip>.mp4
  <clip>.query          # one line, written at render time (U1)
  <clip>.package/
    README.md           # chosen title and description, ready to copy
    cover.jpg           # the chosen one
    alternatives/
      titles.md         # the ten, with the critic's reasoning
      cover-2.jpg
      cover-3.jpg
```

---

## Implementation Units

### U1. Shared frame access and query provenance

- **Goal:** `grab_frame` is importable without the CLIP chain, and a rendered clip remembers the query that found it.
- **Requirements:** R1. KTD2. Covers AE1.
- **Dependencies:** none.
- **Files:** `reels/frames.py`, `reels/judge.py`, `reels/render.py`, `reels/api.py`, `tests/test_frames.py`, `tests/test_render.py`, `tests/test_api.py`
- **Approach:**
  1. Move `grab_frame` from `judge.py` into a new `reels/frames.py`. Update `judge.py` to import it. No behaviour change; the existing judge tests must still pass untouched.
  2. In `render.render`, write `<output stem>.query` beside the clip containing the query string, one line, UTF-8.
  3. Extend `/api/clips` to return the query for each clip, reading the sidecar; return `null` when absent, since clips rendered before this change have none.
- **Execution note:** Do this first and keep it mechanical. It is the only unit that edits existing modules, and everything downstream depends on both halves.
- **Patterns to follow:** `reels/render.py` `output_path` for how a name is derived from the clip.
- **Test scenarios:**
  - `frames.grab_frame` returns JPEG bytes for a valid timestamp, and raises `ReelsError` for an unreadable file.
  - Importing `reels.packaging` does not import `reels.index` — assert on `sys.modules` after a fresh import. This is the property KTD1 promises and the only mechanical way to keep it true.
  - Rendering a clip writes a `.query` sidecar whose contents match the query exactly, including spaces and punctuation.
  - A query containing a newline is stored and read back without corrupting the file.
  - `/api/clips` returns the query for a clip that has a sidecar and `null` for one that does not.
  - Covers AE1. A clip whose sidecar exists is packageable with no query supplied by the caller.
- **Verification:** The existing suite passes unchanged after the move, and a freshly rendered clip has a readable `.query` file beside it.

### U2. Packaging module and export bundle

- **Goal:** A bundle can be written from hand-supplied text and images, with no model involved.
- **Requirements:** R10, R11, R12, R15, R17. KTD9. Covers AE6, AE7, AE9.
- **Dependencies:** U1.
- **Files:** `reels/packaging.py`, `tests/test_packaging.py`
- **Approach:**
  1. Define `Package` — chosen title, description, title alternatives with reasoning, cover paths.
  2. `write_bundle(clip_path, package, out_dir)` creates `<clip stem>.package/`, writes `README.md`, copies the chosen cover to `cover.jpg` and the rest under `alternatives/`.
  3. Mark model-written text as such in the README (R17).
  4. On a name collision, add a numeric suffix — never overwrite. Mirror `render.output_path` rather than inventing a second convention.
  5. Write through a staging directory and rename, so an interruption leaves no half-bundle. Mirror `index.build_index`. Remove a stale staging directory on the next run rather than letting them accumulate.
- **Execution note:** Build this completely before any model call. Everything after can then be tested against a real bundle writer with stubs above it.
- **Patterns to follow:** `reels/render.py` `output_path`; `reels/index.py` `build_index` staged write.
- **Test scenarios:**
  - A bundle written from a hand-made `Package` contains README, cover and alternatives at the documented paths.
  - Covers AE6. Packaging the same clip twice leaves the first bundle intact and readable.
  - An interrupted write leaves no partial bundle directory; a stale staging directory from a previous crash is cleaned up.
  - Covers AE7. A failure during bundling leaves the source clip untouched.
  - A `Package` with no cover and no description still writes a valid bundle — the key-less path.
  - Covers AE9. The README marks model-written text as such.
  - README content contains the title and description verbatim, so it can be copied without the tool.
- **Verification:** A bundle opens in a file manager and its README reads correctly in any editor.

### U3. Source-grounded frame sampling

- **Goal:** Ten representative frames of what the clip actually shows.
- **Requirements:** R2, R16. KTD3, KTD4.
- **Dependencies:** U1, U2.
- **Files:** `reels/packaging.py`, `tests/test_packaging.py`
- **Approach:**
  1. Resolve the source recording and the clip's offset into it. Both are derivable from the clip's name and its `.query` sidecar's neighbours; if the source cannot be resolved, fall back to the rendered clip and record which was used.
  2. Sample evenly across the span with `frames.grab_frame`, excluding the extreme ends.
  3. Compute the timestamps directly — do not reuse `judge.sample_frames`, which takes a `Candidate` from `search.py` and would reintroduce the import KTD1 forbids.
  4. Get duration from `index.probe_times` only if the source is being read; prefer `ffprobe` directly to keep KTD1's no-index-import property.
- **Test scenarios:**
  - Sampling returns the requested count, each with a JPEG magic header.
  - Timestamps are inside the span and strictly increasing.
  - When the source recording is available, frames come from it — assert the frame dimensions match the source, not 1080×1920.
  - When the source is missing, sampling falls back to the rendered clip and records that it did.
  - A zero-length or unreadable input raises `ReelsError`.
- **Verification:** Frames sampled for a real clip visibly differ from one another and are not letterboxed.

### U4. Title generation

- **Goal:** Ten ranked, grounded title candidates with paired cover texts.
- **Requirements:** R2, R3, R4, R16. KTD5, KTD10. Covers AE2, AE3, AE8.
- **Dependencies:** U3.
- **Files:** `reels/packaging.py`, `tests/test_packaging.py`
- **Approach:**
  1. Call one: brainstorm ~25 candidates across several named angles, given the frames and the query.
  2. Call two: a critic that scores candidates, drops any breaching the length rules, **drops any it cannot tie to a specific sampled frame (R16)**, and returns the best ten with no two sharing an angle, each paired with a 1–4 word cover text that complements rather than repeats.
  3. Return the reasoning for the top two, and the supporting frame index for every candidate, so the UI can show what a title is based on.
  4. Structured output, validated before use. A malformed response is an error, not an empty list — the rule `judge._to_verdict` follows.
  5. Injectable `ask` parameter (KTD10).
  6. Enforce the length and grounding rules in code as well as in the prompt. A model told to stay under 65 characters will sometimes not.
- **Execution note:** This unit carries the Goal Capsule's stop condition. **Pre-register the bar before running it:** across 3 clips × 10 candidates, at least one title per clip that the creator would post unedited. Report the count.
  - **3 of 3** — continue as planned.
  - **1 or 2 of 3** — a partial pass. Ship U4 and U6 (titles and description are cheap and useful even when imperfect), and treat U5's covers as the priority since they do not depend on title quality. Do not proceed to U9.
  - **0 of 3** — stop and report. The frames-plus-query premise is too thin, and prompt tuning will not recover it.
- **Test scenarios:**
  - Covers AE2. Every returned candidate is within 65 characters.
  - A model response containing an over-length title has it filtered, not passed through.
  - Covers AE3. Each cover text is not contained in its title.
  - Covers AE8. A candidate whose supporting frame index is missing or out of range is dropped, not returned.
  - Ten candidates returned when the model offers more; the cap holds.
  - Fewer than ten from the model is returned as-is, never padded with duplicates.
  - A malformed response raises `ReelsError` naming what was wrong.
  - A transport failure surfaces rather than returning an empty list — silence would read as "no good titles exist".
  - A title whose subject appears only after character 50 is rejected.
  - An optional steer string reaches the brainstorm prompt (F2).
- **Verification checkpoint:** As pre-registered above. Report the count before building U9.

### U5. Cover images from real frames

- **Goal:** Several covers, each a real frame of the clip with the cover text drawn on legibly.
- **Requirements:** R5, R6, R7, R8, R9. KTD6, KTD7, KTD8. Covers AE4, AE5.
- **Dependencies:** U3. Deliberately does **not** depend on U4 — covers work without a key.
- **Files:** `reels/packaging.py`, `reels/assets/<font>.ttf`, `tests/test_packaging.py`
- **Approach:**
  1. Rank the sampled frames: prefer sharp frames, and pick N that are visually far apart so the creator sees genuinely different options rather than three near-identical stills.
  2. Read the clip's own dimensions and produce covers at that aspect ratio (KTD8). Do not hardcode 1280×720.
  3. Composite the cover text with Pillow using a **vendored** font under `reels/assets/` — an open-licensed condensed bold face, committed to the repo with its licence. Pillow ships no TrueType face and a system font path differs between the author's machine and CI.
  4. Draw a scrim or heavy stroke behind the text unconditionally (KTD7). A gameplay frame is busy everywhere; do not assume clean space exists.
  5. Wrap text that exceeds the available width rather than letting it overflow.
  6. Save as **JPEG**, stepping quality down until under 2 MB. PNG is lossless and has no quality knob, so "compress until under 2 MB" is not implementable against it.
- **Execution note:** Everything here is local and free. Test it hard — this is the unit the creator interacts with most and the one that works even with no API key.
- **Test scenarios:**
  - Covers AE5. A produced cover matches the clip's aspect ratio and is under 2 MB.
  - A deliberately noisy source image still produces a file under 2 MB — assert on the file, not the intent.
  - Covers AE4. Text drawn over a maximally busy frame retains a measurable contrast margin against the pixels behind it.
  - Text longer than the available width wraps rather than overflowing the frame.
  - Ranking returns N visually distinct frames from a sequence containing near-duplicates.
  - The vendored font loads from a path relative to the module, not from a system location.
  - A clip whose dimensions are unreadable raises `ReelsError` rather than defaulting to a guess.
- **Verification:** Three covers for a real clip are recognisably different frames, each with legible text, opened and eyeballed.

### U6. Description generation

- **Goal:** A description grounded in the frames, honest about being model-written.
- **Requirements:** R17. KTD10.
- **Dependencies:** U3, U4 — it takes the chosen title as input.
- **Files:** `reels/packaging.py`, `tests/test_packaging.py`
- **Approach:**
  1. One text call from the frames, the query and the chosen title.
  2. No chapters. Chapters come from transcript timestamps upstream; there is no transcript here, and inventing timestamps would be fabrication.
  3. Keep it short — a Reels description is a couple of lines and some tags.
  4. Injectable `ask` (KTD10).
- **Test scenarios:**
  - A description is returned for a stubbed response and appears verbatim in the bundle.
  - No timestamp-shaped chapter markers appear in the output.
  - A malformed response raises `ReelsError`.
  - An over-long description is truncated at a sentence boundary, not mid-word.
- **Verification:** A description for a real clip claims nothing the frames do not show.

### U7. API endpoints

- **Goal:** The UI can drive packaging over HTTP.
- **Requirements:** R13, R14.
- **Dependencies:** U4, U5, U6.
- **Files:** `reels/api.py`, `tests/test_api.py`
- **Approach:**
  1. `POST /api/package/titles` — clip name, optional steer; the query comes from the sidecar. Returns a job.
  2. `POST /api/package/covers` — clip name, cover text, count. Returns a job; result is cover paths.
  3. `POST /api/package/export` — chosen title, description, chosen cover. Writes the bundle, returns its path.
  4. `GET /media/package` — serve a cover for preview.
  5. **Covers are written under `settings.out_dir`** — specifically `out/<clip stem>.package.tmp/` — so the existing `resolve(name, settings.out_dir)` guard works unchanged. `resolve()` requires an existing file under a configured root; a scratch directory elsewhere could not be guarded, and a bundle directory would be rejected because it is not a file.
  6. Export moves the chosen cover out of the staging directory rather than copying from a client-supplied path.
  7. Reuse the existing job pool and polling shape. Do not add a second progress mechanism.
- **Patterns to follow:** `reels/api.py` — `submit()`, `resolve()`, and the render endpoint are the template.
- **Test scenarios:**
  - Titles, covers and export each return a job id that reaches a terminal state.
  - A path outside the output directory is refused on every new endpoint, including the media route.
  - A failing model call becomes a failed job with a readable error, not one stuck on running.
  - Covers and export succeed with no API key set — only titles and description require one.
  - A clip name that does not exist returns 404, not 500.
  - The titles endpoint reads the query from the sidecar and returns a clear error when neither sidecar nor supplied query exists.
- **Verification:** Driving the sequence with curl against a running server produces a bundle on disk.

### U8. Package panel in the UI

- **Goal:** The creator packages a clip by looking and choosing.
- **Requirements:** R6, R13, R14.
- **Dependencies:** U7.
- **Files:** `web/src/components/PackagePanel.tsx`, `web/src/api.ts`, `web/src/App.tsx`
- **Approach:**
  1. Add a package action to each rendered clip in `ClipList`.
  2. Title candidates as a selectable list, with the top two's reasoning and each candidate's supporting frame visible.
  3. Covers as a grid; selecting one and exporting writes the bundle and shows its path.
  4. An "another round" control that optionally takes a steer, keeping the previous round on screen (F2).
  5. When `judge_available` is false, disable only the title and description actions with an explanatory tooltip — covers and export stay live. Match how the existing "ask the model" button behaves.
- **Patterns to follow:** `web/src/components/VariantPanel.tsx` for panel shape and job handling; `web/src/App.tsx` `run()` for the busy/error pattern.
- **Test scenarios:** none automated — this repo has no frontend test harness and adding one is out of scope. `npm run build` type-checks via `tsc -b`, which CI runs.
- **Verification:** With a key, a clip is packaged end to end in the browser. Without one, covers and export still work and the UI explains what is unavailable.

### U9. Optional generated cover backgrounds

- **Goal:** A creator who wants a designed background instead of a frame can have one.
- **Requirements:** R18. KTD6.
- **Dependencies:** U5. **Build last, and only if U4's checkpoint passed 3 of 3.**
- **Files:** `reels/packaging.py`, `tests/test_packaging.py`, `web/src/components/PackagePanel.tsx`
- **Approach:**
  1. Text call: plan N concepts for the chosen title, each naming a scene that describes only what the sampled frames actually show (R16).
  2. Image call per concept. Reuse U5's compositor for the text — the image model never draws text (KTD7 applies here too, since it may ignore any negative-space instruction).
  3. Record in the bundle that this cover is generated, not a frame of the clip (R18).
  4. Pin the image model id in a `DEFAULT_IMAGE_MODEL` constant with an env override, and note in `docs/DESIGN.md` that this one is pinned deliberately — unlike the judge's alias, image models are not generally published under a floating alias.
  5. Report the **number of image calls** before starting, not a dollar figure. The API returns usage metadata, not money; a hardcoded price against a model id drifts silently and a confidently wrong estimate is worse than none.
- **Execution note:** This is the only unit that costs money to exercise. Everything above works without it; if U4's checkpoint was a partial pass, skip this unit entirely.
- **Test scenarios:**
  - A stubbed image response plus a concept produces a cover with the text composited by the tool, not present in the stub.
  - Covers AE5. A generated cover matches the clip's aspect ratio and is under 2 MB.
  - The bundle records that the cover is generated (R18).
  - One concept failing does not discard concepts that already rendered.
  - The call count is reported before the first image call.
- **Verification:** One manual run produces covers that are recognisably different ideas, each with legible text, each marked as generated in the bundle.

---

## Verification Contract

- `uv run pytest -q` passes. Every unit except U8 adds tests.
- `uv run ruff check .` passes.
- `cd web && npm run build` passes — it runs `tsc -b`, so it type-checks U8.
- Unit tests run with no network and no API key. Every model call is injected; that property is non-negotiable (KTD10) and its loss should fail review.
- The import-isolation test in U1 is the mechanical guard on KTD1. If it starts failing, the layering has broken.
- Two checks are manual because nothing else settles them:
  - U4's pre-registered checkpoint — reported before U9 is considered.
  - U9's single real image run, if U9 is built at all.

## Definition of Done

**Global**

- All units complete and their verification statements hold, except U9 if U4's checkpoint was a partial pass.
- A clip in `out/` is packaged end to end in the browser and the bundle pasted into a platform without editing.
- The suite still runs offline with no key, and covers still build with no key.
- No dead-end code from abandoned approaches remains.
- `docs/DESIGN.md` gains a packaging section, and `CLAUDE.md` gains the layer's architecture notes and gotchas — the vendored font, the JPEG-not-PNG reason, the import-isolation rule, and the source-not-clip frame rule. **U2 owns the `CLAUDE.md` edit; U5 owns the `docs/DESIGN.md` edit.**

**Per unit**

- U1 — existing tests pass unchanged after the move; the import-isolation test holds; a sidecar exists beside a new clip.
- U2 — a bundle survives a re-package and an interrupted write.
- U3 — frames come from the source, at source dimensions.
- U4 — checkpoint reported with its count; length and grounding rules enforced in code, not only in the prompt.
- U5 — covers match the clip's aspect ratio, are under 2 MB, text legible over a busy frame, font loaded from the repo.
- U6 — no fabricated chapters; marked model-written.
- U7 — path guard holds on every new endpoint; covers and export work with no key.
- U8 — works without a key, degrading with an explanation rather than breaking.
- U9 — built only on a full checkpoint pass; covers marked as generated.

---

## Open Questions

**Resolve before implementation**

- Which open-licensed condensed bold font to vendor (U5). Any OFL/SIL face is acceptable; the licence file must be committed alongside it.

**Deferred to implementation**

- How the source recording is resolved from a clip name. The clip stem contains a slugged source stem, which may be enough; otherwise the `.query` sidecar can be extended to record the source path and offset. Decide when building U3.
- Whether the bundle's `alternatives/` directory earns its place. It is cheap to write, but the creator has just seen every alternative in the UI. Keep it for now; drop it if no one ever opens it.

---

## Sources / Research

Findings come from reading a local clone of OpenShorts (MIT) at `~/Projects/pet/openshorts`, and from running it against a 3-minute excerpt of the creator's own footage on 2026-09-12. It is **not** a dependency and nothing is copied; these are techniques and measurements.

| Source | Finding | Used |
|---|---|---|
| `thumbnail.py` `analyze_video_for_titles` | Brainstorm-then-critic in two calls; the 50/65-character mobile rule | KTD5, R3 |
| `thumbnail.py` constants | "Gemini bills video at ~300 tokens/s, so an hour is ~1M tokens for what is a text task" | KTD4 |
| `thumbnail.py` `plan_thumbnail_concepts` | "Asking the image model to invent the concept and render it in one go gives N variations of one idea; splitting it gives N ideas" | U9 only |
| `thumbnail.py` `_generate_one` | Text composited after generation, never drawn by the image model | KTD7 |
| `thumbnail.py` `THUMB_W, THUMB_H = 1280, 720` | **Not used.** Correct for their landscape uploads; wrong for this tool's vertical clips (KTD8) |  |
| `thumbnail.py` `generate_youtube_description` | Chapters from transcript timestamps | **Not used** — no transcript exists here |
| `clip_selection.py` `clip_count_targets` | SaaS retention data on clip counts | **Not used** — "came back a second day" has no meaning for a single-user tool its author wrote |

Observed by running it on the creator's footage: a 3-minute silent Minecraft excerpt produced 2 clips in 175 s for $0.0048, by uploading the whole video to Gemini vision. Its titles were confidently wrong about content — one read "Satisfying Diamond Mining" over footage of plain stone. **That is the failure R16 exists to prevent, and it is why the critic call must drop any candidate it cannot tie to a specific frame.**

Repo context the implementer needs: `docs/DESIGN.md` (why every threshold is what it is, and what is known-weak), `CLAUDE.md` (architecture and gotchas), `reels/variants.py` (the closest existing analogue for a module layered over `render.py`), and `reels/api.py` (`submit` and `resolve` — the job and path-guard patterns every new endpoint must follow).
