"""Packaging stays local unless text generation is explicitly requested."""
import io
import subprocess
import sys

import pytest
from PIL import Image

from reels import ReelsError, packaging


def test_bundle_is_readable_and_never_overwrites(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"clip")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")
    package = packaging.Package("A title", "A description", cover=cover)

    first = packaging.write_bundle(clip, package, tmp_path)
    second = packaging.write_bundle(clip, package, tmp_path)

    assert (first / "README.md").read_text(encoding="utf-8").startswith("# A title")
    assert (first / "cover.jpg").read_bytes() == b"cover"
    assert second != first and first.is_dir() and second.is_dir()


def test_model_titles_filter_invalid_rows():
    payload = {"titles": [
        {"title": "Valid title", "cover_text": "Big view", "reason": "frame", "frame_index": 0},
        {"title": "x" * 66, "cover_text": "short", "frame_index": 0},
        {"title": "Ungrounded", "cover_text": "short", "frame_index": 9},
        {"title": "A title with forest after " + "x" * 50, "subject": "forest", "cover_text": "Trees", "frame_index": 0},
    ]}
    assert packaging._titles(payload, 1) == [packaging.Title("Valid title", "Big view", "frame", 0)]


def test_description_rejects_chapters_and_truncates_at_sentence():
    with pytest.raises(ReelsError, match="timestamps"):
        packaging.generate_description([], "forest", "Forest", ask=lambda *_: {"description": "At 00:10 the trees appear."})
    text = "First grounded sentence. " + "word " * 120 + "Final sentence."
    result = packaging.generate_description([], "forest", "Forest", ask=lambda *_: {"description": text})
    assert len(result) <= 500
    assert result.endswith(".")


def test_covers_preserve_frame_content_aspect_ratio(tmp_path):
    frame = io.BytesIO()
    Image.new("RGB", (768, 309), "red").save(frame, "JPEG")
    sample = packaging.Sample(frame.getvalue(), 1.0, tmp_path / "source.mp4", 0)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"clip")
    # make_covers reads dimensions through ffprobe, so this isolates the image transform.
    original = packaging._size
    packaging._size = lambda _path: (1080, 1920)
    try:
        cover = packaging.make_covers(clip, [sample], "Forest", tmp_path / "covers", 1)[0]
    finally:
        packaging._size = original
    with Image.open(cover) as image:
        assert image.size == (1080, 1920)
        assert image.getpixel((540, 960))[0] > 100


def test_packaging_import_does_not_load_clip_index():
    result = subprocess.run(
        [sys.executable, "-c", "import reels.packaging, sys; print('reels.index' in sys.modules)"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "False"


def test_cover_uses_vendored_font():
    assert packaging.FONT_PATH.is_file()


def test_title_critic_receives_brainstorm_ideas(tmp_path):
    calls = []

    def ask(_frames, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return {"titles": [{"title": "Forest view"}]}
        return {"titles": [{"title": "Forest view", "cover_text": "Wide vista", "reason": "frame 0", "frame_index": 0}]}

    samples = [packaging.Sample(b"jpeg", 1.0, tmp_path / "clip.mp4", 0)]
    assert packaging.generate_titles(samples, "forest", ask=ask)[0].title == "Forest view"
    assert "Forest view" in calls[1]
