"""Step (b): transcript + screenshots.json -> summary in Meetily's format.

Two providers: a local Qwen through Ollama when VCS_SUMMARY_MODEL is set (nothing leaves the
machine), otherwise Gemini."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

from .prompts import build_system_prompt, build_user_prompt
from .transcript import DiagramRef, build_input, mmss

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
# Used only when the main model stays overloaded (503) after retries; "" disables it.
FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")
TEMPERATURE = 0.3
TIMEOUT_MS = 180_000
BACKOFF = (5,)             # one retry, then the fallback model (503s seen to last minutes)


class SummaryError(Exception):
    pass


def normalize_spacing(t: str) -> str:
    """Meetily's stored summaries keep a blank line around each `**Section**` header and
    before tables/lists; without it Markdown glues the header onto the next paragraph."""
    out: list[str] = []
    lines = t.split("\n")
    for i, line in enumerate(lines):
        s = line.strip()
        is_header = bool(re.fullmatch(r"\*\*[^*]+\*\*", s)) or s.startswith("#")
        prev = out[-1].strip() if out else ""
        starts_block = (s.startswith("|") and not prev.startswith("|")) or \
                       (re.match(r"[-*] ", s) and not re.match(r"[-*] ", prev) and not is_header)
        if out and prev and (is_header or starts_block):
            out.append("")
        out.append(line.rstrip())
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if is_header and nxt:
            out.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def clean_output(text: str | None) -> str:
    """Meetily's clean-up: strip <think> blocks, unwrap a ```markdown fence, reject empty.
    Then fix spacing so the Markdown renders like Meetily's own summaries."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    m = re.fullmatch(r"```(?:markdown|md)\s*\n(.*?)\n?```", t, flags=re.DOTALL)
    if m:
        t = m.group(1).strip()
    if not t:
        raise SummaryError("the model returned an empty summary")
    return normalize_spacing(t)


def build_contents(transcript_text: str, diagrams: list[DiagramRef], shots_dir: Path):
    """User message parts: the transcript, then each diagram image after a label."""
    from google.genai import types
    parts = [types.Part.from_text(text=build_user_prompt(transcript_text))]
    for d in diagrams:
        path = Path(shots_dir) / d.shot["image"]
        if not path.is_file():
            continue
        parts.append(types.Part.from_text(
            text=f"Image {d.number}: the diagram screen shown from {mmss(d.time)}."))
        parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type="image/jpeg"))
    return parts


def _no_quota(e) -> bool:
    """429 because the project's quota limit is 0: retrying can't help."""
    if getattr(e, "code", None) != 429:
        return False
    body = getattr(e, "details", None)
    infos = ((body.get("error") or {}).get("details") or []) if isinstance(body, dict) else []
    return any(str((i.get("metadata") or {}).get("quota_limit_value")) == "0"
               for i in infos if isinstance(i, dict))


def _client(api_key: str | None):
    from google import genai
    from google.genai import types
    key = api_key or os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise SummaryError("GEMINI_API_KEY is not set")
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))


# ------------------------------------------------------------------ local (Ollama)
def local_model() -> str:
    """The local summary model ("" = use Gemini)."""
    return os.environ.get("VCS_SUMMARY_MODEL", "").strip()


def _local_url() -> str:
    return os.environ.get("VCS_SUMMARY_URL", "http://127.0.0.1:11434/api/chat").strip()


def _prepare_local_shots(shots: list[dict], shots_dir: Path, log) -> list[dict]:
    """A text-only model can't look at images: every diagram needs a written description.
    Describe the ones that lack it (local VLM if configured); otherwise fall back to OCR only."""
    from . import vlm
    out = []
    for s in shots:
        if s.get("type") == "diagram" and not s.get("description"):
            s = dict(s)
            if vlm.enabled():
                try:
                    s["description"] = vlm.describe(Path(shots_dir) / s["image"])
                except Exception as e:  # noqa: BLE001
                    log(f"screen {s.get('id')}: VLM failed ({e}); using OCR text only")
            if not s.get("description"):
                s["type"] = "slide"
        out.append(s)
    return out


