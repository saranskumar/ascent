"""Transcript + screenshots.json -> summary in Meetily's format, with the local model.

The model is text-in here: diagram screens were already described in the Describe stage, so
every `[SCREEN]` line is plain text (OCR, plus a description for diagrams).
"""
from __future__ import annotations

import re

from .ollama import Ollama
from .prompts import build_system_prompt, build_user_prompt
from .transcript import build_input


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


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(*])")


def drop_repeats(t: str) -> str:
    """Small models can loop ("The meeting was not structured... " x50). Drop every sentence that
    was already written earlier in the report, and lines that repeat an earlier line."""
    seen_sent: set[str] = set()
    seen_line: set[str] = set()
    out = []
    for line in t.split("\n"):
        key_line = re.sub(r"\W+", "", line.lower())
        if line.lstrip().startswith("|") or re.fullmatch(r"\*\*[^*]+\*\*", line.strip()) \
                or line.lstrip().startswith("#") or not key_line:
            out.append(line)                    # tables, headers, blank lines: keep as they are
            continue
        if key_line in seen_line and len(key_line) > 20:
            continue
        seen_line.add(key_line)
        kept = []
        for sent in _SENTENCE.split(line):
            k = re.sub(r"\W+", "", sent.lower())
            if len(k) > 12 and k in seen_sent:
                continue
            seen_sent.add(k)
            kept.append(sent)
        if kept:
            out.append(" ".join(kept))
    return "\n".join(out)


def trim_unfinished(t: str) -> str:
    """Hit the token limit mid-sentence: end at the last complete sentence of that paragraph."""
    t = t.rstrip()
    last = t.rsplit("\n", 1)[-1]
    if not last or last.lstrip().startswith(("|", "#", "**")) or last[-1] in ".!?)|*":
        return t
    cut = max(last.rfind(". "), last.rfind("! "), last.rfind("? "))
    head = t[: len(t) - len(last)]
    return (head + last[: cut + 1]).rstrip() if cut > 0 else head.rstrip()


def clean_output(text: str | None, cut_short: bool = False) -> str:
    """Meetily's clean-up: strip <think> blocks, unwrap a ```markdown fence, reject empty.
    Then drop repeated sentences (small models loop), trim a sentence cut off by the token
    limit, and fix spacing so the Markdown renders like Meetily's own summaries."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    m = re.fullmatch(r"```(?:markdown|md)\s*\n(.*?)\n?```", t, flags=re.DOTALL)
    if m:
        t = m.group(1).strip()
    t = drop_repeats(t)
    # A closing "Note: this summary is based only on..." is meta commentary (rule 7): drop it.
    paras = re.split(r"\n\s*\n", t.strip())
    while len(paras) > 1 and re.match(r"[*_(\s]*note\b", paras[-1], re.I):
        paras.pop()
    t = "\n\n".join(paras)
    if cut_short:
        t = trim_unfinished(t)
    if not t.strip():
        raise SummaryError("the model returned an empty summary")
    return normalize_spacing(t)


# Words people use when they decide or hand out work. Without any of them in the speech, the
# Decisions / Action Items a small model writes are made up (seen live: "develop an updated
# version within 48 hours" from a kids' quiz video nobody planned anything about).
COMMITMENT = re.compile(
    r"\b(we(?:'ll| will| should| need to| have to| must| agreed| decided| can)|let'?s|let us|"
    r"i(?:'ll| will) (?:do|send|take|check|follow|share|get|write|handle|look|fix|call|set)|"
    r"(?:can|could|would) you|please|decid\w*|agree\w*|deadline|action item|to-?do|assign\w*|"
    r"by (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|tonight|next|end of|eod)|"
    r"next (?:week|meeting|sprint|step)s?|follow[- ]up)\b", re.I)
SECTIONS_NEEDING_SPEECH = ("Key Decisions", "Action Items")


def has_commitments(segments: list[dict]) -> bool:
    return any(COMMITMENT.search(s.get("text") or "") for s in segments)


def clear_unspoken_commitments(text: str, segments: list[dict]) -> str:
    """If nobody said anything like a decision or a task, those sections are 'None noted'."""
    if has_commitments(segments):
        return text
    out, skipping = [], False
    for line in text.split("\n"):
        header = re.fullmatch(r"\s*\*\*([^*]+)\*\*\s*", line)
        if header:
            skipping = header.group(1).strip() in SECTIONS_NEEDING_SPEECH
            out.append(line)
            if skipping:
                out.extend(["", "None noted in this section.", ""])
            continue
        if not skipping:
            out.append(line)
    return normalize_spacing("\n".join(out))


def build_messages(segments: list[dict], shots: list[dict], offset: float = 0.0,
                   speaker_names: dict[str, str] | None = None) -> list[dict]:
    transcript_text = build_input(segments, shots, offset, speaker_names)
    if not transcript_text.strip():
        raise SummaryError("transcript and screens are both empty; nothing to summarize")
    return [{"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": build_user_prompt(transcript_text)}]


def summarize(client: Ollama, model: str, segments: list[dict], shots: list[dict], *,
              offset: float = 0.0, speaker_names: dict[str, str] | None = None,
              num_ctx: int = 16384, max_tokens: int = 2000, temperature: float = 0.3,
              repeat_penalty: float = 1.15,
              num_gpu: int | None = 0, keep_alive: str | int = "5m", on_token=None,
              cancel=lambda: False, timeout: float = 900, info: dict | None = None) -> str:
    """Return the cleaned summary. `info` receives model, token counts and timing."""
    messages = build_messages(segments, shots, offset, speaker_names)
    options = {"temperature": temperature, "num_ctx": int(num_ctx), "num_predict": int(max_tokens),
               "repeat_penalty": repeat_penalty, "repeat_last_n": 256}
    if num_gpu is not None:
        options["num_gpu"] = num_gpu
    out = client.chat_stream(model, messages, options, keep_alive=keep_alive, on_token=on_token,
                             cancel=cancel, timeout=timeout)
    if info is not None:
        info.update({k: out.get(k) for k in ("eval_count", "prompt_eval_count", "seconds",
                                             "done_reason")}, model=model)
    text = clean_output(out["text"], cut_short=out.get("done_reason") == "length")
    return clear_unspoken_commitments(text, segments)
