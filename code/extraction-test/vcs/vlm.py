"""Optional local vision model for diagram screens (Ollama / llama.cpp, OpenAI-compatible).

Only screens the extractor marked "diagram" (almost no OCR text) are sent here; everything
else stays OCR-only. Off unless VCS_VLM_MODEL is set, so the Gemini image path is unchanged.
Nothing leaves the machine: the endpoint defaults to Ollama on loopback.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

import cv2

DEFAULT_URL = "http://127.0.0.1:11434/v1/chat/completions"
MAX_SIDE = 1024      # downscale long side: the main speed lever for small VLMs
PROMPT = ("This is a screen shown in a meeting. State the facts it shows in 2-3 plain sentences: "
          "titles, labels, numbers, and how the parts relate. Do not describe colours or layout.")


def enabled() -> bool:
    return bool(os.environ.get("VCS_VLM_MODEL", "").strip())


def _jpeg_b64(path: Path) -> str:
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"cannot read image: {path}")
    h, w = img.shape[:2]
    scale = MAX_SIDE / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError(f"cannot encode image: {path}")
    return base64.b64encode(buf.tobytes()).decode()


def describe(image: Path, timeout: float = 120.0) -> str:
    """Return the model's description of one image. Raises on any failure; callers decide
    whether to fall back."""
    body = {
        "model": os.environ["VCS_VLM_MODEL"].strip(),
        "temperature": 0,
        "max_tokens": 200,
        "keep_alive": 0,   # Ollama: free VRAM right after the call (shared 4 GB GPU)
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + _jpeg_b64(Path(image))}},
        ]}],
    }
    req = urllib.request.Request(
        os.environ.get("VCS_VLM_URL", DEFAULT_URL).strip(),
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    return " ".join((out["choices"][0]["message"]["content"] or "").split())
