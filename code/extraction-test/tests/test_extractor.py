"""Step (a) check: extractor output vs the synthetic video's ground truth."""
import json
import re
from pathlib import Path

import pytest

from tests import synthetic
from vcs.extractor import Params, extract

TOL = 1.5  # seconds; boundaries are only known to the sampling interval


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    d = tmp_path_factory.mktemp("synth")
    video = synthetic.build(d / "synthetic.mp4")
    doc = extract(video, d / "out", Params(), log=lambda *_: None)
    return doc["screenshots"], d / "out"


def squash(t):
    return re.sub(r"\W+", "", t.lower())


def within(s, lo, hi):
    return s["start"] >= lo - TOL and s["end"] <= hi + TOL


def test_screens_and_times(shots):
    screens, out = shots
    exp = {e["name"]: e for e in synthetic.EXPECTED}

    for name in ("A", "B", "diagram", "A again"):
        e = exp[name]
        hit = [s for s in screens if within(s, e["start"], e["end"])
               and abs(s["start"] - e["start"]) <= TOL and abs(s["end"] - e["end"]) <= TOL]
        assert len(hit) == 1, f"{name}: expected one screen {e['start']}-{e['end']}, got {screens}"
        s = hit[0]
        assert s["type"] == e["type"], name
        for phrase in e["contains"]:
            assert squash(phrase) in squash(s["text"]), f"{name} missing {phrase!r}"
        assert (out / s["image"]).is_file()


def test_short_screen_dropped(shots):
    screens, _ = shots
    assert not any(24 - TOL < s["start"] < 26 + TOL - 0.5 and s["end"] < 28 for s in screens)
    assert all(s["end"] - s["start"] >= 3.0 for s in screens)


def test_bullet_build_merged_to_longest(shots):
    screens, _ = shots
    b = next(s for s in screens if abs(s["start"] - 12) <= TOL)
    assert b["merged_from"] >= 2
    assert squash("Hiring four new engineers") in squash(b["text"])


def test_drift_splits_scrolling_slide(shots):
    screens, _ = shots
    d = [s for s in screens if within(s, 40, 60)]
    assert len(d) >= 2
    assert sum(s["end"] - s["start"] for s in d) >= 16  # most of the 20 s is covered


def test_window_chrome_stripped(shots):
    screens, _ = shots
    assert not any("weekly" in s["text"].lower() for s in screens)
    assert len(screens) == 6
