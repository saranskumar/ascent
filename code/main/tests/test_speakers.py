"""Speaker labels in the summary input (Meetily exposes cluster ids, not names)."""
from core import speaker_names
from core.transcript import build_input, speakers, speech_lines

SEGS = [
    {"text": "Welcome everyone to the review", "audio_start_time": 1.0, "speaker": "mic",
     "detected_meeting_speaker_id": 8},
    {"text": "Thanks, glad to be here today", "audio_start_time": 4.0, "speaker": "mic",
     "detected_meeting_speaker_id": 9},
    {"text": "Let's start", "audio_start_time": 6.0, "speaker": "mic",
     "detected_meeting_speaker_id": 8, "assigned_meeting_speaker_id": 12},
]


def test_unnamed_speakers_get_numbers():
    lines = [l for _, l in speech_lines(SEGS)]
    assert lines[0] == "[00:01] Speaker 8: Welcome everyone to the review"
    assert lines[1].startswith("[00:04] Speaker 9: ")
    assert lines[2].startswith("[00:06] Speaker 12: ")      # assigned id wins


def test_names_replace_numbers():
    text = build_input(SEGS, [], names={"8": "Hari", "9": "Saran"})
    assert "[00:01] Hari: Welcome" in text and "[00:04] Saran: Thanks" in text
    assert "Speaker 12: Let's start" in text


def test_single_speaker_has_no_label():
    one = [dict(s, detected_meeting_speaker_id=1) for s in SEGS[:2]]
    assert speech_lines(one)[0][1] == "[00:01] Welcome everyone to the review"


def test_real_name_in_speaker_field_is_used():
    seg = [{"text": "hello there friends", "audio_start_time": 0, "speaker": "Priya"}]
    assert speech_lines(seg)[0][1] == "[00:00] Priya: hello there friends"


def test_speakers_summary():
    sp = speakers(SEGS)
    assert [s["id"] for s in sp] == ["8", "9", "12"]
    assert sp[0]["segments"] == 1 and sp[0]["samples"][0]["text"].startswith("Welcome")
    assert sp[2]["samples"] == []          # "Let's start" is too short to be a useful sample


def test_names_round_trip(tmp_path):
    assert speaker_names.load(tmp_path, "meeting-1") == {}
    speaker_names.save(tmp_path, "meeting-1", {"8": " Hari ", "9": ""})
    assert speaker_names.load(tmp_path, "meeting-1") == {"8": "Hari"}
    assert speaker_names.load(tmp_path, "fixture") == {}


def test_bad_meeting_id_rejected(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        speaker_names.save(tmp_path, "../evil", {"1": "x"})
