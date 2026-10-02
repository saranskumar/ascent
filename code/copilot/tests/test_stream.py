"""contracts/stream.py: seq numbers, replay with ?since, messages back from the client."""
from __future__ import annotations

import asyncio
import socket

from aiohttp import web

from contracts.messages import Segment, Select
from contracts.stream import StreamServer, Subscriber


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def _scenario():
    back = []
    stream = StreamServer("transcript", on_message=lambda d: _append(back, d))
    app = web.Application()
    stream.attach(app)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    url = f"ws://127.0.0.1:{port}/transcript"
    try:
        for i in range(3):
            await stream.publish(Segment(f"s{i}", f"text {i}", i, i + 1))

        got = []
        sub = Subscriber(url, lambda d: _append(got, d), since=1)   # missed seq 2 and 3
        task = asyncio.create_task(sub.run())
        await _until(lambda: len(got) == 2)
        await stream.publish(Segment("s3", "live", 3, 4))
        await _until(lambda: len(got) == 3)
        assert [d["seq"] for d in got] == [2, 3, 4]
        assert sub.last_seq == 4

        assert await sub.send(Select("sug-1", "img-1"))
        await _until(lambda: back)
        assert back[0] == {"suggestion_id": "sug-1", "image_id": "img-1", "type": "select"}
        task.cancel()
    finally:
        await runner.cleanup()


async def _append(lst, d):
    lst.append(d)


async def _until(cond, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


def test_replay_since_and_send_back():
    asyncio.run(_scenario())
