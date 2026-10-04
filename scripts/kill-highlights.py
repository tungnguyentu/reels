#!/usr/bin/env python3
"""Build a vertical highlight reel from on-screen kill banners in shooter footage.

This is NOT semantic search. CLIP cannot read a killfeed -- the text is small, and a
whole shooter video looks like "shooting" to it, so the similarity curve is flat. What
works is detecting the banner the game itself draws, at a fixed place, in a fixed style.

The discriminator that matters: the banner text is red AND full of edges, while a damage
flash is a red slab. Measured on a 5-minute Warzone capture, that one feature separated
17 real kills from every false positive (TAB INVENTORY prompts, flashes) with no misses
among what it found.

    scripts/kill-highlights.py match.mp4 --check      # look before you cut
    scripts/kill-highlights.py match.mp4 -o out/

Defaults target 1920x1080 Warzone/MW. Another game or HUD scale needs --region; run with
--check and adjust until the contact sheet shows the banner centred in each tile.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# Banner text zone as fractions of frame size, so it survives a resolution change.
# Derived from 1920x1080: x 880-1030, y 893-927 -- the "NTH KILL" line.
REGION = (880 / 1920, 893 / 1080, 150 / 1920, 34 / 1080)

SAMPLE_FPS = 4.0  # a banner shows for ~2s; 4fps cannot miss one and costs ~20s per 5min
MIN_EDGES = 150  # text is all edges. A flash is a slab: edges ~0.
REFERENCE_HEIGHT = 1080  # MIN_EDGES was measured on a 1080p capture -- see scaled_edges()
MAX_RED_FRACTION = 0.60  # above this the zone is a flash, not text
EVENT_GAP = 2.5  # seconds of quiet that ends one banner
MIN_EVENT = 0.25

PRE_ROLL = 4.0  # the banner appears AFTER the kill; the shot that earned it is before
POST_ROLL = 2.0
MERGE_GAP = 2.0  # windows closer than this become one continuous shot -- see windows()
OUT_W, OUT_H = 1080, 1920
MAX_FPS = 60  # keep the source rate up to here; forcing 30 halves a 60fps capture and
# shows up as judder exactly where a shooter needs it least -- fast camera movement.
GAME_VOLUME, MUSIC_VOLUME = 0.55, 0.85
# Game audio is levelled once, over the finished reel, by the vendored ffmpeg-skill's
# loudness.py: two passes, and it re-encodes lower when the AAC encoder overshoots the
# ceiling. Measured on three finished reels before this existed: +1.3, +1.4 and +3.2 dBTP
# against a platform limit of -1, and one at -10 LUFS. A fixed limiter ceiling could not fix
# that (-0.8 dBTP and 0.8 LU quieter, still failing); per-segment loudnorm is unusable on
# 2-6 s clips. Targets are the TikTok/Shorts ones, with 0.5 dB under the -1 limit.
LOUDNESS_TOOL = (Path(__file__).resolve().parent.parent
                 / ".claude/skills/ffmpeg-skill/scripts/loudness.py")
TARGET_LUFS, TARGET_TRUE_PEAK = -14.0, -1.5


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()
        sys.exit(f"{cmd[0]} failed: {tail[-1] if tail else proc.returncode}")
    return proc.stdout


def probe(video: Path) -> tuple[int, int, float, int]:
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height,r_frame_rate",
               "-show_entries", "format=duration", "-of", "json", str(video)])
    d = json.loads(out)
    st = d["streams"][0]
    try:
        num, _, den = str(st.get("r_frame_rate", "")).partition("/")
        rate = float(num) / float(den or 1)
        fps = max(1, min(MAX_FPS, round(rate))) if rate > 0 else 30
    except (ValueError, ZeroDivisionError):
        fps = 30  # unreadable rate: a conservative one beats failing the whole build
    return int(st["width"]), int(st["height"]), float(d["format"]["duration"]), fps


def banner_features(path: Path) -> tuple[float, int]:
    """(red fraction, edge count) for one cropped banner-zone frame."""
    a = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    red = (r > 130) & (r - g > 55) & (r - b > 55)
    edges = int((red[:, 1:] != red[:, :-1]).sum() + (red[1:, :] != red[:-1, :]).sum())
    return float(red.mean()), edges


@dataclass(frozen=True)
class Kill:
    start: float  # when the banner appears
    end: float


def scaled_edges(min_edges: int, height: int) -> int:
    """MIN_EDGES adjusted for a capture that is not 1080p.

    The banner zone is defined as fractions of the frame, so it shrinks with the source
    while the edge threshold did not: at 720p the zone holds barely half the pixels and a
    real banner cannot reach a count calibrated on 1080p. Measured on a 720p match, the
    unscaled default found 8 banners where 30 were present.

    Scaled by the linear ratio rather than by area, because edges run along text strokes:
    a stroke gets shorter, not thinner, and area would over-correct. This is calibration
    from one capture, not a law -- a 720p source does better around 90 than the 100 this
    produces.

    Only the default is scaled. A number the operator passed was measured on their own
    footage at its own resolution, and scaling it underneath them would mean --min-edges 90
    quietly became 60.
    """
    if height <= 0 or height == REFERENCE_HEIGHT:
        return min_edges
    return max(1, round(min_edges * height / REFERENCE_HEIGHT))


def detect(video: Path, region: tuple[float, float, float, float], fps: float,
           min_edges: int | None, max_fraction: float, workdir: Path) -> list[Kill]:
    w, h, _, _ = probe(video)
    if min_edges is None:
        min_edges = scaled_edges(MIN_EDGES, h)
    rx, ry, rw, rh = region
    crop = f"{max(2, round(rw * w))}:{max(2, round(rh * h))}:{round(rx * w)}:{round(ry * h)}"
    frames = workdir / "zone"
    frames.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(video),
         "-vf", f"crop={crop},fps={fps}", "-q:v", "2", str(frames / "z_%06d.jpg"), "-y"])

    paths = sorted(frames.glob("z_*.jpg"))
    if not paths:
        sys.exit("no frames extracted -- is --region inside the frame?")
    feats = [banner_features(p) for p in paths]
    times = np.arange(len(paths)) / fps
    hot = np.array([e >= min_edges and f < max_fraction for f, e in feats])

    kills, start, prev = [], None, None
    for t, is_hot in zip(times, hot):
        if is_hot:
            if start is None:
                start = t
            prev = t
        elif start is not None and t - prev > EVENT_GAP:
            kills.append(Kill(start, prev))
            start = None
    if start is not None:
        kills.append(Kill(start, prev))
    return [k for k in kills if k.end - k.start >= MIN_EVENT]


def rank_by_appeal(video: Path, kills: list[Kill], pre: float, post: float,
                   keep: int) -> list[Kill]:
    """The `keep` most appealing kills, still in the order they happened.

    Two orderings, on purpose: the vision model picks WHICH kills survive, and the clock
    decides what order they play in. A reel cut in descending appeal reads as a list
    rather than a match.

    `reels` is imported here rather than at the top: detection is pure pixel work and has
    to keep running without the package, which is the whole reason this file sits outside
    it. Only ranking needs a model.
    """
    from reels import ReelsError
    from reels.judge import judge_all
    from reels.search import Candidate

    spans = [Candidate(max(0.0, k.start - pre), k.start + post, 1.0) for k in kills]
    outcomes = judge_all(video, spans, "the moment an enemy is killed")

    scored: list[tuple[int, Kill]] = []
    unscored: list[Kill] = []
    failures: list[str] = []
    for kill, outcome in zip(kills, outcomes):
        if isinstance(outcome, ReelsError):
            failures.append(str(outcome))
            unscored.append(kill)
        elif outcome.appeal is None:
            unscored.append(kill)
        else:
            scored.append((outcome.appeal, kill))
            print(f"  {kill.start:7.2f}s  appeal {outcome.appeal:2d}/10"
                  f"{'  ' + outcome.hook if outcome.hook else ''}", file=sys.stderr)

    if not scored:
        # Silently falling back to the first N would answer a different question than
        # the one asked, and look identical in the output.
        sys.exit("no kill was scored, so there is no best N to keep"
                 + (f": {failures[0]}" if failures else ""))
    if unscored:
        print(f"{len(unscored)} kills went unscored and were not considered", file=sys.stderr)

    scored.sort(key=lambda pair: pair[0], reverse=True)
    best = [kill for _, kill in scored[:keep]]
    return sorted(best, key=lambda k: k.start)


def drop_near(kills: list[Kill], times: list[float], tolerance: float = 1.5) -> list[Kill]:
    """Kills whose banner does not sit within `tolerance` of any time in `times`.

    Detection is pixels, and an inventory screen or a "Deploy Wingsuit" prompt in the same
    zone reads much like a banner -- measured on one match, the duration and red profile of
    the two are indistinguishable, so no threshold separates them. Naming the handful by
    timestamp after looking at --check is honest about that, and cheaper than a detector
    that pretends to know the difference.
    """
    return [k for k in kills if all(abs(k.start - t) > tolerance for t in times)]


def windows(kills: list[Kill], pre: float, post: float, duration: float,
            merge_gap: float = MERGE_GAP) -> list[tuple[float, float]]:
    """(start, end) per clip, with runs of nearby kills merged into one shot.

    A double or triple kill lands several banners a few seconds apart. Cut one window per
    banner, the windows overlap, and the reel replays the same seconds while dropping the
    fraction of a second between them -- a streak reads as three chopped fragments rather
    than one run. Merging them keeps the streak as it actually happened, which is the part
    worth watching.

    A window that runs past either end of the recording is extended the other way instead
    of being emitted short, so a kill in the opening seconds is not a two-second stub.
    """
    spans: list[tuple[float, float]] = []
    for kill in kills:
        start, end = kill.start - pre, kill.start + post
        if start < 0.0:
            start, end = 0.0, min(duration, end - start)
        if end > duration:
            start, end = max(0.0, start - (end - duration)), duration
        if end - start <= 0:
            continue
        if spans and start - spans[-1][1] <= merge_gap:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return spans


def normalize(path: Path) -> bool:
    """Level the reel's audio in place. False, with the reason on stderr, when it could not.

    Never fatal: the reel is already built and usable, so a missing or failing tool leaves
    it as it was and says so loudly -- silently shipping un-levelled audio is the defect
    this exists to remove.
    """
    if not LOUDNESS_TOOL.is_file():
        print(f"audio NOT normalised: {LOUDNESS_TOOL} is missing", file=sys.stderr)
        return False
    tmp = path.with_name(f"{path.stem}.norm{path.suffix}")
    proc = subprocess.run(
        [sys.executable, str(LOUDNESS_TOOL), str(path), "-I", str(TARGET_LUFS),
         "--tp", str(TARGET_TRUE_PEAK), "-o", str(tmp)],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        print(f"audio NOT normalised: {tail[-1] if tail else proc.returncode}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(path)
    result = next((line for line in (proc.stdout + proc.stderr).splitlines()
                   if line.startswith("result:")), "")
    print(f"audio normalised {result.removeprefix('result:').strip()}", file=sys.stderr)
    return True


def contact_sheet(video: Path, kills: list[Kill], region, dest: Path, workdir: Path) -> Path:
    """One tile per detection, so a human can reject bad ones before any encoding."""
    w, h, _, _ = probe(video)
    rx, ry, _rw, _rh = region
    # A generous band around the zone, so the whole banner is readable in the sheet.
    bx, by = round(rx * w) - 420, round(ry * h) - 15
    tiles = workdir / "sheet"
    tiles.mkdir(parents=True, exist_ok=True)
    for i, k in enumerate(kills, 1):
        run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{(k.start + k.end) / 2:.2f}",
             "-i", str(video), "-frames:v", "1",
             "-vf", (f"crop=1000:120:{max(0, bx)}:{max(0, by)},scale=560:-1,"
                     f"drawtext=text='{i} @{k.start:.1f}s':x=5:y=5:fontsize=22:"
                     f"fontcolor=yellow:box=1:boxcolor=black@0.7"),
             str(tiles / f"t_{i:03d}.jpg"), "-y"])
    run(["ffmpeg", "-nostdin", "-v", "error", "-pattern_type", "glob", "-framerate", "1",
         "-i", str(tiles / "t_*.jpg"), "-frames:v", "1",
         "-vf", f"tile=1x{len(kills)}", "-update", "1", str(dest), "-y"])
    return dest


def build(video: Path, kills: list[Kill], out: Path, music: Path | None,
          pre: float, post: float, workdir: Path, merge_gap: float = MERGE_GAP,
          layout: str = "blur") -> Path:
    w, h, duration, fps = probe(video)
    crop_w = min(w, round(h * OUT_W / OUT_H))
    crop_w -= crop_w % 2
    crop_x = (w - crop_w) // 2

    segs = workdir / "segs"
    segs.mkdir(parents=True, exist_ok=True)
    made = []
    spans = windows(kills, pre, post, duration, merge_gap)
    if len(spans) < len(kills):
        print(f"{len(kills)} kills -> {len(spans)} shots "
              f"({len(kills) - len(spans)} merged into a streak)", file=sys.stderr)
    if layout == "blur":
        # The whole frame at output width -- scaled down, never up -- over a blurred copy of
        # itself. Cropping a 16:9 frame to 9:16 throws away two thirds of it and upsamples the
        # rest 1.78x, which is where the softness came from.
        vf = (f"split[a][b];[a]crop={crop_w}:{h}:{crop_x}:0,scale=135:240,gblur=sigma=6,"
              f"scale={OUT_W}:{OUT_H},setsar=1[bg];"
              f"[b]scale={OUT_W}:-2:flags=lanczos[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
    else:
        vf = f"crop={crop_w}:{h}:{crop_x}:0,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1"
    for i, (start, end) in enumerate(spans, 1):
        length = end - start
        seg = segs / f"s_{i:03d}.mp4"
        run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
             "-i", str(video), "-filter_complex" if layout == "blur" else "-vf", vf,
             "-c:v", "libx264", "-crf", "17", "-preset", "slow", "-pix_fmt", "yuv420p",
             "-r", str(fps), "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
             str(seg), "-y"])
        made.append(seg)
    if not made:
        sys.exit("no segments produced")

    listing = workdir / "concat.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in made))
    stitched = workdir / "stitched.mp4"
    run(["ffmpeg", "-nostdin", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(stitched), "-y"])

    out.parent.mkdir(parents=True, exist_ok=True)
    if music is None:
        # Game audio only. For TikTok this is usually right: add a trending sound in the
        # app instead, for licensing and for reach a baked-in track never gets.
        shutil.move(str(stitched), out)
        return out

    total = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                       "-of", "csv=p=0", str(stitched)]).strip())
    fade = max(0.0, total - 2.0)
    run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(stitched),
         "-stream_loop", "-1", "-i", str(music),
         "-filter_complex",
         (f"[0:a]volume={GAME_VOLUME}[g];[1:a]volume={MUSIC_VOLUME}[m];"
          f"[g][m]amix=inputs=2:duration=first:dropout_transition=0,"
          f"afade=t=out:st={fade:.2f}:d=2,alimiter=limit=0.9:level=disabled[a]"),
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-t", f"{total:.3f}", str(out), "-y"])
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video", type=Path)
    p.add_argument("-o", "--out-dir", type=Path, default=Path("out"))
    p.add_argument("--music", type=Path, default=None,
                   help="bake in a track; omit to keep game audio and add a sound in TikTok")
    p.add_argument("--pre", type=float, default=PRE_ROLL,
                   help="seconds before the banner -- the kill happens here (default 3.0)")
    p.add_argument("--post", type=float, default=POST_ROLL)
    p.add_argument("--merge-gap", type=float, default=MERGE_GAP,
                   help="kills whose windows sit within this many seconds of each other "
                        "become one continuous shot (default 2.0). 0 still merges "
                        "windows that overlap, since replaying those seconds is worse")
    p.add_argument("--skip", type=lambda v: [float(x) for x in v.split(",") if x.strip()],
                   default=None, metavar="T1,T2",
                   help="drop detections near these times (seconds), for the inventory and "
                        "prompt screens --check shows. No threshold separates those from a "
                        "real banner, so they are named rather than guessed at.")
    p.add_argument("--max-clips", type=int, default=0,
                   help="keep only the first N kills (0 = all)")
    p.add_argument("--best", type=int, default=0, metavar="N",
                   help="keep the N most appealing kills instead of the first N, scored "
                        "by the vision model. Costs one judge pass over every detection.")
    p.add_argument("--layout", choices=("blur", "crop"), default="blur",
                   help="blur: whole frame over a blurred backdrop, sharper (default); "
                        "crop: centre 9:16 slice filling the frame, bigger but upscaled 1.78x")
    p.add_argument("--fps", type=float, default=SAMPLE_FPS)
    p.add_argument("--min-edges", type=int, default=None,
                   help="edge count a banner must reach. Left out, the 1080p default of "
                        f"{MIN_EDGES} is scaled to the source height; a value given here is "
                        "used as measured. Try lower if kills are missed.")
    p.add_argument("--max-fraction", type=float, default=MAX_RED_FRACTION)
    p.add_argument("--region", type=str, default=None,
                   help="banner zone as x,y,w,h fractions of the frame (see --check)")
    p.add_argument("--normalize", action=argparse.BooleanOptionalAction, default=True,
                   help="level the audio to -14 LUFS / -1.5 dBTP (default on); "
                        "--no-normalize keeps the game audio exactly as recorded")
    p.add_argument("--check", action="store_true",
                   help="write a contact sheet of detections and stop, encoding nothing")
    p.add_argument("--json", action="store_true",
                   help="print one JSON object on stdout for a caller to parse. Finding no "
                        "kills is then a result rather than exit 1: an empty list, not a "
                        "failure the caller has to tell apart from a broken run.")
    args = p.parse_args()

    if not args.video.is_file():
        sys.exit(f"not a file: {args.video}")
    region = REGION
    if args.region:
        try:
            region = tuple(float(x) for x in args.region.split(","))
            if len(region) != 4:
                raise ValueError
        except ValueError:
            sys.exit("--region wants four comma-separated fractions: x,y,w,h")

    workdir = Path(tempfile.mkdtemp(prefix="killreel-"))
    try:
        kills = detect(args.video, region, args.fps, args.min_edges, args.max_fraction, workdir)
        print(f"{len(kills)} kill banners detected", file=sys.stderr)
        for i, k in enumerate(kills, 1):
            print(f"  {i:2d}. {k.start:7.2f}s  (clip {max(0, k.start - args.pre):.1f}"
                  f"-{k.start + args.post:.1f}s)", file=sys.stderr)
        if not kills:
            print("nothing detected -- try --check with a different --region", file=sys.stderr)
            if args.json:
                print(json.dumps({"kills": [], "output": None}))
                return 0
            return 1
        if args.skip:
            before = len(kills)
            kills = drop_near(kills, args.skip)
            print(f"dropped {before - len(kills)} of {before} detections by --skip",
                  file=sys.stderr)
        if args.best and args.max_clips:
            sys.exit("--best and --max-clips both choose which kills survive; pick one")
        if args.max_clips:
            kills = kills[: args.max_clips]
        if args.best:
            kills = rank_by_appeal(args.video, kills, args.pre, args.post, args.best)

        args.out_dir.mkdir(parents=True, exist_ok=True)
        if args.check:
            sheet = contact_sheet(args.video, kills, region,
                                  args.out_dir / f"{args.video.stem}-detections.png", workdir)
            print(f"\ncontact sheet: {sheet}\n"
                  "Check every tile shows a kill banner, then re-run without --check.",
                  file=sys.stderr)
            return 0

        # The selection goes in the name: a --best run must not overwrite the full reel,
        # and two selections must not overwrite each other.
        picked = f"-best{args.best}" if args.best else f"-first{args.max_clips}" if args.max_clips else ""
        out = build(args.video, kills,
                    args.out_dir / f"{args.video.stem}-kill-highlights{picked}.mp4",
                    args.music, args.pre, args.post, workdir, args.merge_gap, args.layout)
        if args.normalize:
            normalize(out)
        if args.json:
            print(json.dumps({"kills": [k.start for k in kills], "output": str(out)}))
            return 0
        print(out)
        if args.music is None:
            print("game audio kept -- add a trending sound in TikTok for reach and licensing",
                  file=sys.stderr)
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
