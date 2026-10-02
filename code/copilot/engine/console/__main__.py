"""Engine console: one page to run and watch the co-pilot engine (dev tool, not the host GUI).

    python -m engine.console                     # starts transcript (typing) + engine, opens the page
    python -m engine.console --transcript mock   # scripted talk instead of typing
    python -m engine.console --transcript none   # use a transcript source you started yourself

Page: http://127.0.0.1:8770
  - live transcript (ws :8771/transcript), with a box to type lines in typing mode
  - engine runs (ws :8772/events): trigger -> LLM decision -> search query -> every candidate image
    and why it was kept or dropped -> the suggestion
  - suggestions exactly as the GUI receives them (ws :8772/suggestions)
  - start / stop / restart the transcript source and the engine, with their output
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from collections import deque
from pathlib import Path

import aiohttp
from aiohttp import web

from contracts.config import ENGINE_PORT, HOST, SUGGESTIONS_WS, TRANSCRIPT_PORT, TRANSCRIPT_WS

ROOT = Path(__file__).resolve().parents[2]          # code/copilot
PAGE = Path(__file__).resolve().parent / "index.html"
CONSOLE_PORT = int(os.environ.get("COPILOT_CONSOLE_PORT", 8770))
PY = sys.executable

VARIANTS = {
    "transcript": {
        "typing": lambda o: [PY, "-m", "transcript.main"],
        "mock": lambda o: [PY, "-m", "mocks.mock_transcript", "--loop", "--speed", str(o.get("speed") or 1)],
    },
    "engine": {
        "real": lambda o: [PY, "-m", "engine.main"],
        "mock": lambda o: [PY, "-m", "mocks.mock_engine", "--loop"],
    },
}
PORTS = {"transcript": TRANSCRIPT_PORT, "engine": ENGINE_PORT}


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((HOST, port)) == 0


class Part:
    def __init__(self, name: str, hub: "Hub"):
        self.name, self.hub = name, hub
        self.proc: asyncio.subprocess.Process | None = None
        self.variant = ""
        self.opts: dict = {}
        self.state = "stopped"            # stopped | starting | running | exited | error
        self.detail = ""
        self.lines: deque[str] = deque(maxlen=400)

    def info(self) -> dict:
        return {"variant": self.variant, "opts": self.opts, "state": self.state, "detail": self.detail,
                "pid": self.proc.pid if self.proc and self.proc.returncode is None else None}

    async def start(self, variant: str, opts: dict) -> None:
        await self.stop()
        if variant not in VARIANTS[self.name]:
            raise ValueError(f"unknown {self.name} variant {variant!r}")
        port = PORTS[self.name]
        for _ in range(20):                       # a just-killed process may hold the port briefly
            if not port_busy(port):
                break
            await asyncio.sleep(0.2)
        else:
            self._set("error", f"port {port} is in use by another program; stop it first")
            return
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        for k in ("COPILOT_LLM", "COPILOT_SEARCH"):
            env.pop(k, None)
            if opts.get(k):
                env[k] = opts[k]
        self.variant, self.opts = variant, opts
        argv = VARIANTS[self.name][variant](opts)
        self._log(f"$ {' '.join(argv[1:])}" + "".join(f"  [{k}={v}]" for k, v in opts.items() if v))
        self.proc = await asyncio.create_subprocess_exec(
            *argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        self._set("starting", f"pid {self.proc.pid}")
        asyncio.create_task(self._pump(self.proc))
        asyncio.create_task(self._wait_ready(self.proc, port))

    async def _wait_ready(self, proc, port: int) -> None:
        for _ in range(50):
            if proc.returncode is not None or proc is not self.proc:
                return
            if port_busy(port):
                self._set("running", f"pid {proc.pid}, port {port}")
                return
            await asyncio.sleep(0.2)

    async def _pump(self, proc) -> None:
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if line and "automatic function calling" not in line.lower():
                self._log(line)
        code = await proc.wait()
        if proc is self.proc and self.state != "stopped":
            self._set("exited", f"exit code {code}")

    async def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc and proc.returncode is None:
            if os.name == "nt":                   # kill the whole tree
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            else:
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill()
            self._log("(stopped)")
        self._set("stopped", "")

    def _set(self, state: str, detail: str) -> None:
        self.state, self.detail = state, detail
        self.hub.broadcast({"type": "status", "parts": self.hub.status()})

    def _log(self, line: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {line}"
        self.lines.append(line)
        self.hub.broadcast({"type": "log", "part": self.name, "line": line})


class Hub:
    def __init__(self):
        self.clients: set[web.WebSocketResponse] = set()
        self.parts = {n: Part(n, self) for n in VARIANTS}

    def status(self) -> dict:
        return {n: p.info() for n, p in self.parts.items()}

    def broadcast(self, msg: dict) -> None:
        data = json.dumps(msg, ensure_ascii=False)
        for ws in list(self.clients):
            if not ws.closed:
                asyncio.create_task(ws.send_str(data))


hub = Hub()
routes = web.RouteTableDef()


@routes.get("/")
async def index(_):
    return web.FileResponse(PAGE, headers={"Cache-Control": "no-store"})


@routes.get("/api/config")
async def config(_):
    return web.json_response({"transcript_ws": TRANSCRIPT_WS, "suggestions_ws": SUGGESTIONS_WS,
                              "events_ws": f"ws://{HOST}:{ENGINE_PORT}/events",
                              "variants": {k: list(v) for k, v in VARIANTS.items()}})


@routes.post("/api/start")
async def api_start(req):
    d = await req.json()
    try:
        await hub.parts[d["part"]].start(d.get("variant") or next(iter(VARIANTS[d["part"]])),
                                         d.get("opts") or {})
    except (KeyError, ValueError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    return web.json_response({"ok": True})


@routes.post("/api/stop")
async def api_stop(req):
    d = await req.json()
    await hub.parts[d["part"]].stop()
    return web.json_response({"ok": True})


@routes.post("/api/say")
async def api_say(req):
    """Typing box -> transcript.main's /transcript/say (typing mode only)."""
    text = (await req.json()).get("text", "").strip()
    if not text:
        return web.json_response({"ok": False, "error": "empty"}, status=400)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s:
            async with s.post(f"http://{HOST}:{TRANSCRIPT_PORT}/transcript/say", json={"text": text}) as r:
                if r.status == 200:
                    return web.json_response({"ok": True})
                err = ("the transcript source doesn't accept typed lines (only typing mode does)"
                       if r.status in (404, 405) else f"HTTP {r.status}")
    except aiohttp.ClientError:
        err = "no transcript source is running"
    return web.json_response({"ok": False, "error": err}, status=409)


