"""Following Meetily's live transcripts.json (no Meetily, mic or model needed)."""
import json

from transcript.main import RecordingsTail, find_recordings_dir


def write(root, folder, segs):
    d = root / folder
    d.mkdir(exist_ok=True)
    doc = {"version": "1.0", "total_segments": len(segs), "segments": [
        {"id": f"seg_{i}", "sequence_id": i, "text": t, "speaker": sp, "audio_start_time": i * 5.0,
         "audio_end_time": i * 5.0 + 3} for i, (t, sp) in enumerate(segs)]}
    f = d / "transcripts.json"
    f.write_text(json.dumps(doc))
    return f


def bump(f):                       # rewriting within one mtime tick would hide the change on fast disks
    import os
    st = f.stat()
    os.utime(f, (st.st_atime, st.st_mtime + 1))


def test_new_recording_is_followed_segment_by_segment(tmp_path):
    tail = RecordingsTail(str(tmp_path))
    assert tail.poll() == []
    f = write(tmp_path, "Meeting A", [("Hello.", "mic")])
    assert [(n, s["text"]) for n, s in tail.poll()] == [("Meeting A", "Hello.")]
    assert tail.poll() == []                                   # nothing new
    write(tmp_path, "Meeting A", [("Hello.", "mic"), ("Second.", "system")])
    bump(f)
    got = tail.poll()
    assert [s["text"] for _, s in got] == ["Second."] and got[0][1]["speaker"] == "system"


def test_meetings_that_existed_before_start_are_skipped(tmp_path):
    f = write(tmp_path, "Old meeting", [("old one", "mic"), ("old two", "mic")])
    tail = RecordingsTail(str(tmp_path))
    assert tail.poll() == []
    write(tmp_path, "Old meeting", [("old one", "mic"), ("old two", "mic"), ("resumed", "mic")])
    bump(f)
    assert [s["text"] for _, s in tail.poll()] == ["resumed"]  # only what is new


def test_started_mid_recording_catches_up_on_the_active_one_only(tmp_path):
    import os, time
    old = write(tmp_path, "Older", [("older text", "mic")])
    os.utime(old, (time.time() - 100, time.time() - 100))
    write(tmp_path, "Active", [("so far", "mic"), ("and more", "system")])
    tail = RecordingsTail(str(tmp_path), catch_up=True)
    assert [s["text"] for _, s in tail.poll()] == ["so far", "and more"]


def test_half_written_file_is_retried(tmp_path):
    tail = RecordingsTail(str(tmp_path))
    d = tmp_path / "M"
    d.mkdir()
    (d / "transcripts.json").write_text('{"segments": [{"sequence_id": 0, "text": "Hel')   # caught mid-write
    assert tail.poll() == []
    write(tmp_path, "M", [("Hello.", "mic")])
    bump(d / "transcripts.json")
    assert [s["text"] for _, s in tail.poll()] == ["Hello."]


def test_blank_segments_are_ignored_and_dir_is_found(tmp_path, monkeypatch):
    tail = RecordingsTail(str(tmp_path))
    write(tmp_path, "M", [("  ", "mic"), ("Real.", "mic")])
    assert [s["text"] for _, s in tail.poll()] == ["Real."]
    assert find_recordings_dir(str(tmp_path)) == str(tmp_path)
    monkeypatch.setenv("MEETILY_RECORDINGS_DIR", str(tmp_path))
    assert find_recordings_dir() == str(tmp_path)
