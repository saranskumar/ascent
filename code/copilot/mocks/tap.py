"""Print any co-pilot stream, to check a part's output without the next part.

    python -m mocks.tap transcript                 # what part 1 is sending
    python -m mocks.tap suggestions                # what part 2 is sending
    python -m mocks.tap suggestions --auto-select  # also act as a fake GUI: select image 1 of each
    python -m mocks.tap transcript --since 0       # replay everything from the start
"""
from __future__ import annotations

import argparse
import asyncio

from contracts.config import SUGGESTIONS_WS, TRANSCRIPT_WS
from contracts.messages import Select
from contracts.stream import Subscriber

STREAMS = {"transcript": TRANSCRIPT_WS, "suggestions": SUGGESTIONS_WS}


async def main_async(a):
    sub: Subscriber

    async def show(d: dict):
        t, seq = d.get("type"), d.get("seq")
        if t == "segment":
            flag = "" if d.get("final") else " (partial)"
            print(f"#{seq:<4} [{d.get('start', 0):6.1f}] {d.get('speaker') or '?'}: {d.get('text')}{flag}")
        elif t == "suggestion":
            print(f"#{seq:<4} {d.get('priority'):8} {d.get('topic')}  - {d.get('reason')}")
            for im in d.get("images", []):
                print(f"         {im.get('id')}  {im.get('caption')!r}  {im.get('url')}")
            if a.auto_select and d.get("images"):
                img = d["images"][0]["id"]
                ok = await sub.send(Select(d["id"], img))
                print(f"      -> select {img} {'sent' if ok else 'FAILED'}")
        else:
            print(f"#{seq} {d}")

    sub = Subscriber(STREAMS[a.stream], show, since=a.since)
    await sub.run()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stream", choices=STREAMS)
    ap.add_argument("--since", type=int, default=None, help="replay from this seq (0 = everything)")
    ap.add_argument("--auto-select", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(main_async(a))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
