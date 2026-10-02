# Meetily Visual Copilot - Technical Notes

> **Updated Oct 2 (hackathon day).** Plan revised after testing Meetily Pro's API and reading the workflows guide. The registration-era architecture is kept at the bottom for reference.

## Docs in this folder

| Doc | What's in it |
| --- | --- |
| [meetily-api-findings.md](meetily-api-findings.md) | What Meetily Pro's API can and can't do (tested), events, scopes, write-back, summary engine notes |
| [summary-pipeline.md](summary-pipeline.md) | Visual context summary: product goals, post-meeting pipeline, open decision (build on Meetily's summary vs our own) |
| [screen-capture.md](screen-capture.md) | Screenshot capture and selection: MVP based on lecture-to-notes, presenting vs watching, content-area detection plan |
| [copilot-live-transcript.md](copilot-live-transcript.md) | Live co-pilot: why it's blocked, the four options, status on hold |

## Current plan (short)

1. **Priority: Visual context summary**, built as a Meetily workflow: `recording.started` → capture the chosen window → `recording.stopped` / `summary.completed` → extract distinct screenshots → OCR (+ vision model for diagrams) → pair with transcript window → local LLM edit pass → `PUT` summary back into Meetily.
2. **Live co-pilot: on hold.** Meetily returns `409 recording_in_progress` for transcripts during a recording; options are documented.
3. **Everything runs locally** (privacy-first, like Meetily).
4. **Workflow requirements:** Subscribe → Verify HMAC → Deduplicate → Fetch → Act; least-privilege keys; no hard-coded secrets; handle Meetily offline; `manifest.yaml`.

```text
Meetily Pro (local Agent API, 127.0.0.1:8420)
   │ recording.started / recording.stopped / summary.completed (webhooks)
   ▼
Our workflow (local)
   capture window ─► distinct screenshots ─► OCR / VLM
   fetch transcript + Meetily summary ─► pair screenshots with transcript windows
   ─► local LLM edit pass ─► PUT /v1/meetings/{id}/summary
```

## Stack (current)

| Layer | Technology |
| --- | --- |
| Meeting core | Meetily Pro 1.11 Agent API (HTTP, webhooks, CLI) |
| Capture | Windows Graphics Capture (`windows-capture`), 1–2 s sampling, or 1 fps window video |
| Dedup | pHash (centre crop for MVP), two thresholds, text-containment merge (adapted from lecture-to-notes, MIT) |
| OCR | Local CPU OCR (RapidOCR / Tesseract; lecture-to-notes uses Surya for high quality) |
| Vision | Small local VLM via Ollama, diagram-heavy screenshots only |
| LLM | Local via Ollama (model TBD) for the edit pass |
| Hardware | Laptop RTX 2050, 4 GB VRAM, shared with Meetily's own transcription |

## Experiments

[`copilot/`](../../copilot/): `live_transcript.py`, `probe_live.py`, `openapi.json` (live API spec from our Pro install). See [meetily-api-findings.md](meetily-api-findings.md#live-transcript-experiments).

## Registration-era architecture (superseded)

### Architecture (high level)

```text
                    MEETILY CORE
         recording · Whisper · context · Agent API
                          │
           ┌──────────────┴──────────────┐
           ▼                             ▼
   INBOUND (Summarization)      OUTBOUND (Live Copilot)
   Screen → dHash → OCR         Whisper stream → topic/intent
        → timeline fusion            → local retrieval
        → Ollama synthesis           → side panel (2–3)
           │                             │
           ▼                             ▼
   Entity-grounded notes        Preview → 1-click Share
```

Shared layer: **multimodal context** (speech + screen).

### Stack

| Layer | Technology |
| --- | --- |
| Meeting core | [Meetily](https://meetily.ai/) (Zackriya) - transcript, summaries, Agent API / MCP |
| Speech | Whisper (via Meetily / local) |
| Keyframes | Screen capture + difference hashing (dHash) |
| OCR | Apple Vision / Tesseract (changed frames only) |
| LLM | Ollama (local grounded synthesis + intent) |
| Retrieval | Indexed local `/assets` + optional embeddings / vector cache |
| UI | Meetily-adjacent side panel / desktop companion (Tauri candidates) |
| Privacy | Local-first; no cloud required for the visual pipeline |

### Agent loop

1. **Observe** - live transcript, meeting context, screen context  
2. **Reason** - topic, explanatory intent, visual type needed  
3. **Retrieve / Create** - local assets (or generate if appropriate)  
4. **Act** - Preview · Copy · Share · Save  

### 30-hour MVP phases

| Phase | Goal |
| --- | --- |
| 1 | Meetily → app: transcript / context via Agent API or export hooks |
| 2 | Context extract: topic, entities, explanatory intent → visual type |
| 3 | Local retrieval: small curated `/assets` (architecture, algorithms, patterns) |
| 4 | Copilot UI: suggestion card with Preview + Share |
| 5 (stretch) | Screen grounding: keyframe + OCR on timeline |
| 6 (stretch) | Context-aware summary incorporating visual milestones |

## References

- Meetily: https://meetily.ai/
- Developer docs: https://docs.meetily.ai/developers
- API reference: https://docs.meetily.ai/developers/api-reference
- Events: https://docs.meetily.ai/developers/events
- Workflows catalog: https://github.com/Zackriya-Solutions/meetily-workflows
- Repo: https://github.com/Zackriya-Solutions/meetily
- lecture-to-notes (MIT): https://github.com/drpwchen/lecture-to-notes
