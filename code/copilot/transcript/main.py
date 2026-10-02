"""Part 1 - Live transcript service (Hari).

Serves Segment messages on ws://127.0.0.1:8771/transcript (+ GET /transcript/history).
Captures transcription live from either:
1. Active Meetily Pro app (via Chrome DevTools Protocol on port 9222)
2. Standalone microphone transcription using faster-whisper
3. Manual typing fallback or POST /transcript/say HTTP injection

Run:
    python -m transcript.main            # Auto-detects Meetily, falls back to microphone
    python -m transcript.main --mic      # Force standalone microphone
    python -m transcript.main --meetily  # Force Meetily Pro connection
    python -m transcript.main --typing   # Manual typing fallback
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import sys
import time
import numpy as np

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


async def meetily_cdp_source(cdp_port: int = 9222):
    """
    Connects to the running Meetily Pro Tauri WebView2 via Chrome DevTools Protocol,
    subscribes to 'transcript-update' events, and publishes live Segments.
    """
    print(f"Connecting to Meetily Pro on port {cdp_port}...")
    t0 = time.monotonic()
    seg_idx = 0

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{cdp_port}/json", timeout=2.0) as resp:
                    targets = await resp.json()
                    target = next((t for t in targets if "tauri" in t.get("url", "") or t.get("type") == "page"), targets[0])
                    ws_url = target["webSocketDebuggerUrl"]

                async with session.ws_connect(ws_url) as ws:
                    print(f"Connected to Meetily Pro debugger: {ws_url}")
                    print("Ready! Start a recording in Meetily Pro to stream live transcripts to Co-pilot...")

                    setup_js = """
                    (() => {
                        window.__meetily_bridge_queue = window.__meetily_bridge_queue || [];
                        if (!window.__meetily_bridge_installed) {
                            window.__meetily_bridge_installed = true;
                            const handler = (event) => {
                                if (event && event.payload) {
                                    window.__meetily_bridge_queue.push(event.payload);
                                }
                            };
                            const cbId = window.__TAURI_INTERNALS__.transformCallback(handler);
                            window.__TAURI_INTERNALS__.invoke('plugin:event|listen', {
                                event: 'transcript-update',
                                handler: cbId,
                                target: { kind: 'Any' }
                            });
                        }
                        return true;
                    })()
                    """
                    await ws.send_json({"id": 1, "method": "Runtime.evaluate", "params": {"expression": setup_js, "awaitPromise": True}})
                    await ws.receive_json()

                    poll_js = """
                    (() => {
                        const items = window.__meetily_bridge_queue || [];
                        window.__meetily_bridge_queue = [];
                        return JSON.stringify(items);
                    })()
                    """

                    while not ws.closed:
                        await ws.send_json({"id": 2, "method": "Runtime.evaluate", "params": {"expression": poll_js, "awaitPromise": True}})
                        msg = await ws.receive_json()
                        result_val = msg.get("result", {}).get("result", {}).get("value")

                        if result_val:
                            try:
                                updates = json.loads(result_val)
                                for u in updates:
                                    text = (u.get("text") or "").strip()
                                    if not text:
                                        continue
                                    seg_idx += 1
                                    start = float(u.get("audio_start_time", u.get("chunk_start_time", time.monotonic() - t0)))
                                    dur = float(u.get("duration", 1.5))
                                    end = float(u.get("audio_end_time", start + dur))
                                    is_final = not bool(u.get("is_partial", False))
                                    speaker = u.get("source") or "host"

                                    seg = Segment(
                                        id=f"seg-{u.get('sequence_id', seg_idx):04d}",
                                        text=text,
                                        start=round(start, 2),
                                        end=round(end, 2),
                                        final=is_final,
                                        speaker=speaker,
                                    )
                                    d = await stream.publish(seg)
                                    status_str = "FINAL" if is_final else "PARTIAL"
                                    print(f"  [#{d['seq']}] [{start:5.1f}s - {end:5.1f}s] [{status_str}] ({speaker}) {text}")
                            except Exception as e:
                                print(f"Error parsing Meetily segment: {e}")

                        await asyncio.sleep(0.15)

        except Exception as e:
            print(f"Meetily Pro connection notice ({e}). Retrying in 2s...")
            await asyncio.sleep(2.0)


async def live_microphone_source(model_name: str = "base.en", chunk_sec: float = 3.0):
    """
    Captures audio from the microphone and streams transcribed segments in real-time.
    """
    import sounddevice as sd
    from faster_whisper import WhisperModel

    print(f"Loading Whisper model '{model_name}' on CPU (int8)...")
    loop = asyncio.get_running_loop()

    model = await loop.run_in_executor(
        None, lambda: WhisperModel(model_name, device="cpu", compute_type="int8")
    )
    print("Model loaded! Listening to default microphone. Speak now...")

    sample_rate = 16000
    chunk_samples = int(chunk_sec * sample_rate)
    audio_q: queue.Queue[np.ndarray] = queue.Queue()

    def audio_callback(indata, frames, time_info, status):
        if status:
            pass
        audio_q.put(indata[:, 0].copy())

    input_stream = sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        callback=audio_callback,
    )

    t0 = time.monotonic()
    seg_idx = 0
    buffer = np.zeros(0, dtype=np.float32)

    with input_stream:
        while True:
            chunks = []
            while not audio_q.empty():
                try:
                    chunks.append(audio_q.get_nowait())
                except queue.Empty:
                    break

            if chunks:
                buffer = np.concatenate([buffer] + chunks)

            if len(buffer) >= chunk_samples:
                audio_to_transcribe = buffer[:chunk_samples]
                overlap_samples = int(0.5 * sample_rate)
                buffer = buffer[chunk_samples - overlap_samples:]

                now = time.monotonic() - t0
                start_sec = max(0.0, now - chunk_sec)
                end_sec = now

                def transcribe_sync():
                    rms = np.sqrt(np.mean(audio_to_transcribe ** 2))
                    if rms < 0.005:
                        return []
                    segments, _ = model.transcribe(
                        audio_to_transcribe,
                        language="en",
                        vad_filter=True,
                        vad_parameters=dict(min_silence_duration_ms=400),
                    )
                    return [s.text.strip() for s in segments if s.text.strip()]

                texts = await loop.run_in_executor(None, transcribe_sync)

                for text in texts:
                    if not text or text.lower() in ("you", "thank you.", "bye.", "thanks."):
                        continue
                    seg_idx += 1
                    seg = Segment(
                        id=f"seg-{seg_idx:04d}",
                        text=text,
                        start=round(start_sec, 2),
                        end=round(end_sec, 2),
                        final=True,
                        speaker="host",
                    )
                    d = await stream.publish(seg)
                    print(f"  [#{d['seq']}] [{start_sec:5.1f}s - {end_sec:5.1f}s] {text}")

            await asyncio.sleep(0.15)


async def typing_source():
    t0 = time.monotonic()
    n = 0
    loop = asyncio.get_running_loop()
    print("typing mode: each line you enter is sent as a final segment")
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        if not line.strip():
            continue
        n += 1
        now = time.monotonic() - t0
        d = await stream.publish(Segment(id=f"seg-{n:04d}", text=line.strip(), start=max(0, now - 3),
                                         end=now, final=True, speaker="host"))
        print(f"  sent #{d['seq']}")


async def is_meetily_running(cdp_port: int = 9222) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{cdp_port}/json", timeout=0.8) as resp:
                return resp.status == 200
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Part 1: Live Transcript Service")
    parser.add_argument("--typing", action="store_true", help="Run in manual typing mode instead of audio")
    parser.add_argument("--mic", action="store_true", help="Force local microphone transcription via faster-whisper")
    parser.add_argument("--meetily", action="store_true", help="Force Meetily Pro live transcription bridge")
    parser.add_argument("--model", default="base.en", help="Whisper model size for mic mode (default: base.en)")
    args, _ = parser.parse_known_args()

    app = web.Application()
    stream.attach(app)
    app.router.add_post("/transcript/say", say_handler)

    async def start(_):
        if args.typing:
            app["source"] = asyncio.create_task(typing_source())
        elif args.mic:
            app["source"] = asyncio.create_task(live_microphone_source(model_name=args.model))
        elif args.meetily:
            app["source"] = asyncio.create_task(meetily_cdp_source())
        else:
            # Auto-detection: check if Meetily Pro is running on port 9222
            meetily_active = await is_meetily_running()
            if meetily_active:
                print("Meetily Pro detected on port 9222! Using Meetily Pro transcription stream.")
                app["source"] = asyncio.create_task(meetily_cdp_source())
            else:
                print("Meetily Pro not detected on port 9222. Using standalone microphone transcription.")
                app["source"] = asyncio.create_task(live_microphone_source(model_name=args.model))

    app.on_startup.append(start)
    run_app(app, TRANSCRIPT_PORT)


if __name__ == "__main__":
    main()
