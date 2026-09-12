"""Local HTTP API behind the review UI (V2).

Searching and thumbnails cost nothing, so the UI shows candidates immediately and the
person picks by eye. Judging is a separate opt-in call: the model's taste is a second
opinion here rather than a gate, which is also why the UI works with no API key.

Binds to loopback and serves files only from within the roots it was configured with.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ReelsError
from . import index as index_mod
from . import judge as judge_mod
from . import render as render_mod
from . import search as search_mod
from .judge import CROP, PILLARBOX
from .search import Candidate
from .variants import Treatment, default_treatments, render_variants

VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
PREVIEW_WIDTH = 480
PREVIEW_MAX_SECONDS = 60.0
PREVIEW_DIR = Path(tempfile.gettempdir()) / "reels-previews"


@dataclass
class Settings:
    library: Path
    music_dir: Path
    out_dir: Path


@dataclass
class Job:
    id: str
    kind: str
    status: Literal["running", "done", "failed"] = "running"
    result: Any = None
    error: str | None = None
    note: str = ""


@dataclass
class State:
    settings: Settings
    jobs: dict[str, Job] = field(default_factory=dict)
    pool: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=2))


# ---------------------------------------------------------------- request models

class TreatmentIn(BaseModel):
    reframe: Literal["crop", "pillarbox"] = CROP
    focal_x: float = Field(0.5, ge=0.0, le=1.0)
    track: str | None = None
    start: float | None = None
    end: float | None = None

    def to_treatment(self) -> Treatment:
        return Treatment(self.reframe, self.focal_x, self.track, self.start, self.end)


class SearchIn(BaseModel):
    video: str
    query: str = Field(min_length=1)
    min_seconds: float = search_mod.DEFAULT_MIN_SECONDS
    max_seconds: float = search_mod.DEFAULT_MAX_SECONDS
    shortlist: int = Field(search_mod.DEFAULT_SHORTLIST, ge=1, le=100)
    floor: float = search_mod.DEFAULT_FLOOR


class RangeIn(BaseModel):
    start: float
    end: float
    peak: float = 0.0

    def to_candidate(self) -> Candidate:
        return Candidate(self.start, self.end, self.peak)


class JudgeIn(BaseModel):
    video: str
    query: str
    ranges: list[RangeIn] = Field(min_length=1, max_length=20)


class RenderIn(BaseModel):
    video: str
    query: str
    range: RangeIn
    treatments: list[TreatmentIn] = Field(min_length=1)


class IndexIn(BaseModel):
    video: str


# ---------------------------------------------------------------- app

def create_app(settings: Settings) -> FastAPI:
    state = State(settings=settings)
    app = FastAPI(title="reels", docs_url=None, redoc_url=None)
    app.state.reels = state

    def resolve(raw: str, root: Path) -> Path:
        """Reject anything outside the configured root.

        The browser can ask for any path it likes, so membership is checked after
        resolving symlinks rather than by inspecting the string for '..'.
        """
        try:
            path = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
            path.relative_to(root.resolve())
        except (ValueError, OSError):
            raise HTTPException(400, f"path is outside {root}") from None
        if not path.is_file():
            raise HTTPException(404, f"no such file: {raw}")
        return path

    def submit(kind: str, note: str, work: Callable[[], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, note=note)
        state.jobs[job.id] = job

        def run() -> None:
            try:
                job.result = work()
                job.status = "done"
            except ReelsError as exc:
                job.status, job.error = "failed", str(exc)
            except Exception as exc:  # noqa: BLE001 -- a bug here must not strand the UI
                # Any escaping exception leaves the job "running" forever and the browser
                # polling a status that never changes, so everything becomes a failed job.
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"

        state.pool.submit(run)
        return job

    @app.get("/api/config")
    def config() -> dict:
        return {
            "library": str(settings.library),
            "music_dir": str(settings.music_dir),
            "out_dir": str(settings.out_dir),
            "judge_available": judge_mod.API_KEY_ENV in os.environ,
            "reframe_modes": [CROP, PILLARBOX],
            "defaults": {
                "min_seconds": search_mod.DEFAULT_MIN_SECONDS,
                "max_seconds": search_mod.DEFAULT_MAX_SECONDS,
                "shortlist": search_mod.DEFAULT_SHORTLIST,
                "floor": search_mod.DEFAULT_FLOOR,
            },
        }

    @app.get("/api/library")
    def library() -> list[dict]:
        root = settings.library
        if not root.is_dir():
            return []
        found = sorted(p for p in root.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES)
        return [
            {
                "name": p.name,
                "size": p.stat().st_size,
                "indexed": index_mod.is_fresh(p),
            }
            for p in found
        ]

    @app.get("/api/tracks")
    def tracks() -> list[str]:
        try:
            return [p.name for p in render_mod.music_tracks(settings.music_dir)]
        except ReelsError:
            return []

    @app.post("/api/index")
    def start_index(body: IndexIn) -> dict:
        video = resolve(body.video, settings.library)
        job = submit("index", f"indexing {video.name}", lambda: {
            "frames": len(index_mod.build_index(video).embeddings)
        })
        return {"job": job.id}

    @app.post("/api/search")
    def search(body: SearchIn) -> dict:
        video = resolve(body.video, settings.library)
        if not index_mod.is_fresh(video):
            raise HTTPException(409, "not indexed yet")
        loaded = index_mod.load_index(video)
        found = search_mod.candidates(
            loaded, body.query,
            min_seconds=body.min_seconds, max_seconds=body.max_seconds,
            shortlist=body.shortlist, floor=body.floor,
        )
        peak = float(search_mod.score(loaded, body.query).max()) if len(loaded.embeddings) else 0.0
        return {
            "peak": peak,
            "floor": body.floor,
            "duration": loaded.duration,
            "candidates": [
                {"start": c.start, "end": c.end, "peak": c.peak, "duration": c.duration}
                for c in found
            ],
        }

    @app.get("/api/thumb")
    def thumb(video: str, t: float = Query(ge=0.0)) -> Response:
        path = resolve(video, settings.library)
        try:
            jpeg = judge_mod.grab_frame(path, t)  # same single-frame seek the judge uses
        except ReelsError as exc:
            raise HTTPException(404, str(exc)) from None
        return Response(jpeg, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/media/preview")
    def preview(video: str, start: float = Query(ge=0.0), end: float = Query(gt=0.0)) -> FileResponse:
        """A small, muted clip of one candidate, encoded on demand and cached.

        The sources are ultrawide 60fps and not faststart, so range-serving the original
        makes the browser pull a large index and decode far more than it shows. A 480p
        proxy is a couple of seconds to make, then instant and smooth for every replay.
        """
        path = resolve(video, settings.library)
        span = min(max(end - start, 0.1), PREVIEW_MAX_SECONDS)
        key = hashlib.sha256(f"{path}|{start:.3f}|{span:.3f}".encode()).hexdigest()[:16]
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        cached = PREVIEW_DIR / f"{key}.mp4"

        if not cached.is_file():
            staging = cached.with_suffix(".partial.mp4")
            proc = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{span:.3f}",
                 "-i", str(path), "-an", "-vf", f"scale={PREVIEW_WIDTH}:-2",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                 "-movflags", "+faststart", "-pix_fmt", "yuv420p", str(staging), "-y"],
                capture_output=True, text=True, check=False,
            )
            if proc.returncode != 0 or not staging.is_file():
                staging.unlink(missing_ok=True)
                detail = proc.stderr.strip().splitlines()
                raise HTTPException(500, f"preview failed: {detail[-1] if detail else proc.returncode}")
            staging.replace(cached)

        return FileResponse(cached, media_type="video/mp4",
                            headers={"Cache-Control": "public, max-age=86400"})

    @app.post("/api/judge")
    def judge(body: JudgeIn) -> dict:
        video = resolve(body.video, settings.library)

        def work() -> list[dict]:
            out = []
            for item in body.ranges:
                candidate = item.to_candidate()
                try:
                    verdict = judge_mod.judge(video, candidate, body.query)
                    out.append({
                        "start": candidate.start, "end": candidate.end,
                        "accepted": verdict.accepted, "reason": verdict.reason,
                        "focal_x": verdict.focal_x, "reframe": verdict.reframe,
                    })
                except ReelsError as exc:
                    out.append({
                        "start": candidate.start, "end": candidate.end,
                        "accepted": None, "reason": str(exc),
                        "focal_x": None, "reframe": None,
                    })
            return out

        return {"job": submit("judge", f"judging {len(body.ranges)} ranges", work).id}

    @app.post("/api/render")
    def start_render(body: RenderIn) -> dict:
        video = resolve(body.video, settings.library)
        candidate = body.range.to_candidate()
        treatments = [t.to_treatment() for t in body.treatments]

        def work() -> list[str]:
            written = render_variants(
                video, candidate, body.query, treatments,
                music_dir=settings.music_dir, out_dir=settings.out_dir,
            )
            return [p.name for p in written]

        job = submit("render", f"rendering {len(treatments)} variants", work)
        return {"job": job.id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return {"id": job.id, "kind": job.kind, "status": job.status,
                "note": job.note, "result": job.result, "error": job.error}

    @app.get("/api/clips")
    def clips() -> list[dict]:
        if not settings.out_dir.is_dir():
            return []
        found = sorted(settings.out_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [{"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime}
                for p in found]

    @app.get("/media/clip")
    def media(name: str) -> FileResponse:
        return FileResponse(resolve(name, settings.out_dir), media_type="video/mp4")

    dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")

    return app


__all__ = ["Settings", "create_app", "default_treatments"]
