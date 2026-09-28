"""Post-ready titles, covers, descriptions, and export bundles for rendered clips."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat

from . import ReelsError, run_tool
from .frames import grab_frame

API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_MODEL = "gemini-flash-latest"
MAX_TITLE_CHARS = 65
MOBILE_TITLE_CHARS = 50
FONT_PATH = Path(__file__).parent / "assets" / "NotoSans-Bold.ttf"


@dataclass(frozen=True)
class Sample:
    image: bytes
    timestamp: float
    source: Path
    index: int


@dataclass(frozen=True)
class Title:
    title: str
    cover_text: str
    reason: str
    frame_index: int


@dataclass(frozen=True)
class Package:
    title: str
    description: str = ""
    titles: list[Title] = field(default_factory=list)
    cover: Path | None = None
    covers: list[Path] = field(default_factory=list)


Asker = Callable[[Sequence[bytes], str], dict]


def _size(video: Path) -> tuple[int, int]:
    out = run_tool(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "json", str(video)],
        what=f"reading the dimensions of {video.name}",
    )
    try:
        stream = json.loads(out)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReelsError(f"cannot read dimensions of {video.name}") from exc


def _duration(video: Path) -> float:
    out = run_tool(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(video)], what=f"reading duration of {video.name}")
    try:
        duration = float(out.strip())
    except ValueError as exc:
        raise ReelsError(f"cannot read duration of {video.name}") from exc
    if duration <= 0:
        raise ReelsError(f"cannot package a zero-length clip: {video.name}")
    return duration


def _source_metadata(clip: Path) -> tuple[Path, float, float] | None:
    path = clip.with_suffix(".source")
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        source = Path(data["source"])
        start, end = float(data["start"]), float(data["end"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return (source, start, end) if source.is_file() and end > start else None


def sample_frames(clip: Path, count: int = 10) -> list[Sample]:
    """Sample source footage when render metadata still points to it."""
    if count < 1:
        raise ReelsError("frame sample count must be positive")
    clip = Path(clip)
    metadata = _source_metadata(clip)
    source, start, end = metadata if metadata else (clip, 0.0, _duration(clip))
    times = np.linspace(start, end, count + 2)[1:-1]
    return [Sample(grab_frame(source, float(at)), float(at), source, i) for i, at in enumerate(times)]


def _ask_gemini(frames: Sequence[bytes], prompt: str) -> dict:
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise ReelsError(f"{API_KEY_ENV} is not set. Covers and export still work without it.")
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        parts = [types.Part.from_bytes(data=frame, mime_type="image/jpeg") for frame in frames]
        response = client.models.generate_content(model=DEFAULT_MODEL, contents=[*parts, prompt])
    except ReelsError:
        raise
    except Exception as exc:  # provider errors must reach the job status
        raise ReelsError(f"the packaging model request failed: {exc}") from exc
    try:
        return json.loads(response.text)
    except (AttributeError, TypeError, json.JSONDecodeError) as exc:
        raise ReelsError(f"the packaging model returned malformed JSON: {exc}") from exc


def _titles(payload: object, frame_count: int) -> list[Title]:
    if not isinstance(payload, dict) or not isinstance(payload.get("titles"), list):
        raise ReelsError("the packaging model returned malformed titles")
    result = []
    for row in payload["titles"]:
        if not isinstance(row, dict):
            continue
        title, cover_text = row.get("title"), row.get("cover_text")
        frame, subject = row.get("frame_index"), row.get("subject")
        if not isinstance(title, str) or not isinstance(cover_text, str):
            continue
        title, cover_text = title.strip(), cover_text.strip()
        if isinstance(subject, str) and subject.strip() and subject.casefold() not in title[:MOBILE_TITLE_CHARS].casefold():
            continue
        if isinstance(frame, bool) or not isinstance(frame, int) or not 0 <= frame < frame_count:
            continue
        if not title or len(title) > MAX_TITLE_CHARS or len(cover_text.split()) not in range(1, 5):
            continue
        if cover_text.casefold() in title.casefold():
            continue
        reason = row.get("reason", "")
        result.append(Title(title, cover_text, reason.strip() if isinstance(reason, str) else "", frame))
    return result[:10]


def generate_titles(samples: Sequence[Sample], query: str, *, steer: str = "", ask: Asker | None = None) -> list[Title]:
    frames = [sample.image for sample in samples]
    brainstorm = (ask or _ask_gemini)(frames, f"Return JSON {{'titles': [...]}} with 25 distinct short title ideas grounded only in these frames. Query: {query}. Steer: {steer}")
    if not isinstance(brainstorm, dict) or not isinstance(brainstorm.get("titles"), list):
        raise ReelsError("the packaging model returned malformed brainstorm titles")
    critic = (ask or _ask_gemini)(
        frames,
        "Return JSON {'titles': [...]} ranking these grounded title ideas. Each row has "
        "title, subject, cover_text, reason, frame_index. The subject must occur in the "
        "first 50 characters of title. Keep titles under 65 characters, cover text 1 to "
        "4 words, and use a frame index. Ideas: " + json.dumps(brainstorm["titles"]),
    )
    return _titles(critic, len(samples))


def generate_description(samples: Sequence[Sample], query: str, title: str, *, ask: Asker | None = None) -> str:
    payload = (ask or _ask_gemini)([sample.image for sample in samples], f"Return JSON {{'description': '...'}}. Write two short grounded lines for '{title}' about {query}. No chapters or timestamps.")
    if not isinstance(payload, dict) or not isinstance(payload.get("description"), str):
        raise ReelsError("the packaging model returned a malformed description")
    text = payload["description"].strip()
    if not text:
        raise ReelsError("the packaging model returned an empty description")
    if re.search(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)", text):
        raise ReelsError("the packaging model returned fabricated chapter timestamps")
    if len(text) <= 500:
        return text
    boundary = max(
        (match.end() for match in re.finditer(r"[.!?](?:\s|$)", text[:500])),
        default=0,
    )
    if boundary:
        return text[:boundary].strip()
    return text[:500].rsplit(" ", 1)[0].rstrip()


def _sharpness(sample: Sample) -> float:
    image = Image.open(io.BytesIO(sample.image)).convert("L")
    return float(ImageStat.Stat(image).var[0])


def _distance(left: Sample, right: Sample) -> float:
    """Approximate visual distance using tiny grayscale thumbnails."""
    images = [Image.open(io.BytesIO(sample.image)).convert("L").resize((32, 32)) for sample in (left, right)]
    return float(np.mean(np.abs(np.asarray(images[0], dtype=np.float32) - np.asarray(images[1], dtype=np.float32))))


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError as exc:
        raise ReelsError(f"packaging font is unavailable: {FONT_PATH}") from exc


def make_covers(clip: Path, samples: Sequence[Sample], text: str, out_dir: Path, count: int = 3) -> list[Path]:
    if count < 1:
        raise ReelsError("cover count must be positive")
    if not samples:
        raise ReelsError("cannot build covers without sampled frames")
    width, height = _size(Path(clip))
    if width < 1 or height < 1:
        raise ReelsError(f"cannot build covers with invalid dimensions: {width}x{height}")
    sharp = sorted(samples, key=_sharpness, reverse=True)
    ranked = []
    while sharp and len(ranked) < count:
        if not ranked:
            ranked.append(sharp.pop(0))
            continue
        # Prefer a sharp frame for the first cover, then maximise visual distance so
        # alternatives are not near-duplicate stills from adjacent timestamps.
        ranked.append(max(sharp, key=lambda sample: min(_distance(sample, picked) for picked in ranked)))
        sharp.remove(ranked[-1])
    out_dir.mkdir(parents=True, exist_ok=True)
    covers = []
    for i, sample in enumerate(ranked, 1):
        image = ImageOps.fit(Image.open(io.BytesIO(sample.image)).convert("RGB"), (width, height))
        draw = ImageDraw.Draw(image)
        font = _font(max(32, width // 12))
        max_width = width - width // 8
        words, lines, line = text.split(), [], ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if line and draw.textlength(candidate, font=font) > max_width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
        headline = "\n".join(lines)
        box = draw.multiline_textbbox((0, 0), headline, font=font, stroke_width=3)
        x, y = width // 16, height - (box[3] - box[1]) - height // 10
        draw.rounded_rectangle((x - 20, y - 20, min(width - 20, x + box[2] + 20), y + box[3] + 20), radius=12, fill="black")
        draw.multiline_text((x, y), headline, font=font, fill="white", stroke_width=3, stroke_fill="black")
        path = out_dir / f"cover-{i}.jpg"
        for quality in range(90, 0, -5):
            image.save(path, "JPEG", quality=quality, optimize=True)
            if path.stat().st_size < 2 * 1024 * 1024:
                break
        if path.stat().st_size >= 2 * 1024 * 1024:
            raise ReelsError("cannot compress cover below 2 MB")
        covers.append(path)
    return covers


def write_bundle(clip: Path, package: Package, out_dir: Path) -> Path:
    clip, out_dir = Path(clip), Path(out_dir)
    base = out_dir / f"{clip.stem}.package"
    destination = base
    suffix = 2
    while destination.exists():
        destination = out_dir / f"{base.name}-{suffix}"
        suffix += 1
    staging = out_dir / f"{destination.name}.writing"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        (staging / "alternatives").mkdir(parents=True)
        readme = f"# {package.title}\n\n{package.description}\n\nGenerated text is model-written.\n"
        (staging / "README.md").write_text(readme, encoding="utf-8")
        if package.cover:
            shutil.copy2(package.cover, staging / "cover.jpg")
        if package.titles:
            rows = [f"- {item.title}\n  - {item.reason}" for item in package.titles]
            (staging / "alternatives" / "titles.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
        for cover in package.covers:
            if package.cover and Path(cover) == Path(package.cover):
                continue
            shutil.copy2(cover, staging / "alternatives" / Path(cover).name)
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination
