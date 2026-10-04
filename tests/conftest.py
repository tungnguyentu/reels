import subprocess
from pathlib import Path

import numpy as np
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "forest-90s.mp4"


@pytest.fixture(autouse=True)
def pinned_judge_backend(monkeypatch):
    """Pin the judge backend for every test.

    judge.backend() auto-detects, so without this the suite would take the agy path on a
    machine where agy happens to be installed and the gemini path elsewhere -- the tests
    would depend on the host. Tests that exercise agy set the variable themselves.
    """
    monkeypatch.setenv("REELS_JUDGE_BACKEND", "gemini")


@pytest.fixture
def fixture_video(tmp_path):
    """A copy of the fixture, so tests may write an index beside it without dirtying the tree."""
    dest = tmp_path / FIXTURE.name
    dest.write_bytes(FIXTURE.read_bytes())
    return dest


@pytest.fixture
def keyframe_count():
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-skip_frame", "nokey",
         "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(FIXTURE)],
        capture_output=True, text=True, check=True,
    ).stdout
    return len([ln for ln in out.splitlines() if ln.strip().rstrip(",")])


@pytest.fixture
def fake_encoder():
    """Stand-in for CLIP: deterministic unit vectors, so structural tests skip the model load."""
    def encode(paths):
        rng = np.random.default_rng(0)
        vecs = rng.standard_normal((len(paths), 8)).astype(np.float32)
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
    return encode


@pytest.fixture
def true_peak():
    """Measured true peak of a file's audio in dBTP, as a platform check would read it."""
    import subprocess

    def measure(path):
        err = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true",
             "-f", "null", "-"], capture_output=True, text=True, check=True,
        ).stderr
        return float(err[err.rindex("True peak:"):].split("Peak:")[1].split("dBFS")[0])

    return measure
