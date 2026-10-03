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


URL = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|org|net|io|ai|dev|app)\b)", re.I)


def strip_ui_lines(text: str) -> str:
    keep = []
    for line in text.splitlines():
        if URL.search(line):            # address bars, links: never meeting content
            continue
        words = re.findall(r"[A-Za-z']+", line.lower())
        if words and all(w in UI_TOKENS for w in words):
            continue
        keep.append(line)
    return "\n".join(keep)


def ocr_image(img_bgr, min_score: float = 0.5, skip_edges: dict | None = None) -> str:
    """Return the text of a BGR ndarray, one line per detected text box,
    top-to-bottom, with meeting-app chrome lines removed.
    skip_edges={"top": .09, "left": .16, ...}: drop text whose centre lies in those bands of the
    image (a browser's tab strip, toolbar and vertical tabs, when its content area is unknown)."""
    result, _ = _run(img_bgr)
    if not result:
        return ""
    h, w = img_bgr.shape[:2]
    boxes = []
    for box, text, score in result:
        if float(score) < min_score or not text.strip():
            continue
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        if skip_edges:
            cx, cy = sum(xs) / len(xs) / w, sum(ys) / len(ys) / h
            if (cy < skip_edges.get("top", 0) or cx < skip_edges.get("left", 0)
                    or cx > 1 - skip_edges.get("right", 0) or cy > 1 - skip_edges.get("bottom", 0)):
                continue
        boxes.append((min(ys), min(xs), text.strip()))
    # Reading order: bucket by line (y within ~half a text height), then x.
    boxes.sort()
    lines: list[str] = []
    for _, _, t in boxes:
        lines.append(t)
    return strip_ui_lines("\n".join(lines))
