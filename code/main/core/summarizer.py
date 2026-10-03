"""Transcript + screenshots.json -> summary in Meetily's format, with the local model.

The model is text-in here: diagram screens were already described in the Describe stage, so
every `[SCREEN]` line is plain text (OCR, plus a description for diagrams).
"""
from __future__ import annotations

import re

from .ollama import Ollama
from .prompts import (CHUNK_SYSTEM, COMBINE_SYSTEM, build_chunk_prompt, build_combine_prompt,
                      build_system_prompt, build_user_prompt, get_template)
from .transcript import build_input, mmss, screen_lines


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




# ------------------------------------------------------------------ long meetings (Meetily's way)
def rough_token_count(s: str) -> int:
    """Meetily's estimate (summary/processor.rs): 0.35 tokens per character."""
    return int(len(s or "") * 0.35 + 0.999)


def chunk_text(text: str, chunk_tokens: int, overlap_tokens: int = 100) -> list[str]:
    """Meetily's chunker, adapted: chunks of ~chunk_tokens with ~overlap_tokens repeated at the
    start of the next one. It breaks at a line (our input is one transcript or [SCREEN] line per
    row, so no line is ever cut), else at a sentence, else at a word."""
    if not text or chunk_tokens <= 0:
        return [text] if text else []
    size = int(chunk_tokens / 0.35)
    overlap = int(overlap_tokens / 0.35)
    if len(text) <= size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            for sep in ("\n", ". ", " "):
                cut = window.rfind(sep)
                if cut > overlap:
                    end = start + cut + len(sep)
                    break
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        nxt = max(end - overlap, start + 1)
        line_start = text.rfind("\n", start, nxt)          # begin the overlap at a whole line
        start = line_start + 1 if line_start > start else nxt
    return [c for c in chunks if c]


def context_budget(num_ctx: int, max_tokens: int, system_prompt: str) -> int:
    """Tokens of source text that fit one call: the context minus the answer, the instructions
    and Meetily's 300-token margin."""
    return max(800, int(num_ctx) - int(max_tokens) - rough_token_count(system_prompt) - 300)


def build_messages(segments: list[dict], shots: list[dict], offset: float = 0.0,
                   speaker_names: dict[str, str] | None = None, template: dict | None = None
                   ) -> list[dict]:
    transcript_text = build_input(segments, shots, offset, speaker_names)
    if not transcript_text.strip():
        raise SummaryError("transcript and screens are both empty; nothing to summarize")
    return [{"role": "system", "content": build_system_prompt(t=template or get_template(None))},
            {"role": "user", "content": build_user_prompt(transcript_text)}]


