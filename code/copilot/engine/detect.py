"""Decide when a picture would help, and what to search for.

Two paths (the "mix"):
  fast  a final segment contains a pointing phrase ("let me explain", "looks like a", "if you plot")
        -> ask the LLM right away, focused on that line -> `elevated` suggestion
  slow  every few seconds, if new speech arrived -> ask the LLM about the last ~30 s, focused on
        the new lines -> `ambient` suggestion
If the LLM is unreachable on the fast path, the noun phrase after the cue is used as the query.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from contracts.messages import Segment

from . import llm

CUES = [
    r"\blet me (?:show|explain|draw|walk you through|illustrate)\b",
    r"\b(?:take a )?look at\b",
    r"\bas you can see\b",
    r"\bhere(?:'s| is| are) (?:the|a|an|our|what|how)\b",
    r"\b(?:looks?|looked) (?:like|something like)\b",
    r"\b(?:something|kind of|sort of) like (?:a|an|this)\b",
    r"\blike (?:this|that)\b",
    r"\b(?:picture|imagine|visuali[sz]e) (?:this|a|an|the)\b",
    r"\bif you (?:plot|draw|graph|chart|sketch|look at)\b",
    r"\bthis (?:diagram|chart|graph|picture|image|photo|architecture|layout|design|drawing|figure)\b",
    r"\bwhat (?:it|that|this) looks like\b",
]
CUE_RE = re.compile("|".join(CUES), re.I)

SYSTEM = """You are the visual co-pilot in a live meeting. You read what the host has been saying and decide whether showing the audience ONE picture right now would help them follow. If yes, you write a web image search query; the host picks from the results.

Suggest a picture only for something concrete and depictable that is being talked about in the NEW lines:
- a physical thing: object, device, product, part, vehicle, machine, place, animal, plant ("split keyboard", "cycloidal drive")
- a technical idea people usually draw: architecture, design pattern, algorithm, protocol, data structure, state machine, pipeline, process flow
- the shape of a chart or graph the speaker is describing ("the delay doubles each time")
- a well-known named thing (landmark, famous experiment, notable product)

Do NOT suggest a picture for: greetings, small talk, logistics and scheduling, next steps, opinions, questions about the meeting, vague abstract talk ("the issue", "our plan"), or specific people. If it is the team's own private system, suggest only when a generic picture of the same kind of thing would help (e.g. "API gateway microservices architecture diagram"); never search for private names or internal numbers.

If a topic in ALREADY SUGGESTED covers the same thing, answer visual_needed=false, unless the speaker has clearly moved to a different part of it that needs its own picture.

Answer with one JSON object:
{"visual_needed": true|false,
 "topic": "2-5 words, what the picture shows, Title Case",
 "query": "web image search query, 3-8 words, specific, no filler like 'let me' or 'this'. End with exactly one of: 'diagram' for drawn ideas (use 'architecture diagram' for how a system is built, 'sequence diagram' only if the speaker walks through messages step by step), 'chart' for graphs, 'photo' for physical things (even technical parts like wheels or gearboxes, unless the speaker explains how they work inside)",
 "alt_queries": ["two more search queries for the SAME topic from clearly different angles, so the host gets varied options instead of 20 near-identical shots: change the viewpoint, setting, or representation (e.g. close-up vs in use vs labelled diagram, or photo vs infographic), never just rephrase. Same format as query."],
 "kind": "diagram" | "photo" | "chart" | "illustration",
 "reason": "the short quote (max 12 words) from the transcript that triggered it",
 "confidence": 0.0-1.0}
When visual_needed is false, the other fields may be empty."""


@dataclass
class Need:
    topic: str
    query: str
    kind: str
    reason: str
    priority: str
    confidence: float
    path: str          # "fast" | "slow" | "heuristic"
    backend: str = ""
    alt_queries: list = field(default_factory=list)   # other angles on the same topic (searched too)
    run: str = ""      # engine's id for this detection, so dev tools can follow it through search


def find_cue(text: str) -> re.Match | None:
    return CUE_RE.search(text)


def _fmt(s: Segment, new: bool) -> str:
    who = f"{s.speaker}: " if s.speaker else ""
    return f"{'NEW ' if new else '    '}[{s.start:6.1f}] {who}{s.text}"


def build_prompt(window: list[Segment], new_ids: set[str], recent: list[str], cue: str | None) -> str:
    lines = "\n".join(_fmt(s, s.id in new_ids) for s in window)
    parts = [f"TRANSCRIPT (oldest first, NEW = said since you last looked):\n{lines}",
             f"ALREADY SUGGESTED: {', '.join(recent) if recent else '(nothing yet)'}"]
    if cue:
        parts.append(f'The host just used a pointing phrase ("{cue}") in the last NEW line, so they '
                     "probably want to show something now. Focus on the thing that phrase points at "
                     "(the noun phrase just before or after it).")
    return "\n\n".join(parts)


def ask(window: list[Segment], new_ids: set[str], recent: list[str], cue: str | None) -> Need | None:
    d, backend = llm.complete_json(SYSTEM, build_prompt(window, new_ids, recent, cue))
    if not d.get("visual_needed"):
        return None
    topic, query = str(d.get("topic") or "").strip(), str(d.get("query") or "").strip()
    if not topic or not query:
        return None
    conf = _float(d.get("confidence"), 0.5)
    if conf < (0.45 if cue else 0.6):
        return None
    kind = str(d.get("kind") or "illustration").lower()
    if kind not in ("diagram", "photo", "chart", "illustration"):
        kind = "illustration"
    alts = d.get("alt_queries") or []
    alts = [str(a).strip()[:120] for a in alts if isinstance(a, str) and a.strip()
            and a.strip().lower() != query.lower()][:2]
    return Need(topic=topic[:60], query=query[:120], kind=kind, alt_queries=alts,
                reason=str(d.get("reason") or "").strip()[:120],
                priority="elevated" if cue else "ambient", confidence=conf,
                path="fast" if cue else "slow", backend=backend)


STOP = {"the", "a", "an", "this", "that", "it", "how", "what", "our", "my", "your", "of", "is",
        "works", "work", "so", "we", "you", "and", "to", "in", "on", "be", "one", "thing"}


def heuristic(seg: Segment, m: re.Match) -> Need | None:
    """No LLM: take up to 5 content words around the cue (after it first, else before it)."""
    after = re.split(r"[.,;:!?]", seg.text[m.end():], maxsplit=1)[0]
    before = re.split(r"[.,;:!?]", seg.text[:m.start()])[-1]
    for chunk in (after, before):
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9+\-]*", chunk) if w.lower() not in STOP]
        if words:
            phrase = " ".join(words[:5])
            return Need(topic=phrase.title(), query=phrase, kind="illustration",
                        reason=seg.text[:80], priority="elevated", confidence=0.3, path="heuristic")
    return None


def topic_key(t: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", t.lower())
    return {w.rstrip("s") for w in words if w not in STOP and len(w) > 1}


def same_topic(a: str, b: str) -> bool:
    ka, kb = topic_key(a), topic_key(b)
    if not ka or not kb:
        return False
    return len(ka & kb) / min(len(ka), len(kb)) >= 0.6


def _float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
