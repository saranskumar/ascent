"""Offline tests: no network, no LLM. Run from code/copilot:  python -m pytest -q"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image as PImage, ImageDraw

from contracts.messages import Segment
from engine import detect, fetch, main as em, search
from engine.search import Candidate


# ------------------------------------------------------------------------------------- detect
@pytest.mark.parametrize("text", [
    "So the fix is the circuit breaker pattern. Let me explain how it works.",
    "If you plot it, the retry delay doubles each time.",
    "it's the one that looks like a mechanical keyboard cut in half",
    "picture a disc with lobes that wobbles around inside a ring of pins",
    "As you can see, the threshold is breached.",
    "Here's the architecture we ended up with.",
])
def test_cue_found(text):
    assert detect.find_cue(text)


@pytest.mark.parametrize("text", [
    "Morning everyone, can you all hear me okay?",
    "I'll take them after lunch.",
    "Thanks all, see you tomorrow.",
])
def test_no_cue(text):
    assert not detect.find_cue(text)


def test_heuristic_takes_phrase_after_cue():
    seg = Segment("s", "it's the one that looks like a mechanical keyboard cut in half", 0, 3)
    need = detect.heuristic(seg, detect.find_cue(seg.text))
    assert need and need.query == "mechanical keyboard cut half" and need.priority == "elevated"


def test_same_topic():
    assert detect.same_topic("Circuit Breaker Pattern", "circuit breaker pattern diagram")
    assert detect.same_topic("Split Keyboard", "Ergonomic Split Keyboards")
    assert not detect.same_topic("Circuit Breaker Pattern", "Exponential Backoff With Jitter")


# ------------------------------------------------------------------------------------- fetch
def test_size_filter():
    assert not fetch.size_ok(1500, 300, "photo")        # banner
    assert not fetch.size_ok(300, 300, "photo")         # icon-sized
    assert fetch.size_ok(450, 406, "diagram")           # small but readable diagram
    assert fetch.size_ok(0, 0, "photo")                 # unknown until downloaded


def test_stock_sites_blocked():
    assert fetch.blocked(Candidate(url="https://www.shutterstock.com/x.jpg"))
    assert not fetch.blocked(Candidate(url="https://martinfowler.com/x.png"))


def _png(color, shape=True) -> bytes:
    im = PImage.new("RGB", (800, 600), "white")
    if shape:
        ImageDraw.Draw(im).ellipse([100, 100, 500, 500], fill=color)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def test_process_rejects_blank_and_garbage():
    assert isinstance(fetch._process(_png("red"), "photo"), tuple)
    assert fetch._process(_png("red", shape=False), "photo") == "blank"
    assert fetch._process(b"<html>not an image</html>", "photo") == "not a readable image"


# ------------------------------------------------------------------------------------- engine
class FakeWorld:
    """Stand-ins for the LLM, search and download steps."""

    def __init__(self, monkeypatch, tmp_path: Path, answers: dict[str, str]):
        self.answers = answers           # substring of the newest line -> topic
        self.published = []
        monkeypatch.setattr(detect, "ask", self.ask)
        monkeypatch.setattr(search, "search_all", lambda q, n=20, quick=False: ([Candidate(url=f"u/{q}")], ["fake:1"]))
        monkeypatch.setattr(fetch, "fetch_best", self.fetch_best)
        monkeypatch.setattr(em, "SETTLE", 0.01)
        monkeypatch.setattr(em, "MIN_GAP", 0.0)
        self.tmp = tmp_path

    def ask(self, window, new_ids, recent, cue):
        newest = [s for s in window if s.id in new_ids][-1].text
        for key, topic in self.answers.items():
            if key in newest and topic not in recent:
                return detect.Need(topic, topic.lower(), "diagram", key, "elevated" if cue else "ambient",
                                   0.9, "fast" if cue else "slow")
        return None

    async def fetch_best(self, cands, kind, out_dir, name, **kw):
        f = self.tmp / f"{name}.jpg"
        f.write_bytes(b"x")
        return [fetch.Fetched(cands[0], f, f, 800, 600, hash(name))]

    async def publish(self, sug):
        self.published.append(sug)
        return {"seq": len(self.published)}


async def _feed(eng: em.Engine, texts: list[str]):
    for i, t in enumerate(texts):
        await eng.on_segment(Segment(f"s{i}", t, i * 5.0, i * 5.0 + 4))
        await asyncio.sleep(0.05)
    for _ in range(50):
        await asyncio.sleep(0.02)
        if not eng.tasks and not eng.slow_busy:
            break


def test_engine_fast_and_slow_paths(monkeypatch, tmp_path):
    w = FakeWorld(monkeypatch, tmp_path, {"circuit breaker": "Circuit Breaker", "keyboard": "Split Keyboard"})
    eng = em.Engine(w.publish, images_dir=tmp_path, log_path=None)
    asyncio.run(_feed(eng, [
        "the fix is the circuit breaker pattern, let me explain",   # cue -> fast, elevated
        "anyone tried the split keyboard?",                         # no cue -> slow, ambient
        "Thanks all, see you tomorrow.",
    ]))
    got = {s.topic: s.priority for s in w.published}
    assert got == {"Circuit Breaker": "elevated", "Split Keyboard": "ambient"}
    assert all(i.url.startswith("http://") for s in w.published for i in s.images)


def test_engine_does_not_repeat_topic(monkeypatch, tmp_path):
    w = FakeWorld(monkeypatch, tmp_path, {"circuit breaker": "Circuit Breaker"})
    monkeypatch.setattr(detect, "ask", lambda window, new_ids, recent, cue: detect.Need(
        "Circuit Breaker", "cb", "diagram", "", "ambient", 0.9, "slow"))   # LLM ignores ALREADY SUGGESTED
    eng = em.Engine(w.publish, images_dir=tmp_path, log_path=None)
    asyncio.run(_feed(eng, ["circuit breaker one", "circuit breaker two", "circuit breaker three"]))
    assert len(w.published) == 1


def test_dismiss_keeps_topic_blocked(monkeypatch, tmp_path):
    w = FakeWorld(monkeypatch, tmp_path, {"keyboard": "Split Keyboard"})
    eng = em.Engine(w.publish, images_dir=tmp_path, log_path=None)
    asyncio.run(_feed(eng, ["anyone tried the split keyboard?"]))
    sid = w.published[0].id
    eng.on_feedback({"type": "dismiss", "suggestion_id": sid})
    assert not eng.reserve("split keyboards", check_only=True)


def test_revised_segment_is_checked_again(monkeypatch, tmp_path):
    w = FakeWorld(monkeypatch, tmp_path, {"keyboard": "Split Keyboard"})
    eng = em.Engine(w.publish, images_dir=tmp_path, log_path=None)

    async def go():
        await _feed(eng, ["hello there"])                                       # id s0, nothing to show
        await eng.on_segment(Segment("s0", "anyone tried the split keyboard?", 0, 4))  # same id, new text
        await _feed(eng, [])
    asyncio.run(go())
    assert [s.topic for s in w.published] == ["Split Keyboard"]
