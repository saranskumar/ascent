# Co-pilot contract

This is the only thing the three parts share. If you change it, tell the others and update `messages.py`, the samples and the mocks in the same commit.

```text
 1. transcript (Hari) ──segments──► 2. engine (Shreevardhan) ──suggestions──► 3. GUI (Mahreen)
    ws :8771/transcript                ws :8772/suggestions   ◄──select / dismiss──
                                       http :8772/images/…
```

All traffic is local only (`127.0.0.1`). Ports can be overridden with `COPILOT_TRANSCRIPT_PORT` / `COPILOT_ENGINE_PORT` (see `config.py`).

## Streams

Each producer serves its stream at:

| | Transcript (part 1) | Suggestions (part 2) |
| --- | --- | --- |
| WebSocket | `ws://127.0.0.1:8771/transcript` | `ws://127.0.0.1:8772/suggestions` |
| History (HTTP GET, JSON list) | `/transcript/history?since=N` | `/suggestions/history?since=N` |
| Messages back from the client | none | `select`, `dismiss` |

- **`seq`:** every message gets a number that goes up by one. The stream server sets it, so producers don't.
- **Reconnects:** a client remembers the last `seq` it got and reconnects with `?since=<seq>`. It then receives everything it missed, in order, followed by live messages.
- **First connection:** without `since`, a client gets the last 60 s of transcript (or 120 s of suggestions), then live messages.
- **Producer restarted:** if `since` is higher than anything the server has sent, the server must have restarted. It treats the request as a first connection. Clients should accept `seq` going backwards after a reconnect.
- **History endpoint:** for debugging (open it in a browser) and for pulling the whole history at once. It's not a polling fallback.

In Python, don't implement any of this yourself. Use `contracts/stream.py`: `StreamServer` to serve a stream, `Subscriber` to read one.

## Messages

Defined as dataclasses in [`messages.py`](messages.py). Unknown fields must be ignored, so adding a field doesn't break anyone.

### `segment` (1 → 2)
```json
{"type": "segment", "seq": 118, "id": "seg-0042",
 "text": "so let me explain the circuit breaker pattern",
 "start": 123.4, "end": 127.9, "final": true, "speaker": "host"}
```
- `id` stays the same for a given segment. If text is sent again with the same `id`, it replaces the earlier text (e.g. partial → final).
- `final: false` marks a partial that may still change. The engine only uses finals.
- `start` / `end` are seconds from the start of the meeting. `speaker` is optional.

### `suggestion` (2 → 3)
```json
{"type": "suggestion", "seq": 7, "id": "sug-0007", "topic": "Circuit breaker pattern",
 "reason": "speaker: 'let me explain the circuit breaker pattern'",
 "priority": "elevated", "source_segment_ids": ["seg-0042"], "expires_in": 45,
 "images": [{"id": "img-0031",
             "url": "http://127.0.0.1:8772/images/img-0031.jpg",
             "thumb_url": "http://127.0.0.1:8772/images/img-0031_thumb.jpg",
             "width": 1280, "height": 720, "source": "local",
             "source_ref": "assets/circuit-breaker.png", "caption": "Circuit breaker states"}]}
```
- `priority`: `ambient` (show quietly) or `elevated` (worth getting the host's attention).
- `expires_in`: seconds after which the overlay may hide the suggestion.
- `source`: `local` (team image folder) or `web`. Images are always served by the engine over HTTP, even local ones, so the GUI never reads files from disk.

### `select` / `dismiss` (3 → 2)
```json
{"type": "select",  "suggestion_id": "sug-0007", "image_id": "img-0031"}
{"type": "dismiss", "suggestion_id": "sug-0007"}
```
The engine uses these to rank images and to stop suggesting the same topic again. Putting the image on the canvas happens entirely inside the GUI.

## Samples

- `samples/transcript.jsonl`: a scripted ~100 s talk (outage → circuit breaker → backoff → an off-topic keyboard). `mock_transcript` replays it.
- `samples/suggestions.jsonl`: what a good engine *should* produce for that talk, with timings. `mock_engine` replays it, and Shreevardhan can use it as a rough target.
