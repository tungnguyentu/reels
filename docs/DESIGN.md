# Design

Why `reels` is built the way it is. Read this before changing a threshold or the pipeline
shape — most of these decisions have a measurement behind them, and a few were wrong in
ways that only showed up when run against real footage.

Numbers below come from one 75-minute, 3410×1372, 60fps, 6.9 GB Minecraft capture, which
is the reference recording the thresholds were calibrated against.

## The problem

Finding the moment is the easy half. The hard half is deciding where it starts and stops,
and whether it is a *shot* at all.

A semantic match on "forest" fires just as happily on a close-up of leaves two blocks away
as on a vista, and fires on a forest visible behind an inventory screen. Both are correct
matches and neither is usable. Most of a long recording is also dead air — menus, caves,
walking in the dark — regardless of what you asked for.

## Read keyframes, don't decode

Indexing extracts the keyframes the recording already contains (`ffmpeg -skip_frame
nokey`) instead of decoding to a target frame rate.

| Approach | Full 75-minute file | Frames |
|---|---|---|
| Decode at 1 fps | ~5 minutes | 4,500 |
| Keyframe-only | **54 seconds** | 2,248 |

The reference recording writes a keyframe every 2.0 seconds, which is finer than any clip
boundary needs. A recording with a longer keyframe interval indexes just as correctly,
only with coarser boundaries.

Two consequences worth knowing:

- ffprobe and ffmpeg are separate passes over the same file, paired **by ordinal**, with a
  count-mismatch guard. Both read stream `v:0` so they cannot disagree about which stream
  they are describing.
- Keyframe times are absolute presentation timestamps, but `ffmpeg -ss` counts from the
  stream's `start_time`. The offset is subtracted at index time; without that, every cut
  on a container with a nonzero start time lands late.

## No vector database

2,248 embeddings × 512 float32 is about 4.5 MB. Similarity is one matrix product against a
NumPy array. A vector database here would be infrastructure with nothing to do.

The index is a single `.npz` beside the source video, written through a staging file and an
atomic rename so an interrupted write cannot be read back as valid.

It is bound to more than the file's mtime:

- **Source size**, because `cp -p`, `rsync -t`, and archive extraction all preserve mtime.
- **Model name and pretrained tag**, because embeddings from a different checkpoint live in
  a different space. Scoring against them returns confident nonsense rather than an error —
  a silent failure, which is the worst kind.

## Thresholds: two mechanisms, different jobs

### The absolute floor answers "is this here at all"

Without it, nothing can ever return empty. Any relative threshold admits its own top few
percent on every query, so a recording shot entirely underground would still shortlist
twenty candidates for "forest landscape" and spend twenty API calls to learn nothing.

Calibrated on the reference recording with ten queries:

| | Peak smoothed score |
|---|---|
| Subjects present (forest, cave, water, buildings, menus) | 0.218 – 0.317 |
| Subjects absent (city street, food, faces, stadium, office) | 0.120 – 0.164 |

`DEFAULT_FLOOR = 0.19` is the midpoint of that gap.

**This is the least robust number in the codebase.** CLIP similarity is not calibrated
across prompts: a long descriptive query scores lower than a short one on identical
footage. On the test fixture, `"a wide forest landscape with trees and sky"` scores 0.1879
— below the floor — while `"forest"` scores 0.2307. The CLI prints the observed peak when
nothing clears the floor, so the failure is legible rather than silent, but the real fix is
to normalise against a set of control prompts instead of using an absolute number. That is
unbuilt.

### Hysteresis answers "where does this moment start and stop"

Two thresholds, computed as fractions of the query's own dynamic range, with the range
taken from the score **minimum** up to the 99th percentile.

Not percentiles at both ends. Percentiles describe *how many* frames match, so on a
recording where most of the session matches, both ends land inside the matching band, the
span collapses, and hysteresis has no room to work. The minimum is an honest baseline at
any match ratio — the worst-matching frame is a genuine non-match reference.

A range opens at the higher threshold and stays open until the score falls below the lower
one, so a single dipped frame mid-shot does not split one moment into two.

### Lengths are enforced in seconds

`--max-seconds` is a promise about time. Enforcing it as a keyframe count assumes uniform
spacing; on a variable-GOP recording a calm stretch has long gaps between keyframes, and a
fixed count spans far more wall time than asked. A `--max-seconds 45` request could return
an 88-second clip.

A run longer than the maximum is split into several consecutive candidates rather than
reduced to one window, so a session that matches throughout gives the judge a real
shortlist instead of a single arbitrary slice.

## The judge

CLIP finds frames that match the words. It cannot tell a landscape from a close-up of the
same foliage, and it matches a forest visible behind a chest UI.

So a vision model gates each candidate on composition, camera motion, and UI overlays, and
returns where a vertical crop should sit. Its role is deliberately narrow: **CLIP owns the
score curve and the boundaries, the model owns accept/reject and crop focus.** Boundaries
come from per-frame signal rather than a model reading timestamps off thumbnails.

Verdicts are structured output, validated before use. A malformed response is an error, not
a rejection — silently treating a broken reply as "not usable footage" would discard real
clips.

Transient failures retry with backoff. Not just `429`: a live free-tier run lost two of six
ranges to `503 UNAVAILABLE — this model is currently experiencing high demand`, which is as
temporary as a rate limit and more common on shared capacity. Real failures — a bad key, a
malformed request — surface immediately, because retrying those only multiplies the wait.

## Reframing: two paths

A 9:16 window over a 2.49:1 source keeps about 22% of the frame width. `docs/crop-comparison.png`
shows the same frame under a centre crop, a blurred pillarbox, and a tighter crop.

Neither path works alone:

- A **focal-point crop** is right for a distant vista, and destroys a scene that needs its
  full width.
- A **blurred pillarbox** preserves the whole scene as a strip, which is correct for a wide
  subject and looks like a compromise applied to a shot that didn't need it.

So the judge picks per clip. The crop window is computed in both dimensions — a source
taller than 9:16 gets a correctly-shaped crop rather than being stretched to fit.

## Testing

Tests run against a real 90-second fixture cut from the reference recording, re-encoded
with a forced 2.0s keyframe interval. Synthetic video would not exercise the keyframe
assumption the whole indexing design rests on.

Two things are deliberately not covered by unit tests, because nothing but a real run can
settle them: whether the judge's taste is any good, and whether the floor generalises past
the recording it was calibrated on.
