"""Test-session defaults shared by every test module."""
import os
import shutil
from pathlib import Path

import pytest

# A local tool should not send DeepEval usage telemetry from test runs.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

FIXTURE_GOLDENS = Path(__file__).parent / "data" / "goldens.json"


@pytest.fixture(autouse=True)
def fixed_goldens(request, tmp_path, monkeypatch):
    """Point the golden store at a fixed copy, so editing the real
    backend/datasets/goldens.json from the side panel never breaks the suite.
    Smoke tests keep checking the real file."""
    if request.node.get_closest_marker("smoke"):
        return
    from backend.datasets import goldens

    copy = tmp_path / "fixture-goldens.json"
    shutil.copyfile(FIXTURE_GOLDENS, copy)
    monkeypatch.setattr(goldens, "GOLDENS_PATH", str(copy))
