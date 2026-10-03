# Live Co-pilot: Status & Options (Oct 3)

**Status (updated Oct 3): all three parts work, built as decoupled parts.** Meetily Pro doesn't expose a live transcript through its API (see [meetily-api-findings.md](meetily-api-findings.md)), so the co-pilot was split so it didn't block on it. The transcript source is now solved with options 2 and 1 below: part 1 reads Meetily's live `transcript-update` event over the WebView2 debug port and falls back to the microphone. Part 2 (image engine) and part 3 (PySide6 overlay + canvas) are working. Architecture: [`code/copilot/ARCHITECTURE.md`](../../code/copilot/ARCHITECTURE.md); code, contracts and mocks: [`code/copilot/`](../../code/copilot/README.md). The visual summary remains the priority.

## What exists now

- **Image engine (part 2):** cue phrases (fast path) plus an LLM over the last 30 s (slow path) decide when a picture would help; Serper + DuckDuckGo are raced (Wikimedia Commons if both are empty); downloads are filtered (banners, tiny images, stock watermarks, near-duplicates) and 3 images are published as a `suggestion`. About 5-7 s from speech to suggestion on the sample talks.
- **Web image search is on by default** for the engine; only the short keyword query is sent, never the transcript. This replaces the earlier "local folder first" idea below for the co-pilot.
- **Transcript source (part 1):** Meetily Pro bridge (option 2) when Meetily runs with `--remote-debugging-port=9222`, otherwise the microphone with `faster-whisper` (option 1), plus typing mode and `POST /transcript/say`. See the [transcript README](../../code/copilot/transcript/README.md).
- **GUI (part 3):** PySide6 overlay (always on top, excluded from screen capture, history reel, keyboard controls, panic shutter) and a canvas window to share.
- **Not pursued:** options 3 and 4 below. The Meetily bridge relies on undocumented app internals and could break on a Meetily update; the microphone fallback covers that.

## Why it's blocked

- `GET /v1/meetings/{id}/transcript` (and meeting detail, export) return **`409 recording_in_progress`** during a recording; search doesn't see the active meeting.
- `transcript.updated` fires live but carries **no text**.
- Track 1 is "Automate Workflow with Meetily.ai", and the workflows guide describes **event-driven, mostly post-meeting** automations. A live overlay fits that model poorly.

## Options

| Option | How | Local? | Uses Meetily? | Cost / risk |
| --- | --- | --- | --- | --- |
| **1. Own live transcription** | capture mic + system audio, `faster-whisper` (`base.en`/`small`, int8) on 3–5 s chunks, small Ollama model (`qwen2.5:3b` / `llama3.2:3b`) to spot "visualisable" concepts every ~10 s | yes | no (parallel) | Transcribes the same speech twice; our RTX 2050 has only 4 GB VRAM, shared with Meetily's own transcription |
| **2. Read Meetily's window** | launch Meetily with `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222`, read transcript text from the page | yes | yes (unofficially) | Relies on undocumented app internals; can break on any update. Untested. |
| **3. Patch Meetily Community** | add a listener to the internal `transcript-update` event (`audio/transcription/worker.rs`) that serves segments on a local endpoint (e.g. SSE at `127.0.0.1:<port>/live`): ~50–80 lines of Rust using the existing `tokio` dependency | yes | yes (open-source build) | **Organisers OK'd modifying the open-source code.** The real cost is building Meetily from source on Windows (Rust, Node, whisper.cpp, GPU), which could take hours. Could be offered upstream as "live transcript API" product feedback. |
| **4. Post-meeting "visual recap"** | after the meeting, a local LLM picks 2–3 concepts that most need a picture → generate a **Mermaid diagram** of the discussed process/architecture, or pull from the team's local image folder, and add it to the summary | yes | yes (fits the workflow model) | Not live anymore |

Transcription should stay local (privacy-first, like Meetily). Update Oct 2: **LLM APIs (Gemini) are allowed for summarisation/LLM steps** where needed.

## Images

- Implemented: web image search (Serper, DuckDuckGo, Wikimedia Commons), sending only the keyword (never audio or transcript).
- Not built: a local image folder indexed with CLIP on-device, as an offline alternative.

## What was chosen

Option 2 (read Meetily's window) as the primary source, with option 1 (own live transcription) as the automatic fallback. Option 3 (patching Meetily Community) wasn't needed; it remains the robust long-term route if the debug-port approach breaks.

## Judge framing

"Meetily only releases the transcript after the meeting (`409 recording_in_progress`), so a live feature can't be built on its interfaces today." Worth raising with the Zackriya team as product feedback.
