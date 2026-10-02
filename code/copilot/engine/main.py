"""Part 2 - image engine (Shreevardhan).

Reads Segment messages from part 1, decides when a visual would help, finds images on the web,
and serves Suggestion messages on ws://127.0.0.1:8772/suggestions (+ GET /suggestions/history).
Image files are served from .cache/images at http://127.0.0.1:8772/images/<file>.

    python -m engine.main

Pipeline per suggestion:  detect.py (cue fast path / LLM slow path)  ->  search.py (Serper / DDG /
Wikimedia)  ->  fetch.py (download, filter, dedupe, resize)  ->  publish.
Every decision is appended to .cache/engine_log.jsonl and published on the dev-only stream
ws://127.0.0.1:8772/events (not part of the contract; the engine console reads it).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from pathlib import Path

from aiohttp import web

from contracts.config import ENGINE_PORT, IMAGES_URL, TRANSCRIPT_WS
from contracts.messages import Image, Segment, Suggestion, from_dict
from contracts.stream import StreamServer, Subscriber, run_app

from . import detect, fetch, llm, search

CACHE = Path(__file__).resolve().parent.parent / ".cache"
IMAGES = CACHE / "images"
LOG = CACHE / "engine_log.jsonl"

WINDOW_SECONDS = 30       # speech the LLM sees
CHECK_EVERY = 6           # slow path: periodic check (only runs if new speech arrived)
SETTLE = 1.0              # slow path also runs this long after a final segment arrives...
MIN_GAP = 5.0             # ...but never more often than this (LLM rate limits)
TOPIC_COOLDOWN = 180      # don't re-suggest the same topic for this long
RECENT_TOPICS_SHOWN = 300 # topics from the last 5 min are listed to the LLM as ALREADY SUGGESTED


class Engine:
    def __init__(self, publish, images_dir: Path = IMAGES, log_path: Path | None = LOG,
                 on_event=None):
        self.publish = publish                       # async (Suggestion) -> dict
        self.on_event = on_event                     # (dict) -> None, every log event (dev console)
        self.n_run = 0
        self.images_dir = images_dir
        self.log_path = log_path
        self.segments: "OrderedDict[str, Segment]" = OrderedDict()
        self.arrived: dict[str, float] = {}          # segment id -> monotonic arrival time
        self.checked: set[str] = set()               # segment ids the LLM has already looked at
        self.topics: list[tuple[str, float]] = []    # (topic, monotonic time) reserved / suggested
        self.seen_hashes: list[int] = []             # recent image hashes, to avoid repeats
        self.run = time.strftime("%H%M%S")          # ids/files stay unique across engine restarts
        self.n_sug = 0
        self.n_img = 0
        self.slow_busy = False
        self.last_slow = 0.0
        self.tasks: set[asyncio.Task] = set()
        self.suggested: dict[str, dict] = {}         # suggestion id -> info (for feedback)
        self.pending: dict[str, tuple[detect.Need, float]] = {}  # topic -> being searched right now

    # ----------------------------------------------------------------------------- input
    async def on_segment(self, seg: Segment) -> None:
        if not seg.final or not seg.text.strip():
            return
        old = self.segments.get(seg.id)
        if old and old.text != seg.text:
            self.checked.discard(seg.id)     # revised text (or a restarted source reusing ids): look again
        elif old:
            return                           # exact repeat, e.g. replay after a reconnect
        self.segments[seg.id] = seg
        self.segments.move_to_end(seg.id)
        self.arrived.setdefault(seg.id, time.monotonic())
        m = detect.find_cue(seg.text)
        if m:
            self._spawn(self._fast(seg, m))
        else:
            self._spawn(self._settle())

    async def _settle(self) -> None:
        await asyncio.sleep(SETTLE)
        wait = MIN_GAP - (time.monotonic() - self.last_slow)
        if wait > 0:
            await asyncio.sleep(wait)
        await self.tick()

    def window(self) -> list[Segment]:
        if not self.segments:
            return []
        latest = max(s.end for s in self.segments.values())
        return [s for s in self.segments.values() if s.end >= latest - WINDOW_SECONDS]

    # ----------------------------------------------------------------------------- paths
    async def _fast(self, seg: Segment, m) -> None:
        w = [s for s in self.window() if s.end <= seg.end]
        new = {s.id for s in w if s.id not in self.checked} | {seg.id}
        self.checked |= new
        t0 = time.monotonic()
        run = self._start_run("fast", w, new, m.group(0))
        try:
            need = await asyncio.to_thread(detect.ask, w, new, self.recent_topics(), m.group(0))
        except llm.LLMError as e:
            self.log("llm_error", run=run, path="fast", error=str(e)[:300])
            need = detect.heuristic(seg, m)
        if need:
            need.run = run
        self.log("detect", run=run, path="fast", seg=seg.id, cue=m.group(0),
                 secs=round(time.monotonic() - t0, 2), need=need.__dict__ if need else None)
        if need and not self.reserve(need.topic, check_only=True):
            self.log("skip_duplicate", run=run, topic=need.topic)
            self._upgrade(need.topic)
        elif need:
            await self.suggest(need, [seg.id], t_start=self.arrived.get(seg.id, t0))
        else:
            # LLM said "already suggested": if the slow path is fetching something right now, the
            # cue tells us the host wants it on screen, so make it elevated.
            self._upgrade(None)

    def _upgrade(self, topic: str | None) -> None:
        now = time.monotonic()
        for t, (need, at) in self.pending.items():
            if (topic is None and now - at < 15) or (topic and detect.same_topic(topic, t)):
                if need.priority != "elevated":
                    need.priority = "elevated"
                    self.log("upgrade", run=need.run, topic=t)

    async def tick(self) -> None:
        """Slow path: one LLM look at recent speech, if anything new was said."""
        if self.slow_busy:
            return
        w = self.window()
        new = {s.id for s in w if s.id not in self.checked}
        if not new:
            return
        self.slow_busy = True
        self.last_slow = time.monotonic()
        self.checked |= new
        t0 = time.monotonic()
        run = self._start_run("slow", w, new, None)
        try:
            need = await asyncio.to_thread(detect.ask, w, new, self.recent_topics(), None)
        except llm.LLMError as e:
            self.log("llm_error", run=run, path="slow", error=str(e)[:300])
            return
        finally:
            self.slow_busy = False
        if need:
            need.run = run
        self.log("detect", run=run, path="slow", new=sorted(new), secs=round(time.monotonic() - t0, 2),
                 need=need.__dict__ if need else None)
        if need:
            src = [s.id for s in w if s.id in new][-3:]
            first_new = min((self.arrived.get(i, t0) for i in src), default=t0)
            await self.suggest(need, src, t_start=first_new)

    def _start_run(self, path: str, w: list[Segment], new: set[str], cue: str | None) -> str:
        self.n_run += 1
        run = f"r{self.n_run}"
        self.log("detect_start", run=run, path=path, cue=cue,
                 new_lines=[s.text for s in w if s.id in new],
                 recent_topics=self.recent_topics(),
                 prompt=detect.build_prompt(w, new, self.recent_topics(), cue))
        return run

    # ----------------------------------------------------------------------------- topics
    def recent_topics(self) -> list[str]:
        now = time.monotonic()
        return [t for t, at in self.topics if now - at < RECENT_TOPICS_SHOWN]

    def reserve(self, topic: str, check_only: bool = False) -> bool:
        now = time.monotonic()
        if any(detect.same_topic(topic, t) and now - at < TOPIC_COOLDOWN for t, at in self.topics):
            return False
        if not check_only:
            self.topics.append((topic, now))
        return True

    def release(self, topic: str) -> None:
        self.topics = [(t, at) for t, at in self.topics if t != topic]

    # ----------------------------------------------------------------------------- retrieve
    async def suggest(self, need: detect.Need, source_ids: list[str], t_start: float) -> None:
        if not self.reserve(need.topic):
            self.log("skip_duplicate", run=need.run, topic=need.topic)
            return
        self.n_sug += 1
        sid = f"sug-{self.run}-{self.n_sug:03d}"
        t0 = time.monotonic()
        self.pending[need.topic] = (need, t0)
        try:
            await self._suggest(need, sid, source_ids, t_start, t0)
        finally:
            self.pending.pop(need.topic, None)

    async def _suggest(self, need: detect.Need, sid: str, source_ids: list[str], t_start: float,
                       t0: float) -> None:
        self.log("search_start", run=need.run, sug=sid, query=need.query, kind=need.kind)
        cands, providers = await asyncio.to_thread(search.search_all, need.query, 20)
        t_s = time.monotonic()
        self.log("search", run=need.run, sug=sid, query=need.query, providers=providers,
                 n=len(cands), secs=round(t_s - t0, 2))
        report: list[dict] = []
        picked = await fetch.fetch_best(cands, need.kind, self.images_dir, sid, want=3,
                                        seen_hashes=self.seen_hashes, try_n=14, report=report)
        t_search = time.monotonic() - t0
        self.log("fetch", run=need.run, sug=sid, secs=round(time.monotonic() - t_s, 2),
                 picked=len(picked), candidates=report)
        if not picked:
            self.release(need.topic)
            self.log("no_images", run=need.run, sug=sid, query=need.query, providers=providers)
            return

        images = []
        for p in picked:
            self.n_img += 1
            images.append(Image(id=f"img-{self.run}-{self.n_img:03d}", url=f"{IMAGES_URL}/{p.full.name}",
                                thumb_url=f"{IMAGES_URL}/{p.thumb.name}", width=p.width,
                                height=p.height, source="web", source_ref=p.cand.page or p.cand.url,
                                caption=p.cand.title[:100]))
            self.seen_hashes.append(p.ahash)
        self.seen_hashes = self.seen_hashes[-100:]
        sug = Suggestion(id=sid, topic=need.topic, images=images, reason=need.reason,
                         priority=need.priority, source_segment_ids=source_ids)
        d = await self.publish(sug)
        total = time.monotonic() - t_start
        self.suggested[sid] = {"topic": need.topic, "query": need.query,
                               "images": {i.id: i.source_ref for i in images}}
        self.log("suggest", run=need.run, sug=sid, seq=d.get("seq"), path=need.path, backend=need.backend,
                 topic=need.topic, query=need.query, kind=need.kind, priority=need.priority,
                 providers=providers, n_images=len(images), search_secs=round(t_search, 2),
                 total_secs=round(total, 2), images=[i.source_ref for i in images])
        print(f"#{d.get('seq')} {need.priority:8} {need.topic!r}  q={need.query!r}  "
              f"{len(images)} img via {','.join(providers)}  [{need.path}, {total:.1f}s after speech]")

    # ----------------------------------------------------------------------------- feedback
    def on_feedback(self, d: dict) -> None:
        sid = d.get("suggestion_id")
        info = self.suggested.get(sid, {})
        if d.get("type") == "select":
            self.log("select", sug=sid, image=d.get("image_id"), topic=info.get("topic"),
                     ref=info.get("images", {}).get(d.get("image_id")))
            print(f"<- select {sid} / {d.get('image_id')} ({info.get('topic')})")
        elif d.get("type") == "dismiss":
            if info.get("topic"):     # restart the cooldown: the host doesn't want this topic
                self.release(info["topic"])
                self.topics.append((info["topic"], time.monotonic()))
            self.log("dismiss", sug=sid, topic=info.get("topic"))
            print(f"<- dismiss {sid} ({info.get('topic')})")

    # ----------------------------------------------------------------------------- misc
    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self._done)

    def _done(self, t: asyncio.Task) -> None:
        self.tasks.discard(t)
        if not t.cancelled() and t.exception():
            print(f"[engine] task failed: {t.exception()!r}")

    def log(self, event: str, **kw) -> None:
        kw = {"t": round(time.time(), 2), "event": event, **kw}
        if self.on_event:
            self.on_event(kw)
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(kw, ensure_ascii=False, default=str) + "\n")


async def tick_loop(engine: Engine) -> None:
    while True:
        await asyncio.sleep(CHECK_EVERY)
        try:
            await engine.tick()
        except Exception as e:
            print(f"[engine] tick failed: {e!r}")


def main():
    IMAGES.mkdir(parents=True, exist_ok=True)
    engine: Engine
    stream = StreamServer("suggestions", replay_seconds=120,
                          on_message=lambda d: _async(engine.on_feedback, d))
    events = StreamServer("events", replay_seconds=600)    # dev-only: what the engine is doing
    loop_holder: dict = {}

    def on_event(d: dict) -> None:
        # Engine.log is sync and may run in a worker thread; publish on the event loop, in order.
        loop = loop_holder.get("loop")
        if loop:
            loop.call_soon_threadsafe(lambda: loop.create_task(events.publish(d)))

    engine = Engine(stream.publish, on_event=on_event)

    async def on_segment(d: dict):
        msg = from_dict(d)
        if isinstance(msg, Segment):
            await engine.on_segment(msg)

    app = web.Application()
    stream.attach(app)
    events.attach(app)
    app.router.add_static("/images", IMAGES)

    async def start(_):
        loop_holder["loop"] = asyncio.get_running_loop()
        app["sub"] = asyncio.create_task(Subscriber(TRANSCRIPT_WS, on_segment).run())
        app["tick"] = asyncio.create_task(tick_loop(engine))
        if not llm.backends() or llm.backends() == ["local"]:
            app["warm"] = asyncio.create_task(asyncio.to_thread(llm.warm_local))

    app.on_startup.append(start)
    print(f"reading {TRANSCRIPT_WS}; images at {IMAGES_URL}")
    print(f"llm: {', '.join(llm.backends()) or 'none'}; search: {', '.join(search.provider_order())}")
    run_app(app, ENGINE_PORT)


async def _async(fn, *a):
    fn(*a)


if __name__ == "__main__":
    main()
