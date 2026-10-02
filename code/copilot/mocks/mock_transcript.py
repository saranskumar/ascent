"""Stand-in for part 1: replays a scripted transcript on ws /transcript at real speed.

    python -m mocks.mock_transcript                  # contracts/samples/transcript.jsonl
    python -m mocks.mock_transcript --speed 4 --loop
    python -m mocks.mock_transcript --file my_recording.jsonl

Each segment is sent as a partial (first half of the words) halfway through, then as a final
at its end time, so consumers see revisions like a real recogniser would produce.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from aiohttp import web

from contracts.config import TRANSCRIPT_PORT
from contracts.messages import Segment
from contracts.stream import StreamServer, run_app

SAMPLE = Path(__file__).resolve().parent.parent / "contracts" / "samples" / "transcript.jsonl"


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


async def replay(stream: StreamServer, segs: list[dict], speed: float, loop: bool, partials: bool):
    await asyncio.sleep(1)  # give subscribers a moment to connect
    offset, n = 0.0, 0
    while True:
        clock = 0.0
        for s in segs:
            sid = s["id"] if n == 0 else f"{s['id']}-r{n}"
            start, end = s["start"] + offset, s["end"] + offset
            words = s["text"].split()
            if partials and len(words) > 3:
                mid = (s["start"] + s["end"]) / 2
                await asyncio.sleep(max(0, mid - clock) / speed)
                clock = mid
                await stream.publish(Segment(sid, " ".join(words[: len(words) // 2]), start, end,
                                             final=False, speaker=s.get("speaker")))
            await asyncio.sleep(max(0, s["end"] - clock) / speed)
            clock = s["end"]
            d = await stream.publish(Segment(sid, s["text"], start, end, final=True,
                                             speaker=s.get("speaker")))
            print(f"#{d['seq']:<4} [{start:6.1f}] {s['text']}")
        if not loop:
            print("transcript finished (server stays up; use --loop to repeat)")
            return
        offset += segs[-1]["end"] + 3
        n += 1
        await asyncio.sleep(3 / speed)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=SAMPLE)
    ap.add_argument("--speed", type=float, default=1.0, help="2 = twice as fast")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--no-partials", action="store_true")
    ap.add_argument("--port", type=int, default=TRANSCRIPT_PORT)
    a = ap.parse_args()

    stream = StreamServer("transcript")
    app = web.Application()
    stream.attach(app)

    async def start(_):
        app["replay"] = asyncio.create_task(
            replay(stream, load(a.file), a.speed, a.loop, not a.no_partials))

    app.on_startup.append(start)
    run_app(app, a.port)


if __name__ == "__main__":
    main()
