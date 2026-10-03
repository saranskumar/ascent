"""Optional local vision model for diagram screens (Ollama native API).

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

DEFAULT_URL = "http://127.0.0.1:11434/api/chat"
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


def describe(image: Path, timeout: float = 120.0, model: str | None = None,
             num_gpu: int | None = None, prompt: str | None = None) -> str:
    """Return the model's description of one image. Raises on any failure; callers decide
    whether to fall back. model / num_gpu / prompt override the environment (test page)."""
    options = {"temperature": 0, "num_predict": 250}
    if num_gpu is None and os.environ.get("VCS_VLM_NUM_GPU", "").strip():
        num_gpu = int(os.environ["VCS_VLM_NUM_GPU"])      # 0 = CPU only (vision encoder needs
    if num_gpu is not None:                               # RAM/VRAM headroom on 4 GB GPUs)
        options["num_gpu"] = num_gpu
    body = {
        "model": (model or os.environ["VCS_VLM_MODEL"]).strip(),
        "stream": False,
        "keep_alive": 0,   # free memory right after the call
        "options": options,
        "messages": [{"role": "user", "content": prompt or PROMPT,
                      "images": [_jpeg_b64(Path(image))]}],
    }
    req = urllib.request.Request(
        os.environ.get("VCS_VLM_URL", DEFAULT_URL).strip(),
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    return " ".join((out["message"]["content"] or "").split())


def list_models() -> list[str]:
    """Models installed in the local Ollama (empty when it is not running)."""
    base = os.environ.get("VCS_VLM_URL", DEFAULT_URL).strip().rsplit("/api/", 1)[0]
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=3) as r:
            return sorted(m["name"] for m in json.load(r).get("models", []))
    except Exception:  # noqa: BLE001
        return []
