"""Settings, kept in data/settings.json. Everything the app does can be changed in the Settings
tab; this module only knows the defaults and the types.

One local model (Ollama) does both jobs: it describes diagram screens (vision) and writes the
summary. Nothing leaves the machine.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .extractor import Params

DEFAULT_MODEL = "qwen3-vl:2b-instruct"

# What the vision model is asked about each screen. Pictures matter (a quiz's fruit and animals,
# a product photo): it names what is shown and reads the words, so a spoken "find the biggest
# fruit" can be tied to what was on screen.
VLM_PROMPT = ("This is a screen shown in a meeting. Say what it shows in 1-2 plain sentences: name "
              "the objects, animals, people (by their role, not their looks) and pictures, and read "
              "out any words, letters and numbers. For a chart or diagram, give the labels, the "
              "values and how the parts relate. Do not describe colours or layout.")
OLD_VLM_PROMPTS = {"This is a screen shown in a meeting. State the facts it shows in 2-3 plain "
                   "sentences: titles, labels, numbers, and how the parts relate. Do not describe "
                   "colours or layout."}

DEFAULTS: dict = {
    # ---- model (Ollama, local)
    "model": DEFAULT_MODEL,
    "ollama_url": "http://127.0.0.1:11434",
    "device": "cpu",              # "cpu" (num_gpu 0) or "gpu" (Ollama decides how many layers fit)
    "num_ctx": 16384,             # Ollama's default 4096 would cut long transcripts
    "max_tokens": 2000,           # summary length cap
    "temperature": 0.3,
    "repeat_penalty": 1.15,      # >1 stops small models from looping the same sentence
    "vlm_max_tokens": 250,        # per diagram description
    "vlm_max_side": 1024,         # downscale long side before sending (main speed lever)
    "vlm_prompt": VLM_PROMPT,
    "describe_screens": "all",    # all | diagrams (screens with little text) | none
    "start_ollama": True,         # start Ollama with the app, and again if it stops
    "keep_loaded": True,          # keep the model in memory while jobs are queued
    "request_timeout": 900,       # seconds without a single token before a call is abandoned

    # ---- extraction (see extractor.Params)
    "interval": Params.interval,
    "hash_threshold": Params.hash_threshold,
    "drift_threshold": Params.drift_threshold,
    "min_dwell": Params.min_dwell,
    "diagram_chars": Params.diagram_chars,

    # ---- automation (Meetily webhooks)
    "auto_capture": True,         # recording.started -> capture a window
    "ask_window": True,           # ...picked in the window (off = use the remembered one)
    "auto_summarize": True,       # recording.stopped -> queue extract + summary right away
    "auto_publish": True,         # write ours into Meetily if it has none, else ask Replace/Keep
    "show_on_start": True,        # bring the window up when a recording starts
    "pick_timeout": 45,           # no answer in this many seconds: capture the last-used window (0 = wait)
    "show_on_stop": True,         # ...and on the Live tab when it stops
    "notify": True,               # tray notifications for done / failed / needs you
    "delete_screenshots_after_summary": False,
    "keep_capture_video": False,
    "watch": {},                  # last window picked: {hwnd, title, process}

    # ---- app
    "webhook_port": 8766,         # extraction-test uses 8765; both can be installed side by side
    "start_minimized": True,      # start in the tray
    "theme": "system",            # system | light | dark
    "queue_paused": False,
}

# Typed groups for the Settings page (key, label, hint). Order = display order.
NUMBER_LIMITS = {
    "num_ctx": (2048, 131072, 1024), "max_tokens": (200, 16000, 100),
    "temperature": (0.0, 2.0, 0.05), "repeat_penalty": (1.0, 2.0, 0.05), "vlm_max_tokens": (50, 2000, 10),
    "vlm_max_side": (256, 4096, 64), "request_timeout": (60, 7200, 30),
    "interval": (0.25, 30.0, 0.25), "hash_threshold": (1, 512, 1),
    "drift_threshold": (1, 1024, 1), "min_dwell": (0.0, 120.0, 0.5),
    "diagram_chars": (0, 1000, 5), "webhook_port": (1024, 65535, 1), "pick_timeout": (0, 600, 5),
}


class Settings:
    """Thread-safe dict-like settings with type-checked updates."""

    def __init__(self, data_dir: Path):
        self.file = Path(data_dir) / "settings.json"
        self._lock = threading.Lock()
        self._s = dict(DEFAULTS)
        if self.file.exists():
            try:
                saved = json.loads(self.file.read_text("utf-8"))
                self._s.update({k: v for k, v in saved.items() if k in DEFAULTS})
                if self._s.get("vlm_prompt") in OLD_VLM_PROMPTS:   # untouched old default
                    self._s["vlm_prompt"] = VLM_PROMPT
            except ValueError:
                pass

    def __getitem__(self, key):
        with self._lock:
            return self._s[key]

    def get(self, key, default=None):
        with self._lock:
            return self._s.get(key, default)

    def all(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._s))

    def update(self, new: dict) -> dict:
        """Apply the keys that exist and have the right type (ints are accepted for floats)."""
        with self._lock:
            for k, v in (new or {}).items():
                if k not in DEFAULTS:
                    continue
                d = DEFAULTS[k]
                if isinstance(d, bool):
                    v = bool(v)
                elif isinstance(d, float) and isinstance(v, (int, float)):
                    v = float(v)
                elif isinstance(d, int) and isinstance(v, (int, float)) and not isinstance(v, bool):
                    v = int(v)
                elif not isinstance(v, type(d)):
                    continue
                self._s[k] = v
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(self._s, indent=2), "utf-8")
            return dict(self._s)

    def reset(self, keys: list[str] | None = None) -> dict:
        return self.update({k: DEFAULTS[k] for k in (keys or DEFAULTS) if k != "watch"})

    def extract_params(self) -> Params:
        s = self.all()
        return Params(interval=float(s["interval"]), hash_threshold=int(s["hash_threshold"]),
                      drift_threshold=int(s["drift_threshold"]), min_dwell=float(s["min_dwell"]),
                      diagram_chars=int(s["diagram_chars"]))

    def num_gpu(self) -> int | None:
        """Ollama `num_gpu` option: 0 = CPU only, None = let Ollama decide."""
        return 0 if self["device"] == "cpu" else None
