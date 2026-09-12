# reels

Turn a long screen recording into vertical short-form clips by describing what you want.

```
reels index recording.mp4
reels clip recording.mp4 "a wide forest landscape with trees and sky"
```

You get finished 9:16 MP4s with a music bed, cut at boundaries a vision model approved.

## Why this exists

Every open-source "long video → shorts" tool works the same way: Whisper transcribes the
speech, an LLM picks quotable moments from the transcript, a face-tracker crops to the
speaker. That pipeline is excellent for podcasts and talking-head content.

It has nothing to work with on gameplay, scenery, drone footage, or timelapse. No speech,
no transcript, no face. Those tools don't degrade on silent footage — they have no input
at all.

`reels` searches what the footage **looks like** instead of what someone says in it. You
describe a moment; it finds the moments that match, decides which of them are actually
usable shots, and cuts them.

## How it works

Indexing reads the keyframes a recording already contains rather than decoding the video,
which is the difference between two minutes and ten. On a 75-minute 6.9 GB capture:

| | |
|---|---|
| Keyframes extracted and embedded | 2,248 |
| Index time | ~2 minutes |
| Index size on disk | 4.5 MB |

That index is the durable part. Querying it afterwards is cheap, so one recording answers
"forest", "sunset", "cave with lava" and "the boat bit" for the cost of indexing once.

A query then runs three stages:

1. **Score** every keyframe against the query with CLIP, and reject outright if nothing
   in the recording clears an absolute floor. This is what stops a hopeless query from
   spending API calls.
2. **Find boundaries** from the score curve — where a match rises and falls, not a fixed
   window around the best frame.
3. **Judge** each candidate with a vision model, which answers whether the span is a
   usable shot (horizon and depth rather than a close-up, steady camera, no inventory or
   menu overlay) and where in the frame a vertical crop should sit.

Survivors get cut, reframed to 1080×1920, and mixed with a track from your music folder.

`docs/DESIGN.md` covers why each of those is built the way it is, with the measurements
behind the thresholds.

## Requirements

- Python 3.11+
- `ffmpeg` and `ffprobe` on `PATH`
- A Gemini API key — [free tier](https://aistudio.google.com/apikey) is enough for normal use
- A GPU helps indexing but is not required

## Install

```bash
git clone https://github.com/tungnguyentu/reels.git
cd reels
uv sync
export GEMINI_API_KEY=...
```

## Usage

```bash
# Index a recording once (slow; reused by every later query)
uv run reels index recording.mp4

# Ask for clips. Indexes on demand if you skipped the step above.
uv run reels clip recording.mp4 "a wide forest landscape with trees and sky"
```

Useful flags:

| Flag | Default | What it does |
|---|---|---|
| `--shortlist N` | 20 | How many candidates to send to the judge. Lower it if you hit rate limits. |
| `--min-seconds` / `--max-seconds` | 15 / 45 | Clip length bounds, enforced in real time. |
| `--floor F` | 0.19 | Minimum match score before anything is returned at all. |
| `--music-dir DIR` | `~/Videos` | Where to look for audio. Only audio extensions are considered. |
| `--out-dir DIR` | `out/` | Where finished clips are written. |

Set `REELS_JUDGE_MODEL` to use a different vision model than the default.

When nothing matches, the tool says so and writes nothing — and prints the score it
actually saw, so you can tell "not in this recording" from "scored just under the floor".

## The review UI

```bash
cd web && npm install && npm run build    # once
uv run reels ui                           # opens a browser on 127.0.0.1:8765
```

Search and thumbnails cost nothing, so the UI shows you candidates immediately and you
pick by eye — **it works with no API key at all.** Asking the model is a separate button,
and its verdict is advice next to the thumbnails rather than a gate.

Picking a candidate opens a variant panel. One moment can be rendered several ways at
once: focal-point crop vs blurred pillarbox, a slider that drags the 9:16 window across
the frame with a live overlay showing exactly what it keeps, and a specific music track
per variant. Render them all and choose the one you'd post.

That inverts the judge's role. It is the least reliable part of the pipeline, and in the
UI it stops being load-bearing.

## Limitations

Worth knowing before you rely on it:

- **The judge is the weak link.** Scoring and boundary detection are deterministic and
  testable; whether a span is a *good shot* is a model's opinion. In testing it accepted
  one clearly postable clip and one mediocre one out of six candidates. If it rejects
  things you'd have posted, the prompt in `reels/judge.py` is the thing to tune, not the
  thresholds.
- **The floor is sensitive to query phrasing.** A long descriptive query scores lower than
  a short one on identical footage, so the same clip can pass under `"forest"` and fail
  under `"a wide forest landscape with trees and sky"`. The empty-result message prints
  the observed peak so you can tell which case you're in.
- **Very wide sources crop badly.** A 2.49:1 capture keeps about 22% of its width in a
  9:16 window. `docs/recording-setup.md` covers the capture-side settings that help.
- **No offline mode yet.** The judge requires a hosted API. A local vision-model backend
  behind the same interface is the obvious next step. The UI does not need it — search,
  thumbnails, variants and rendering all run without a key.

## Contributing

Yes please — see `CONTRIBUTING.md`. `docs/DESIGN.md` explains why the current design is
what it is, which is the fastest way to avoid re-litigating a settled decision.

## License

Apache-2.0. See `LICENSE`.
