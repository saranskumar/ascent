"""Local OCR (RapidOCR, ONNX, CPU). Nothing leaves the machine."""
from __future__ import annotations

import re
import threading

_engine = None
_lock = threading.Lock()   # one OCR at a time: the engine is shared by request/job threads

# Words that only appear in meeting-app chrome. Lines made up *entirely* of these
# are dropped so chrome can't win the "longest text" pick (lecture-to-notes does
# the same with Zoom UI tokens). Extend as we see Meet / Zoom / Teams output.
UI_TOKENS = {
    "mute", "unmute", "camera", "video", "start", "stop", "share", "sharing",
    "screen", "chat", "participants", "people", "leave", "end", "record",
    "recording", "raise", "hand", "reactions", "more", "you", "present",
    "presenting", "meeting", "captions", "apps", "activities", "details",
    "turn", "on", "off", "audio", "settings", "invite", "security", "reactions",
    "participant", "view",
}


def _get_engine():
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
    return _engine


def _run(img):
    engine = _get_engine()
    with _lock:
        return engine(img)


def warm_up() -> None:
    """Load the OCR engine now. Must run BEFORE the first Windows Graphics Capture session in a
    process: if onnxruntime is first initialized after a WGC session, the next
    `start_free_threaded()` crashes with an access violation (reproduced Oct 2: capture -> OCR ->
    capture crashes; OCR -> capture -> OCR -> capture is fine)."""
    import numpy as np
    _run(np.full((64, 64, 3), 255, dtype=np.uint8))


def strip_ui_lines(text: str) -> str:
    keep = []
    for line in text.splitlines():
        words = re.findall(r"[A-Za-z']+", line.lower())
        if words and all(w in UI_TOKENS for w in words):
            continue
        keep.append(line)
    return "\n".join(keep)


def ocr_image(img_bgr, min_score: float = 0.5) -> str:
    """Return the text of a BGR ndarray, one line per detected text box,
    top-to-bottom, with meeting-app chrome lines removed."""
    result, _ = _run(img_bgr)
    if not result:
        return ""
    boxes = []
    for box, text, score in result:
        if float(score) < min_score or not text.strip():
            continue
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append((min(ys), min(xs), text.strip()))
    # Reading order: bucket by line (y within ~half a text height), then x.
    boxes.sort()
    lines: list[str] = []
    for _, _, t in boxes:
        lines.append(t)
    return strip_ui_lines("\n".join(lines))
