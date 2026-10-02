"""Step (d) pieces that don't need a live window (the live path: tests/live_capture_check.py)."""
import json
import sys

import numpy as np
import pytest

from vcs.capture import _fit
from vcs.server import App, parse_ts


def test_fit_letterboxes_resized_window():
    tall = np.full((400, 200, 3), 255, np.uint8)
    out = _fit(tall, (320, 200))
    assert out.shape == (200, 320, 3)
    assert out[:, :50].max() == 0 and out[:, 270:].max() == 0      # black side bars
    assert out[100, 160].min() == 255                               # content centred
    same = np.zeros((200, 320, 3), np.uint8)
    assert _fit(same, (320, 200)) is same


def test_parse_ts_handles_meetily_nanoseconds():
    a = parse_ts("2026-10-02T11:06:39.627532700+00:00")
    b = parse_ts("2026-10-02T11:06:41.127532Z")
    assert (b - a).total_seconds() == pytest.approx(1.5)
    assert parse_ts("2026-10-02T16:36:39+05:30") == parse_ts("2026-10-02T11:06:39+00:00")
    assert parse_ts("") is None and parse_ts("nonsense") is None


def test_offset_prefers_recording_started(tmp_path):
    app = App(tmp_path)
    d = tmp_path / "capture-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({
        "capture_started_at": "2026-10-02T11:07:00+00:00",
        "recording_meeting_id": "m1",
        "recording_started_at": "2026-10-02T11:06:39.500000+00:00"}))
    r = app.suggest_offset("capture-1", "m1")
    assert r == {"offset": 20.5, "basis": "recording.started"}
    assert app.suggest_offset("capture-1", "fixture") == {"offset": None}


def test_offset_estimated_from_meeting_end(tmp_path, monkeypatch):
    """created_at is when Meetily saved the meeting, i.e. the recording's end."""
    import vcs.server as S

    class FakeClient:
        def get_meeting(self, mid):
            return {"id": mid, "created_at": "2026-10-02T11:08:00.000000000+00:00"}

        def get_transcript(self, mid):
            return {"segments": [{"audio_end_time": 40.0}, {"audio_end_time": 90.0}]}

    monkeypatch.setattr(S, "MeetilyClient", FakeClient)
    app = App(tmp_path)
    d = tmp_path / "capture-2"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"capture_started_at": "2026-10-02T11:06:35+00:00"}))
    r = app.suggest_offset("capture-2", "m2")
    assert r["offset"] == 5.0 and r["basis"].startswith("estimated")


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Win32 only")
def test_window_list_shape():
    from vcs.windows import list_windows
    for w in list_windows():
        assert {"hwnd", "title", "process", "width", "height", "minimized"} <= set(w)
        assert w["title"].strip()
