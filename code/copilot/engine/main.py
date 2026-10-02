"""Part 2 - image engine (Saran).

Reads Segment messages from part 1, decides when a visual would help, finds images, and serves
Suggestion messages on ws://127.0.0.1:8772/suggestions (+ GET /suggestions/history).
Image files are served from .cache/images at http://127.0.0.1:8772/images/<file>.
Select / dismiss from the GUI arrive in on_feedback().

    python -m engine.main

The wiring works end to end already; the three TODO functions are the actual engine.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from pathlib import Path

from aiohttp import web

from contracts.config import ENGINE_PORT, IMAGES_URL, TRANSCRIPT_WS
from contracts.messages import Image, Segment, Suggestion, from_dict
from contracts.stream import StreamServer, Subscriber, run_app

IMAGES = Path(__file__).resolve().parent.parent / ".cache" / "images"
WINDOW_SECONDS = 60       # how much recent speech to reason over
CHECK_EVERY = 8           # seconds between "does this need a visual?" checks
TOPIC_COOLDOWN = 120      # don't re-suggest a topic for this long

segments: "OrderedDict[str, Segment]" = OrderedDict()   # id -> latest final version
recent_topics: dict[str, float] = {}                     # topic -> last suggested / dismissed
n_suggestions = 0


# ---------------------------------------------------------------------------------------------
# TODO(Saran): the engine.
# ---------------------------------------------------------------------------------------------
def detect(window: list[Segment]) -> dict | None:
    """Recent speech -> {"topic", "query", "kind", "reason", "priority"} or None.
    Plan: cue phrases for a fast path, LLM (Gemini) every CHECK_EVERY s for the rest."""
    return None


def retrieve(need: dict) -> list[Image]:
    """Find 2-3 images for the need. Local folder (CLIP index) first, opt-in web search second.
    Save files into IMAGES and point Image.url / thumb_url at f"{IMAGES_URL}/<file>"."""
    return []


def on_select(suggestion_id: str, image_id: str) -> None:
    """Host used an image: good signal for ranking later."""


# ---------------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------------
stream: StreamServer


async def on_segment(d: dict):
    msg = from_dict(d)
    if isinstance(msg, Segment) and msg.final and msg.text.strip():
        segments[msg.id] = msg
        segments.move_to_end(msg.id)


def window() -> list[Segment]:
    if not segments:
        return []
    latest = max(s.end for s in segments.values())
    return [s for s in segments.values() if s.end >= latest - WINDOW_SECONDS]


async def check_loop():
    global n_suggestions
    while True:
        await asyncio.sleep(CHECK_EVERY)
        w = window()
        if not w:
            continue
        need = await asyncio.to_thread(detect, w)
        if not need or time.monotonic() - recent_topics.get(need["topic"], -1e9) < TOPIC_COOLDOWN:
            continue
        images = await asyncio.to_thread(retrieve, need)
        if not images:
            continue
        n_suggestions += 1
        recent_topics[need["topic"]] = time.monotonic()
        d = await stream.publish(Suggestion(
            id=f"sug-{n_suggestions:04d}", topic=need["topic"], images=images,
            reason=need.get("reason", ""), priority=need.get("priority", "ambient"),
            source_segment_ids=[s.id for s in w[-3:]]))
        print(f"#{d['seq']} suggested {need['topic']!r} ({len(images)} images)")


async def on_feedback(d: dict):
    topic = next((m["topic"] for _, m in stream.history if m["id"] == d.get("suggestion_id")), None)
    if d.get("type") == "select":
        on_select(d["suggestion_id"], d["image_id"])
        print(f"<- select {d['suggestion_id']} / {d['image_id']} ({topic})")
    elif d.get("type") == "dismiss":
        if topic:
            recent_topics[topic] = time.monotonic()   # restart its cooldown
        print(f"<- dismiss {d['suggestion_id']} ({topic})")


def main():
    global stream
    IMAGES.mkdir(parents=True, exist_ok=True)
    stream = StreamServer("suggestions", replay_seconds=120, on_message=on_feedback)
    app = web.Application()
    stream.attach(app)
    app.router.add_static("/images", IMAGES)

    async def start(_):
        app["sub"] = asyncio.create_task(Subscriber(TRANSCRIPT_WS, on_segment).run())
        app["check"] = asyncio.create_task(check_loop())

    app.on_startup.append(start)
    print(f"reading {TRANSCRIPT_WS}; images at {IMAGES_URL}")
    run_app(app, ENGINE_PORT)


if __name__ == "__main__":
    main()
