"""scripts/ux_screens.py: a gesture sample replayed through the real app onto a still
background gives a screen per state and hint, and the grammar the replay tests expect.
In its own process: the script replaces time.perf_counter with its virtual clock."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_a_sample_gives_its_screens_and_the_expected_grammar(tmp_path):
    env = {**os.environ, "PALMCARDS_DATA": str(tmp_path / "data")}  # never your preferences
    out = tmp_path / "ux"
    run = subprocess.run([sys.executable, "scripts/ux_screens.py", "sentence-fold-back", "--out", str(out)],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-2000:]
    data = json.loads((out / "sentence-fold-back" / "screens.json").read_text())
    wall, dark = data["runs"]["wall"], data["runs"]["dark"]
    assert wall["sample_check"] == []  # the app did what the grammar replay does
    states = [(s["state"]["app"], s["state"]["mode"], s["state"]["level"]) for s in wall["shots"]]
    assert ("prepare", "browse", "sentence") in states and ("prepare", "focus", "sentence") in states
    assert [s["why"] for s in dark["shots"]] == [s["why"] for s in wall["shots"]]  # the same screens on both
    assert all((out / "sentence-fold-back" / "wall" / s["file"]).stat().st_size > 0 for s in wall["shots"])
    assert (out / "index.html").exists() and (out / "sentence-fold-back" / "index.html").exists()
