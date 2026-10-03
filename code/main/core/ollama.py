"""Local model calls through Ollama's native API (loopback only; nothing leaves the machine).

One model does both jobs: `describe()` turns a diagram screen into a few sentences (vision),
`chat_stream()` writes the summary token by token so the Live tab can show it as it grows.
"""
from __future__ import annotations

import base64
import json
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2

from .errors import Canceled


class OllamaError(Exception):
    pass


def _post(url: str, body: dict, timeout: float):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _explain(e: Exception, base: str, model: str) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("error", "")
        except Exception:  # noqa: BLE001
            detail = ""
        if e.code == 404 or "not found" in detail:
            return f"model {model} isn't installed in Ollama (run: ollama pull {model})"
        return f"Ollama error {e.code}: {detail or e.reason}"
    if isinstance(e, (urllib.error.URLError, ConnectionError)):
        return f"can't reach Ollama at {base}. Is it running? ({e})"
    if isinstance(e, (TimeoutError, socket.timeout)):
        return "Ollama didn't answer in time (model too slow, or stuck loading)"
    return f"{type(e).__name__}: {e}"


def find_executable() -> tuple[str, list[str]] | None:
    """How to start Ollama here: the tray app (what its installer sets up), else `ollama serve`."""
    import os
    import shutil
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"
    app = local / "ollama app.exe"
    if app.is_file():
        return str(app), []
    cli = local / "ollama.exe"
    if cli.is_file():
        return str(cli), ["serve"]
    found = shutil.which("ollama")
    return (found, ["serve"]) if found else None


def is_local(base: str) -> bool:
    from urllib.parse import urlparse
    return (urlparse(base).hostname or "") in ("127.0.0.1", "localhost", "::1")


class Ollama:
    def __init__(self, base: str = "http://127.0.0.1:11434"):
        self.base = base.rstrip("/")

    # ---- running
    def up(self, timeout: float = 2) -> bool:
        try:
            with urllib.request.urlopen(self.base + "/api/version", timeout=timeout) as r:
                r.read()
            return True
        except Exception:  # noqa: BLE001
            return False

    def ensure_running(self, wait: float = 40, log=lambda *_: None) -> bool:
        """Start Ollama if it isn't answering (only for an Ollama on this computer). Returns
        whether it answers now. Safe to call often: it's one quick request when Ollama is up."""
        if self.up():
            return True
        if not is_local(self.base):
            return False
        exe = find_executable()
        if exe is None:
            log("Ollama isn't running and isn't installed where expected; install it from ollama.com")
            return False
        import subprocess
        path, args = exe
        log(f"Ollama isn't running; starting it ({Path(path).name})")
        try:
            subprocess.Popen([path, *args], close_fds=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                             | getattr(subprocess, "DETACHED_PROCESS", 0),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            log(f"couldn't start Ollama: {e}")
            return False
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(1)
            if self.up():
                log("Ollama is running")
                return True
        log(f"started Ollama, but it didn't answer within {wait:.0f}s")
        return False

    # ---- info
    def models(self, timeout: float = 3) -> list[str]:
        """Installed models; raises OllamaError when Ollama isn't reachable."""
        try:
            with urllib.request.urlopen(self.base + "/api/tags", timeout=timeout) as r:
                return sorted(m["name"] for m in json.load(r).get("models", []))
        except Exception as e:  # noqa: BLE001
            raise OllamaError(_explain(e, self.base, "")) from None

    def loaded(self, timeout: float = 3) -> list[dict]:
        """Models currently in memory ({name, size_vram, ...})."""
        try:
            with urllib.request.urlopen(self.base + "/api/ps", timeout=timeout) as r:
                return json.load(r).get("models", [])
        except Exception:  # noqa: BLE001
            return []

    def unload(self, model: str) -> None:
        """Free the model's memory now (keep_alive 0 with no prompt)."""
        try:
            with _post(self.base + "/api/generate", {"model": model, "keep_alive": 0}, 30) as r:
                r.read()
        except Exception:  # noqa: BLE001 - nothing loaded / Ollama gone: nothing to free
            pass

    # ---- chat
    def chat_stream(self, model: str, messages: list[dict], options: dict, *,
                    keep_alive: str | int = "5m", on_token=None, cancel=lambda: False,
                    timeout: float = 900) -> dict:
        """Stream a chat reply. on_token(piece) for every chunk; cancel() is polled between
        chunks. `timeout` is the longest wait for the next chunk (the first one includes model
        load + reading the prompt). Returns {"text", "eval_count", "prompt_eval_count", "seconds"}."""
        body = {"model": model, "messages": messages, "stream": True, "options": options,
                "keep_alive": keep_alive}
        t0 = time.monotonic()
        parts: list[str] = []
        final: dict = {}
        try:
            r = _post(self.base + "/api/chat", body, timeout)
        except Exception as e:  # noqa: BLE001
            raise OllamaError(_explain(e, self.base, model)) from None
        try:
            for raw in r:
                if cancel():
                    raise Canceled()
                if not raw.strip():
                    continue
                msg = json.loads(raw)
                if msg.get("error"):
                    raise OllamaError(f"Ollama: {msg['error']}")
                piece = (msg.get("message") or {}).get("content") or ""
                if piece:
                    parts.append(piece)
                    if on_token:
                        on_token(piece)
                if msg.get("done"):
                    final = msg
                    break
        except (Canceled, OllamaError):
            raise
        except Exception as e:  # noqa: BLE001
            raise OllamaError(_explain(e, self.base, model)) from None
        finally:
            r.close()
        return {"text": "".join(parts), "eval_count": final.get("eval_count"),
                "prompt_eval_count": final.get("prompt_eval_count"),
                "done_reason": final.get("done_reason"),
                "seconds": round(time.monotonic() - t0, 1)}

    # ---- vision
    def describe(self, image: Path, model: str, prompt: str, *, num_gpu: int | None = 0,
                 max_tokens: int = 250, max_side: int = 1024, keep_alive: str | int = "5m",
                 cancel=lambda: False, timeout: float = 600, on_token=None) -> str:
        options = {"temperature": 0, "num_predict": int(max_tokens),
                   "repeat_penalty": 1.15, "repeat_last_n": 128}
        if num_gpu is not None:
            options["num_gpu"] = num_gpu
        msgs = [{"role": "user", "content": prompt, "images": [jpeg_b64(Path(image), max_side)]}]
        out = self.chat_stream(model, msgs, options, keep_alive=keep_alive, cancel=cancel,
                               timeout=timeout, on_token=on_token)
        return tidy_description(out["text"])


_SENT = re.compile(r"(?<=[.!?])\s+")


def tidy_description(text: str, max_sentences: int = 14) -> str:
    """Small vision models loop ("The video is in a Settings mode." x15, seen live): keep each
    sentence once, at most a few, and drop a last sentence cut off by the token limit."""
    seen, out = set(), []
    sents = _SENT.split(" ".join((text or "").split()))
    for i, s in enumerate(sents):
        key = re.sub(r"\W+", "", s.lower())
        if not key or key in seen:
            continue
        if i == len(sents) - 1 and s[-1:] not in ".!?\"')" and out:
            break                                   # unfinished last sentence
        seen.add(key)
        out.append(s)
        if len(out) >= max_sentences:
            break
    return " ".join(out)


def jpeg_b64(path: Path, max_side: int = 1024) -> str:
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"cannot read image: {path}")
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError(f"cannot encode image: {path}")
    return base64.b64encode(buf.tobytes()).decode()
