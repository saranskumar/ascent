"""Shared plumbing so nobody re-implements seq numbers, replay and reconnects.

Server side (transcript and engine):

    stream = StreamServer("transcript")       # ws /transcript, GET /transcript/history
    app = web.Application(); stream.attach(app)
    await stream.publish(Segment(...))        # assigns seq, stores, sends to every client

Client side (engine reading transcript, Python GUI reading suggestions, tap):

    sub = Subscriber(TRANSCRIPT_WS, on_message)
    await sub.run()                           # reconnects forever, resumes with ?since=<last seq>
    await sub.send(Select(...))               # only for streams that accept messages back

Replay rules: `?since=N` sends every stored message with seq > N, then live ones.
No `since` sends the last `replay_seconds` (default 60 s), then live ones.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable

import aiohttp
from aiohttp import web

from .messages import dumps, to_dict

OnMessage = Callable[[dict], Awaitable[None]]


class StreamServer:
    def __init__(self, name: str, replay_seconds: float = 60,
                 on_message: OnMessage | None = None):
        self.name = name
        self.replay_seconds = replay_seconds
        self.on_message = on_message          # called with messages clients send back
        self.history: list[tuple[float, dict]] = []   # (monotonic time, message)
        self.seq = 0
        self._clients: set[asyncio.Queue] = set()

    def attach(self, app: web.Application) -> None:
        app.router.add_get(f"/{self.name}", self._ws)
        app.router.add_get(f"/{self.name}/history", self._history)

    async def publish(self, msg) -> dict:
        d = to_dict(msg)
        self.seq += 1
        d["seq"] = self.seq
        self.history.append((time.monotonic(), d))
        for q in self._clients:
            q.put_nowait(d)
        return d

    def since(self, since: int | None) -> list[dict]:
        if since is None:
            cutoff = time.monotonic() - self.replay_seconds
            return [d for t, d in self.history if t >= cutoff]
        return [d for _, d in self.history if d["seq"] > since]

    async def _history(self, request: web.Request) -> web.Response:
        msgs = self.since(_int(request.query.get("since"), default=0))
        return web.json_response(msgs, headers={"Access-Control-Allow-Origin": "*"})

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=10)
        await ws.prepare(request)
        # Queue the replay and register in one step (no await between), so no message is
        # lost or reordered between "history" and "live".
        q: asyncio.Queue = asyncio.Queue()
        for d in self.since(_int(request.query.get("since"))):
            q.put_nowait(d)
        self._clients.add(q)

        async def writer():
            while True:
                await ws.send_str(json.dumps(await q.get(), ensure_ascii=False))

        w = asyncio.create_task(writer())
        try:
            async for m in ws:
                if m.type == aiohttp.WSMsgType.TEXT and self.on_message:
                    try:
                        await self.on_message(json.loads(m.data))
                    except Exception as e:      # a bad client message must not kill the stream
                        print(f"[{self.name}] bad incoming message {m.data!r}: {e}")
        finally:
            self._clients.discard(q)
            w.cancel()
        return ws


class Subscriber:
    def __init__(self, url: str, on_message: OnMessage, since: int | None = None,
                 retry_seconds: float = 1.0):
        self.url = url
        self.on_message = on_message
        self.last_seq = since
        self.retry_seconds = retry_seconds
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    async def run(self) -> None:
        warned = False
        async with aiohttp.ClientSession() as s:
            while True:
                url = self.url if self.last_seq is None else f"{self.url}?since={self.last_seq}"
                try:
                    async with s.ws_connect(url, heartbeat=10) as ws:
                        self._ws, warned = ws, False
                        print(f"[sub] connected {url}")
                        async for m in ws:
                            if m.type != aiohttp.WSMsgType.TEXT:
                                continue
                            d = json.loads(m.data)
                            self.last_seq = d.get("seq", self.last_seq)
                            await self.on_message(d)
                except aiohttp.ClientError as e:
                    if not warned:
                        print(f"[sub] {self.url} unavailable ({e.__class__.__name__}), retrying...")
                        warned = True
                finally:
                    self._ws = None
                await asyncio.sleep(self.retry_seconds)

    async def send(self, msg) -> bool:
        if self._ws is None or self._ws.closed:
            return False
        await self._ws.send_str(dumps(msg))
        return True


def run_app(app: web.Application, port: int, host: str = "127.0.0.1") -> None:
    print(f"serving on http://{host}:{port}")
    web.run_app(app, host=host, port=port, print=None)


def _int(v: str | None, default: int | None = None) -> int | None:
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default