@routes.get("/ws")
async def ws_handler(req):
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(req)
    hub.clients.add(ws)
    await ws.send_str(json.dumps({"type": "status", "parts": hub.status()}))
    for name, p in hub.parts.items():
        await ws.send_str(json.dumps({"type": "logs", "part": name, "lines": list(p.lines)}))
    try:
        async for _ in ws:
            pass
    finally:
        hub.clients.discard(ws)
    return ws


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript", choices=["typing", "mock", "none"], default="typing")
    ap.add_argument("--speed", type=float, default=1.0, help="mock transcript speed")
    ap.add_argument("--engine", choices=["real", "mock", "none"], default="real")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    a = ap.parse_args()

    app = web.Application()
    app.add_routes(routes)

    async def startup(_):
        if a.transcript != "none":
            await hub.parts["transcript"].start(a.transcript, {"speed": a.speed})
        if a.engine != "none":
            await hub.parts["engine"].start(a.engine, {})
        if not a.no_open:
            webbrowser.open(f"http://{HOST}:{CONSOLE_PORT}")

    async def cleanup(_):
        for p in hub.parts.values():
            await p.stop()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    print(f"engine console: http://{HOST}:{CONSOLE_PORT}  (Ctrl+C stops everything it started)")
    web.run_app(app, host=HOST, port=CONSOLE_PORT, print=None)


if __name__ == "__main__":
    main()
