"""Command line entry point (U5).

`reels index` builds the embedding cache. `reels clip` runs the whole pipeline and
indexes on demand, so the first run on a recording is the only slow one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import ReelsError, search
from . import index as index_mod
from . import judge as judge_mod
from . import render as render_mod

DEFAULT_MUSIC_DIR = Path.home() / "Videos"
DEFAULT_OUT_DIR = Path("out")
DEFAULT_LIBRARY = Path.home() / "Videos"
DEFAULT_UI_PORT = 8765


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def cmd_index(args: argparse.Namespace) -> int:
    video = Path(args.video)
    if index_mod.is_fresh(video) and not args.force:
        existing = index_mod.load_index(video)
        _say(f"already indexed: {len(existing.embeddings)} keyframes")
        return 0
    _say(f"indexing {video.name} (first run on a long recording takes a few minutes)")
    built = index_mod.build_index(video, force=args.force)
    _say(f"indexed {len(built.embeddings)} keyframes -> {index_mod.index_path(video).name}")
    return 0


def cmd_clip(args: argparse.Namespace) -> int:
    video = Path(args.video)
    if not index_mod.is_fresh(video):
        _say(f"indexing {video.name} (one-time, a few minutes on a long recording)")
    built = index_mod.build_index(video)

    # Fail fast on a bad music directory: validating it inside render would spend every
    # vision-model call first, then stop on something knowable before any of them.
    tracks = render_mod.music_tracks(Path(args.music_dir))

    found = search.candidates(
        built,
        args.query,
        min_seconds=args.min_seconds,
        max_seconds=args.max_seconds,
        shortlist=args.shortlist,
        floor=args.floor,
    )
    if not found:
        peak = float(search.score(built, args.query).max())
        _say(f'nothing in {video.name} matches "{args.query}" -- no candidates to judge.')
        _say(f"  best match scored {peak:.4f}, below the {args.floor} floor.")
        if peak >= args.floor * 0.85:
            _say("  that is close -- try a shorter query, or lower --floor.")
        return 0

    _say(f"{len(found)} candidate ranges ({len(tracks)} music tracks); "
         f"asking the vision model about each")
    accepted: list[tuple[search.Candidate, judge_mod.Verdict]] = []
    rejected: list[str] = []
    errored: list[str] = []
    for candidate in found:
        span = f"{candidate.start:.0f}-{candidate.end:.0f}s"
        try:
            verdict = judge_mod.judge(video, candidate, args.query)
        except ReelsError as exc:
            # One failed range does not throw away the ranges that already succeeded.
            errored.append(f"  {span}: {exc}")
            continue
        if verdict.accepted:
            accepted.append((candidate, verdict))
            _say(f"  {span}: accepted ({verdict.reframe})")
        else:
            rejected.append(f"  {span}: {verdict.reason}")

    if not accepted:
        _say(f'no usable clip for "{args.query}" in {video.name}.')
        if rejected:
            _say(f"rejected {len(rejected)}:")
            for line in rejected:
                _say(line)
        if errored:
            _say(f"errored {len(errored)}:")
            for line in errored:
                _say(line)
        # Every range failing to be judged (an unset API key, no network) is a failed run,
        # not a recording that happens to contain nothing.
        return 1 if errored and not rejected else 0

    # Say why the others went, even on a successful run: the judge's taste is the part
    # worth disagreeing with, and silent rejections give nothing to disagree with.
    for line in rejected:
        _say(f"  rejected{line}")

    for candidate, verdict in accepted:
        written = render_mod.render(
            video, candidate, verdict, args.query,
            music_dir=Path(args.music_dir), out_dir=Path(args.out_dir),
        )
        print(written)

    if errored:
        _say(f"{len(errored)} range(s) could not be judged:")
        for line in errored:
            _say(line)
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    """Serve the review UI on loopback.

    Searching and thumbnails need no API key, so the UI is usable without one -- judging
    is the only call that does, and it is opt-in per candidate.
    """
    try:
        import uvicorn

        from .api import Settings, create_app
    except ImportError as exc:
        raise ReelsError(f"the UI extras are not installed: {exc}") from exc

    app = create_app(Settings(
        library=Path(args.library), music_dir=Path(args.music_dir), out_dir=Path(args.out_dir),
    ))
    url = f"http://127.0.0.1:{args.port}"
    built = Path(__file__).resolve().parent.parent / "web" / "dist"
    if not built.is_dir():
        _say("web/dist not built -- serving the API only. Run `pnpm install && pnpm build` in web/.")
    _say(f"reels ui on {url}  (library: {args.library})")
    if not args.no_browser:
        import webbrowser

        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reels", description=__doc__.splitlines()[0])
    subs = parser.add_subparsers(dest="command", required=True)

    index_cmd = subs.add_parser("index", help="build the embedding cache for a recording")
    index_cmd.add_argument("video")
    index_cmd.add_argument("--force", action="store_true", help="re-index even if current")
    index_cmd.set_defaults(func=cmd_index)

    clip_cmd = subs.add_parser("clip", help="cut Reels matching a text query")
    clip_cmd.add_argument("video")
    clip_cmd.add_argument("query")
    clip_cmd.add_argument("--min-seconds", type=float, default=search.DEFAULT_MIN_SECONDS)
    clip_cmd.add_argument("--max-seconds", type=float, default=search.DEFAULT_MAX_SECONDS)
    clip_cmd.add_argument("--shortlist", type=int, default=search.DEFAULT_SHORTLIST)
    clip_cmd.add_argument("--floor", type=float, default=search.DEFAULT_FLOOR,
                          help="minimum peak similarity before any range is emitted")
    clip_cmd.add_argument("--music-dir", default=str(DEFAULT_MUSIC_DIR))
    clip_cmd.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    clip_cmd.set_defaults(func=cmd_clip)

    ui_cmd = subs.add_parser("ui", help="review candidates and pick variants in a browser")
    ui_cmd.add_argument("--library", default=str(DEFAULT_LIBRARY), help="folder of recordings")
    ui_cmd.add_argument("--music-dir", default=str(DEFAULT_MUSIC_DIR))
    ui_cmd.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ui_cmd.add_argument("--port", type=int, default=DEFAULT_UI_PORT)
    ui_cmd.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    ui_cmd.set_defaults(func=cmd_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ReelsError as exc:
        _say(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
