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
MAX_RED_FRACTION = 0.60  # above this the zone is a flash, not text
EVENT_GAP = 2.5  # seconds of quiet that ends one banner
MIN_EVENT = 0.25

PRE_ROLL = 3.0  # the banner appears AFTER the kill; the shot that earned it is before
POST_ROLL = 1.5
OUT_W, OUT_H, OUT_FPS = 1080, 1920, 30
GAME_VOLUME, MUSIC_VOLUME = 0.55, 0.85


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()
        sys.exit(f"{cmd[0]} failed: {tail[-1] if tail else proc.returncode}")
    return proc.stdout


def probe(video: Path) -> tuple[int, int, float]:
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height", "-show_entries", "format=duration",
               "-of", "json", str(video)])
    d = json.loads(out)
    st = d["streams"][0]
    return int(st["width"]), int(st["height"]), float(d["format"]["duration"])


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


def detect(video: Path, region: tuple[float, float, float, float], fps: float,
           min_edges: int, max_fraction: float, workdir: Path) -> list[Kill]:
    w, h, _ = probe(video)
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


def contact_sheet(video: Path, kills: list[Kill], region, dest: Path, workdir: Path) -> Path:
    """One tile per detection, so a human can reject bad ones before any encoding."""
    w, h, _ = probe(video)
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
          pre: float, post: float, workdir: Path) -> Path:
    w, h, duration = probe(video)
    crop_w = min(w, round(h * OUT_W / OUT_H))
    crop_w -= crop_w % 2
    crop_x = (w - crop_w) // 2

    segs = workdir / "segs"
    segs.mkdir(parents=True, exist_ok=True)
    made = []
    for i, k in enumerate(kills, 1):
        start = max(0.0, k.start - pre)
        length = min(duration, k.start + post) - start
        if length <= 0:
            continue
        seg = segs / f"s_{i:03d}.mp4"
        run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
             "-i", str(video),
             "-vf", f"crop={crop_w}:{h}:{crop_x}:0,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1",
             "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-r", str(OUT_FPS), "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
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
          f"afade=t=out:st={fade:.2f}:d=2,alimiter=limit=0.92[a]"),
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
    p.add_argument("--max-clips", type=int, default=0,
                   help="keep only the first N kills (0 = all)")
    p.add_argument("--best", type=int, default=0, metavar="N",
                   help="keep the N most appealing kills instead of the first N, scored "
                        "by the vision model. Costs one judge pass over every detection.")
    p.add_argument("--fps", type=float, default=SAMPLE_FPS)
    p.add_argument("--min-edges", type=int, default=MIN_EDGES)
    p.add_argument("--max-fraction", type=float, default=MAX_RED_FRACTION)
    p.add_argument("--region", type=str, default=None,
                   help="banner zone as x,y,w,h fractions of the frame (see --check)")
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
                    args.music, args.pre, args.post, workdir)
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
