"""JSON-returning LLM call for the engine.

Gemini (fast flash-lite model) first; local llama.cpp (OpenAI-compatible server) when Gemini has no
key, is rate-limited or fails. A failing backend is benched for a while instead of retried each call.

Env (from the real environment, else code/copilot/.env, else code/extraction-test/.env):
    GEMINI_API_KEY
    COPILOT_GEMINI_MODELS   comma list, tried in order   (default gemini-3.5-flash-lite,gemini-flash-lite-latest)
    COPILOT_LOCAL_LLM_URL   default http://127.0.0.1:8080/v1/chat/completions
    COPILOT_LOCAL_MODEL     default qwen3-8b-Q4_K_M
    COPILOT_LLM             auto | gemini | local   (default auto)
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    for f in (ROOT / ".env", ROOT.parent / "extraction-test" / ".env"):
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = (s.strip() for s in line.split("=", 1))
            if k and v and not os.environ.get(k):
                os.environ[k] = v.strip('"').strip("'")


load_env()

GEMINI_MODELS = [m.strip() for m in os.environ.get(
    "COPILOT_GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-flash-lite-latest").split(",") if m.strip()]
LOCAL_URL = os.environ.get("COPILOT_LOCAL_LLM_URL", "http://127.0.0.1:8080/v1/chat/completions")
LOCAL_MODEL = os.environ.get("COPILOT_LOCAL_MODEL", "qwen3-8b-Q4_K_M")
MODE = os.environ.get("COPILOT_LLM", "auto")


class LLMError(Exception):
    pass


_benched: dict[str, float] = {}   # backend name -> monotonic time it may be used again
_client = None


def _usable(name: str) -> bool:
    return time.monotonic() >= _benched.get(name, 0)


def _bench(name: str, seconds: float, why: str) -> None:
    _benched[name] = time.monotonic() + seconds
    print(f"[llm] {name} benched {seconds:.0f}s: {why}")


def _gemini(model: str, system: str, user: str, timeout: float) -> str:
    global _client
    from google import genai
    from google.genai import types
    if _client is None:
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                               http_options=types.HttpOptions(timeout=int(timeout * 1000)))
    r = _client.models.generate_content(
        model=model, contents=user,
        config=types.GenerateContentConfig(system_instruction=system, temperature=0.2,
                                           response_mime_type="application/json"))
    return r.text or ""


def _local(system: str, user: str, timeout: float) -> str:
    body = {"model": LOCAL_MODEL, "temperature": 0.2, "max_tokens": 300,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(LOCAL_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def backends() -> list[str]:
    out = []
    if MODE in ("auto", "gemini") and os.environ.get("GEMINI_API_KEY"):
        out += [f"gemini:{m}" for m in GEMINI_MODELS]
    if MODE in ("auto", "local"):
        out.append("local")
    return out


def complete_json(system: str, user: str, timeout: float = 12) -> tuple[dict, str]:
    """Returns (parsed JSON object, backend used). Raises LLMError if every backend failed."""
    errors = []
    for name in backends():
        if not _usable(name):
            continue
        try:
            if name == "local":
                text = _local(system, user, timeout=max(timeout, 30))   # first call loads the model
            else:
                text = _gemini(name.split(":", 1)[1], system, user, timeout)
            return _parse(text), name
        except Exception as e:
            msg = str(e)
            code = getattr(e, "code", None)
            if code == 429 or "RESOURCE_EXHAUSTED" in msg:
                _bench(name, 300, "rate limited")
            elif code == 404:
                _bench(name, 3600, "model not found")
            elif isinstance(e, (ValueError, json.JSONDecodeError)):
                pass                                   # bad JSON this once; try the next backend
            else:
                _bench(name, 30, f"{type(e).__name__}: {msg[:120]}")
            errors.append(f"{name}: {type(e).__name__}: {msg[:120]}")
    raise LLMError("; ".join(errors) or "no LLM backend available")


def _parse(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)       # tolerate ```json fences or chatter around it
    d = json.loads(m.group(0) if m else text)
    if not isinstance(d, dict):
        raise ValueError("LLM did not return a JSON object")
    return d


def warm_local() -> None:
    """Load the local model in the background so a later fallback isn't a 20 s wait."""
    if "local" in backends():
        try:
            _local("Reply with JSON.", '{"ping": true}', timeout=120)
        except Exception as e:
            print(f"[llm] local warm-up failed: {e}")