def summarize(client: Ollama, model: str, segments: list[dict], shots: list[dict], *,
              offset: float = 0.0, speaker_names: dict[str, str] | None = None,
              template: dict | None = None, chunk_tokens: int = 0,
              num_ctx: int = 16384, max_tokens: int = 2000, temperature: float = 0.3,
              repeat_penalty: float = 1.15,
              num_gpu: int | None = 0, keep_alive: str | int = "5m", on_token=None,
              cancel=lambda: False, timeout: float = 900, info: dict | None = None,
              log=lambda *_: None, progress=lambda *_: None) -> str:
    """Return the cleaned summary. `info` receives model, token counts, timing and chunk count.

    Fits in the context: one call, like before. Too long (or chunk_tokens set lower): Meetily's
    two passes - summarize each chunk, combine the chunk summaries (in rounds if they are still
    too long), then fill the template from the combined text. progress(fraction, label)."""
    template = template or get_template(None)
    system = build_system_prompt(t=template)
    source = build_input(segments, shots, offset, speaker_names)
    if not source.strip():
        raise SummaryError("transcript and screens are both empty; nothing to summarize")
    options = {"temperature": temperature, "num_ctx": int(num_ctx), "num_predict": int(max_tokens),
               "repeat_penalty": repeat_penalty, "repeat_last_n": 256}
    if num_gpu is not None:
        options["num_gpu"] = num_gpu
    budget = context_budget(num_ctx, max_tokens, system)
    if chunk_tokens:
        budget = min(budget, max(400, int(chunk_tokens)))
    total = rough_token_count(source)
    stats = {"chunks": 1, "calls": 0, "source_tokens": total, "budget_tokens": budget}
    parts: list[dict] = []          # per-part summaries, saved with the meeting
    combined = ""

    def call(system_prompt: str, user: str, stream: bool = False) -> dict:
        stats["calls"] += 1
        out = client.chat_stream(model, [{"role": "system", "content": system_prompt},
                                         {"role": "user", "content": user}], options,
                                 keep_alive=keep_alive, on_token=on_token if stream else None,
                                 cancel=cancel, timeout=timeout)
        return out

    if total <= budget:
        log(f"transcript + screens ~{total} tokens: fits the model's context in one pass")
        user = build_user_prompt(source)
    else:
        chunks = [carry_screens(c, shots, offset) for c in chunk_text(source, budget, 100)]
        stats["chunks"] = len(chunks)
        log(f"transcript + screens ~{total} tokens, more than fits one call (~{budget}): "
            f"summarizing {len(chunks)} parts along the timeline, then combining them "
            f"(Meetily's method)")
        notes = []
        for i, chunk in enumerate(chunks, 1):
            progress(0.05 + 0.55 * (i - 1) / len(chunks), f"part {i}/{len(chunks)}")
            out = call(CHUNK_SYSTEM, build_chunk_prompt(chunk, i, len(chunks)))
            note = clean_output(out["text"], cut_short=out.get("done_reason") == "length")
            notes.append(note)
            span = chunk_span(chunk)
            parts.append({"part": i, "from": span[0], "to": span[1], "summary": note})
            log(f"part {i}/{len(chunks)} ({span[0]}-{span[1]}) summary:\n" + note)
        rnd = 0
        while len(notes) > 1 and rough_token_count(build_combine_prompt(notes)) > budget:
            rnd += 1                         # still too long for one combine: combine in groups
            groups, cur = [], []
            for n in notes:
                if cur and rough_token_count(build_combine_prompt(cur + [n])) > budget:
                    groups.append(cur)
                    cur = []
                cur.append(n)
            groups.append(cur)
            if len(groups) == len(notes):    # each note alone is too big: can't shrink further
                break
            log(f"combining round {rnd}: {len(notes)} summaries into {len(groups)}")
            notes = [clean_output(call(COMBINE_SYSTEM, build_combine_prompt(g))["text"])
                     if len(g) > 1 else g[0] for g in groups]
        if len(notes) > 1:
            progress(0.65, "combining the parts")
            combined = clean_output(call(COMBINE_SYSTEM, build_combine_prompt(notes))["text"])
            log("combined summary:\n" + combined)
        else:
            combined = notes[0]
        user = f"<transcript_chunks>\n{combined}\n</transcript_chunks>"
        progress(0.75, "writing the report")

    out = call(system, user, stream=True)
    if info is not None:
        info.update({k: out.get(k) for k in ("eval_count", "prompt_eval_count", "seconds",
                                             "done_reason")}, model=model, template=template.get("name"),
                    **stats)
        info["parts"], info["combined"] = parts, combined
    text = clean_output(out["text"], cut_short=out.get("done_reason") == "length")
    return clear_unspoken_commitments(text, segments)


_TIME = re.compile(r"^\[(\d+):(\d{2})(?::(\d{2}))?\]", re.M)


def _secs(m) -> float:
    a, b, c = m.group(1), m.group(2), m.group(3)
    return int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)


def chunk_span(chunk: str) -> tuple[str, str]:
    """First and last [MM:SS] of a part, for the log and the saved part summaries."""
    times = [_secs(m) for m in _TIME.finditer(chunk)]
    if not times:
        return "?", "?"
    return mmss(min(times)), mmss(max(times))


def carry_screens(chunk: str, shots: list[dict], offset: float = 0.0) -> str:
    """A screen still showing when a part starts belongs to that part too: its [SCREEN] line sits
    only in the part where it first appeared, so speech in the next part ("as you can see…")
    would lose it. Repeat it at the top of the part, marked as still on screen."""
    first = _TIME.search(chunk)
    if not first or not shots:
        return chunk
    t0 = _secs(first)
    carried = []
    for t, line in screen_lines(shots, offset):
        shot_end = next((s["end"] + offset for s in shots if abs(s["start"] + offset - t) < 0.01), t)
        if t < t0 < shot_end and line not in chunk:
            carried.append(line.replace("[SCREEN] (", "[SCREEN] (still on screen; ", 1))
    return "\n".join(carried + [chunk]) if carried else chunk