def summarize_local(segments: list[dict], screenshots_doc: dict, shots_dir: Path, *,
                    offset: float = 0.0, info: dict | None = None, log=lambda *_: None,
                    speaker_names: dict[str, str] | None = None, timeout: float = 900.0) -> str:
    model = local_model()
    shots = _prepare_local_shots(screenshots_doc.get("screenshots", []), shots_dir, log)
    transcript_text, _ = build_input(segments, shots, offset, speaker_names)
    if not transcript_text.strip():
        raise SummaryError("transcript is empty; nothing to summarize")
    options = {"temperature": TEMPERATURE,
               "num_ctx": int(os.environ.get("VCS_SUMMARY_CTX", "16384")),
               "num_predict": int(os.environ.get("VCS_SUMMARY_MAX_TOKENS", "2000"))}
    gpu = os.environ.get("VCS_SUMMARY_NUM_GPU", os.environ.get("VCS_VLM_NUM_GPU", "")).strip()
    if gpu:
        options["num_gpu"] = int(gpu)
    body = {"model": model, "stream": False, "keep_alive": 0, "options": options,
            "messages": [{"role": "system", "content": build_system_prompt()},
                         {"role": "user", "content": build_user_prompt(transcript_text)}]}
    req = urllib.request.Request(_local_url(), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)
    except Exception as e:  # noqa: BLE001
        raise SummaryError(f"local model {model} failed: {e}. Is Ollama running and the model "
                           f"pulled (ollama pull {model})?") from e
    text = clean_output((out.get("message") or {}).get("content"))
    if info is not None:
        info["model"] = model
    return text


def summarize(segments: list[dict], screenshots_doc: dict, shots_dir: Path, *,
              offset: float = 0.0, model: str | None = None, api_key: str | None = None,
              client=None, info: dict | None = None, log=lambda *_: None,
              sleep=time.sleep, speaker_names: dict[str, str] | None = None) -> str:
    """Return the cleaned summary. `info["model"]` is set to the model that answered."""
    if local_model() and client is None:
        return summarize_local(segments, screenshots_doc, shots_dir, offset=offset, info=info,
                               log=log, speaker_names=speaker_names)
    from google.genai import errors, types
    shots = screenshots_doc.get("screenshots", [])
    transcript_text, diagrams = build_input(segments, shots, offset, speaker_names)
    if not transcript_text.strip():
        raise SummaryError("transcript is empty; nothing to summarize")
    contents = build_contents(transcript_text, diagrams, shots_dir)
    config = types.GenerateContentConfig(
        system_instruction=build_system_prompt(), temperature=TEMPERATURE,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    client = client or _client(api_key)
    main = model or DEFAULT_MODEL
    models = [main] + ([FALLBACK_MODEL] if FALLBACK_MODEL and FALLBACK_MODEL != main else [])

    last: Exception | None = None
    for mi, m in enumerate(models):
        if mi:
            log(f"{models[mi - 1]} is overloaded; falling back to {m}")
        for attempt in range(len(BACKOFF) + 1):
            try:
                resp = client.models.generate_content(model=m, contents=contents, config=config)
                text = clean_output(resp.text)
                if info is not None:
                    info["model"] = m
                return text
            except errors.APIError as e:
                last = e
                code = getattr(e, "code", None)
                if _no_quota(e):
                    raise SummaryError(
                        f"this Gemini API key has no quota for {m} (limit 0). Create a key in a "
                        "new project at https://aistudio.google.com/apikey or enable billing.") from e
                if code in (429, 500, 503, 504) and attempt < len(BACKOFF):
                    log(f"{m}: {code}, retrying in {BACKOFF[attempt]}s")
                    sleep(BACKOFF[attempt])
                    continue
                if code in (503, 504):
                    break                       # overloaded: try the fallback model
                raise SummaryError(f"Gemini API error: {e}") from e
    raise SummaryError(f"Gemini is overloaded (tried {', '.join(models)}); try again shortly. "
                       f"Last error: {last}")
