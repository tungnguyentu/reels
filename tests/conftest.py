import subprocess
from pathlib import Path

import numpy as np
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "forest-90s.mp4"


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
