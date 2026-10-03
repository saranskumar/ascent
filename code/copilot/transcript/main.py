"""Part 1 - Live transcript service (Hari).

Serves Segment messages on ws://127.0.0.1:8771/transcript (+ GET /transcript/history).
The only source is Meetily Pro's own live transcript (its Parakeet model, mic + system audio):
it follows the transcripts.json that Meetily rewrites in the meeting folder while recording (no debug
port, no restart, no API call). No microphone, no second transcription: if Meetily isn't recording,
nothing is published.

Run:
    python -m transcript.main [--dir <Meetily recordings folder>]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

# Suppress Hugging Face symlink warnings on Windows
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import aiohttp
from aiohttp import web
from contracts.config import TRANSCRIPT_PORT
from contracts.messages import Segment
from contracts.stream import StreamServer, run_app

stream = StreamServer("transcript")

T0 = time.monotonic()
RUN = time.strftime("%H%M%S")   # ids stay unique if this process restarts
_n = 0


async def say(text: str) -> dict:
    global _n
    _n += 1
    now = time.monotonic() - T0
    return await stream.publish(Segment(id=f"seg-{RUN}-{_n:03d}", text=text.strip(), start=max(0, now - 3),
                                        end=now, final=True, speaker="host"))


async def say_handler(request: web.Request) -> web.Response:
    text = str((await request.json()).get("text", "")).strip()
    if not text:
        return web.json_response({"ok": False, "error": "empty"}, status=400)
    d = await say(text)
    return web.json_response({"ok": True, "seq": d["seq"]})


SPEAKERS = {"mic": "host", "system": "guest"}      # Meetily labels the two audio channels


def find_recordings_dir(explicit: str | None = None) -> str | None:
    """Where Meetily saves recordings: --dir, $MEETILY_RECORDINGS_DIR, the default
    ~/Music/meetily-recordings, or the folder named in Meetily's own log."""
    import re
    cands = [explicit, os.environ.get("MEETILY_RECORDINGS_DIR"),
             os.path.join(os.path.expanduser("~"), "Music", "meetily-recordings")]
    for c in cands:
        if c and os.path.isdir(c):
            return c
    log = os.path.join(os.environ.get("APPDATA", ""), "pro.meetily.ai", "logs", "Meetily Pro.log")
    try:
        with open(log, encoding="utf-8", errors="replace") as f:
            hits = re.findall(r"meeting folder[^:]*: (.+?)[\\/]Meeting [^\\/\r\n]+", f.read()[-2_000_000:])
        if hits and os.path.isdir(hits[-1]):
            return hits[-1]
    except OSError:
        pass
    return None


class RecordingsTail:
    """Follows Meetily's live transcript on disk.

    While recording, Meetily rewrites `<recordings>/<meeting>/transcripts.json` every time a segment is
    finalised (seen in its log: "wrote transcripts.json with 14 / 15 / 16 segments"). Reading that
    file gives Meetily's own transcript (Parakeet, mic + system audio) live, with no debug port, no
    restart and no API call. Meetings that existed before we started are skipped unless
    `catch_up` says one is recording right now."""

    def __init__(self, root: str, catch_up: bool = False):
        self.root = root
        self.sent: dict[str, int] = {}        # folder -> highest sequence_id already published
        self.mtime: dict[str, float] = {}
        for name, doc in self._docs(force=True):
            segs = doc.get("segments") or []
            if catch_up and name == self._newest():
                self.sent[name] = -1           # recording right now: publish what it already has
                self.mtime.pop(name, None)     # ...so the first poll must read it
            else:
                self.sent[name] = max((int(x.get("sequence_id", i)) for i, x in enumerate(segs)), default=-1)

    def _newest(self) -> str | None:
        try:
            names = [n for n in os.listdir(self.root) if os.path.isfile(os.path.join(self.root, n, "transcripts.json"))]
        except OSError:
            return None
        return max(names, key=lambda n: os.path.getmtime(os.path.join(self.root, n, "transcripts.json")), default=None)

    def _docs(self, force: bool = False):
        try:
            names = os.listdir(self.root)
        except OSError:
            return
        for name in names:
            f = os.path.join(self.root, name, "transcripts.json")
            try:
                m = os.path.getmtime(f)
                if not force and self.mtime.get(name) == m:
                    continue
                with open(f, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError):
                continue                       # not there yet, or caught mid-write: next poll
            self.mtime[name] = m
            yield name, doc

    def poll(self) -> list[tuple[str, dict]]:
        """New (meeting_folder, segment) pairs since the last call, oldest first."""
        out = []
        for name, doc in self._docs():
            last = self.sent.get(name, -1)
            for i, seg in enumerate(doc.get("segments") or []):
                seq = int(seg.get("sequence_id", i))
                if seq > last and (seg.get("text") or "").strip():
                    out.append((name, seg))
                    last = max(last, seq)
            self.sent[name] = last
        return out


def meetily_is_recording() -> bool:
    """Meetily's read-only loopback API: is a recording in progress?"""
    import urllib.request
    try:
        tok = open(os.path.join(os.environ["APPDATA"], "pro.meetily.ai", "gateway-token"), encoding="utf-8").read().strip()
        req = urllib.request.Request("http://127.0.0.1:8420/v1/recording", headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode()).get("state") in ("recording", "paused")
    except Exception:
        return False


async def meetily_file_source(root: str | None = None):
    """Publishes Meetily Pro's own live transcript by following its transcripts.json."""
    while not (root_dir := find_recordings_dir(root)):
        print("Can't find Meetily's recordings folder; waiting. Use --dir <folder> or set MEETILY_RECORDINGS_DIR.")
        await asyncio.sleep(5)
    print(f"Following Meetily Pro's live transcript in {root_dir}")
    loop = asyncio.get_running_loop()
    tail = RecordingsTail(root_dir, catch_up=await loop.run_in_executor(None, meetily_is_recording))
    print("Ready. Start (or continue) a recording in Meetily Pro; its segments appear here as they are finalised.")
    while True:
        for folder, u in tail.poll():
            seq = int(u.get("sequence_id", 0))
            tag = "".join(ch for ch in folder.split("_2026")[0] if ch.isdigit())[-8:] or "m"
            start = float(u.get("audio_start_time", 0.0))
            end = float(u.get("audio_end_time", start + float(u.get("duration", 0.0))))
            who = u.get("speaker") or "mic"
            d = await stream.publish(Segment(id=f"seg-{tag}-{seq:04d}", text=u["text"].strip(), start=round(start, 2),
                                             end=round(end, 2), final=True, speaker=SPEAKERS.get(who, who)))
            print(f"  [#{d['seq']}] [{start:6.1f}s - {end:6.1f}s] ({who}) {u['text'].strip()}")
        await asyncio.sleep(0.25)


def main():
    parser = argparse.ArgumentParser(description="Part 1: Live Transcript Service (Meetily Pro's own transcript)")
    parser.add_argument("--dir", help="Meetily's recordings folder (default: ~/Music/meetily-recordings)")
    args, _ = parser.parse_known_args()

    app = web.Application()
    stream.attach(app)
    app.router.add_post("/transcript/say", say_handler)

    async def start(_):
        app["source"] = asyncio.create_task(meetily_file_source(args.dir))

    app.on_startup.append(start)
    run_app(app, TRANSCRIPT_PORT)


if __name__ == "__main__":
    main()
