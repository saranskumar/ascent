# Live Co-pilot: Status & Options (Oct 2)

**Status: on hold, no decision yet.** The live co-pilot needs a live transcript, and Meetily Pro doesn't expose one (see [meetily-api-findings.md](meetily-api-findings.md)). The visual summary is the priority.

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

## Images, if we build it

- Default: the team's **local image folder**, indexed with CLIP on-device.
- Optional and opt-in: web image search, sending only the keyword (never audio or transcript).

## Suggested next step (if revived)

Timebox one person to 1–2 h trying to build Meetily Community from source. If it builds → option 3. If not → option 4.

## Judge framing

"Meetily only releases the transcript after the meeting (`409 recording_in_progress`), so a live feature can't be built on its interfaces today." Worth raising with the Zackriya team as product feedback.
