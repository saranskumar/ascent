"""Build Meetily-style summary input: `[MM:SS] text` per segment, with `[SCREEN]` lines
interleaved at each screenshot's start time."""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_SCREEN_TEXT = 1500  # chars of OCR text per screenshot


def mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def segment_start(seg: dict) -> float | None:
    v = seg.get("audio_start_time")
    return float(v) if isinstance(v, (int, float)) else None


# Meetily's `speaker` field holds the audio source, not a person.
GENERIC_SPEAKERS = {"", "mic", "system", "speaker", "unknown", "me", "you", "microphone"}


def speaker_key(seg: dict) -> str | None:
    """Diarization cluster of a segment (the assigned one wins over the detected one)."""
    for k in ("assigned_meeting_speaker_id", "detected_meeting_speaker_id"):
        if seg.get(k) is not None:
            return str(seg[k])
    return None


def speaker_label(seg: dict, names: dict[str, str] | None, multi: bool) -> str | None:
    key = speaker_key(seg)
    if names and key is not None and (names.get(key) or "").strip():
        return names[key].strip()
    spk = (seg.get("speaker") or "").strip()
    if spk.lower() not in GENERIC_SPEAKERS:
        return spk                      # a real name already in the transcript
    if multi and key is not None:
        return f"Speaker {key}"
    return None


def speakers(segments: list[dict]) -> list[dict]:
    """Detected speakers: id, segment count, first spoken time and a few sample lines."""
    out: dict[str, dict] = {}
    for seg in segments:
        key, text = speaker_key(seg), (seg.get("text") or "").strip()
        if key is None or not text:
            continue
        sp = out.setdefault(key, {"id": key, "segments": 0, "first": segment_start(seg) or 0.0,
                                  "samples": []})
        sp["segments"] += 1
        if len(sp["samples"]) < 3 and len(text.split()) >= 4:
            sp["samples"].append({"time": segment_start(seg) or 0.0, "text": text[:160]})
    return sorted(out.values(), key=lambda s: s["first"])


def speech_lines(segments: list[dict], names: dict[str, str] | None = None
                 ) -> list[tuple[float, str]]:
    """(time, line) per non-empty segment, like Meetily's useSummaryGeneration, prefixed
    with the speaker's name when known (Meetily Pro's summaries use the names you assign).
    With several detected speakers and no name, `Speaker <id>` keeps voices apart.
    A segment without audio_start_time inherits the previous segment's time."""
    multi = len({speaker_key(s) for s in segments if speaker_key(s) is not None}) > 1
    out: list[tuple[float, str]] = []
    last = 0.0
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        t = segment_start(seg)
        if t is None:
            t = last
        last = t
        who = speaker_label(seg, names, multi)
        out.append((t, f"[{mmss(t)}] {who + ': ' if who else ''}{text}"))
    return out


@dataclass
class DiagramRef:
    number: int          # 1-based, as referenced in the [SCREEN] line
    shot: dict
    time: float          # recording-relative start


def screen_lines(shots: list[dict], offset: float = 0.0
                 ) -> tuple[list[tuple[float, str]], list[DiagramRef]]:
    """One `[SCREEN]` line per screenshot at its start time (+offset = how many seconds
    after the audio recording started the screen capture started). Diagram screenshots
    point at an image that is attached to the Gemini call."""
    lines, diagrams = [], []
    for s in sorted(shots, key=lambda s: s["start"]):
        t = s["start"] + offset
        span = f"on screen {mmss(t)}-{mmss(s['end'] + offset)}"
        if s.get("type") == "diagram":
            d = DiagramRef(len(diagrams) + 1, s, t)
            diagrams.append(d)
            body = f"Diagram or image-heavy screen, see image {d.number}."
            text = re.sub(r"\s*\n\s*", " / ", (s.get("text") or "").strip())
            if text:
                body += f' OCR: "{text[:MAX_SCREEN_TEXT]}"'
        else:
            text = re.sub(r"\s*\n\s*", " / ", (s.get("text") or "").strip())
            if not text:
                continue
            body = f'Slide. OCR: "{text[:MAX_SCREEN_TEXT]}"'
        lines.append((t, f"[{mmss(t)}] [SCREEN] ({span}) {body}"))
    return lines, diagrams


def build_input(segments: list[dict], shots: list[dict], offset: float = 0.0,
                names: dict[str, str] | None = None) -> tuple[str, list[DiagramRef]]:
    speech = speech_lines(segments, names)
    screen, diagrams = screen_lines(shots, offset)
    # Stable merge by time; on a tie the speech line goes first.
    merged = sorted([(t, 0, i, ln) for i, (t, ln) in enumerate(speech)]
                    + [(t, 1, i, ln) for i, (t, ln) in enumerate(screen)])
    return "\n".join(ln for *_, ln in merged), diagrams
