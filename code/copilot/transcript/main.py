"""Part 1 - live transcript service (Hari).

Serves Segment messages on ws://127.0.0.1:8771/transcript (+ GET /transcript/history).
Everything below the line is yours to replace; keep the publish() calls and the Segment shape.

Right now it runs in "typing mode": every line you type becomes a final segment. That is already
useful for the engine (type "let me explain the circuit breaker pattern" and watch what happens).

    python -m transcript.main
"""
from __future__ import annotations

import asyncio
import sys
import time

from aiohttp import web

from contracts.config import TRANSCRIPT_PORT
from contracts.messages import Segment
from contracts.stream import StreamServer, run_app

stream = StreamServer("transcript")


# ---------------------------------------------------------------------------------------------
# TODO(Hari): replace typing mode with the real source (see docs/tech/copilot-live-transcript.md):
#   - faster-whisper on 3-5 s chunks of mic + system audio, or
#   - Meetily (patched Community build / webview), or anything else.
# Rules from contracts/README.md:
#   - stable `id` per segment; resend the same id when its text changes (partial -> final)
#   - `start`/`end` in seconds from the start of the meeting
#   - aim for < 5 s from speech to the final segment
# ---------------------------------------------------------------------------------------------
async def typing_source():
    t0 = time.monotonic()
    n = 0
    loop = asyncio.get_running_loop()
    print("typing mode: each line you enter is sent as a final segment")
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:            # stdin closed
            return
        if not line.strip():
            continue
        n += 1
        now = time.monotonic() - t0
        d = await stream.publish(Segment(id=f"seg-{n:04d}", text=line.strip(), start=max(0, now - 3),
                                         end=now, final=True, speaker="host"))
        print(f"  sent #{d['seq']}")


def main():
    app = web.Application()
    stream.attach(app)

    async def start(_):
        app["source"] = asyncio.create_task(typing_source())

    app.on_startup.append(start)
    run_app(app, TRANSCRIPT_PORT)


if __name__ == "__main__":
    main()
