# Meetily Visual Context Summaries: Technical Overview

> **Updated Oct 3.** The project is the offline desktop app in [`code/main/`](../../code/main/README.md). This page covers its architecture, stack and the decisions behind them.

## Docs in this folder

| Doc | What's in it |
| --- | --- |
| [summary-pipeline.md](summary-pipeline.md) | Stages from recording to summary: timeline input, templates, long meetings, guards, write-back |
| [screen-capture.md](screen-capture.md) | Window picker, 1 fps capture, distinct screens, content area per layout, screen descriptions |
| [meetily-api-findings.md](meetily-api-findings.md) | What Meetily Pro's API can and can't do (tested), events, scopes, write-back |
| [meetily-summary-prompts.md](meetily-summary-prompts.md) | Meetily's prompts, templates and chunking (from its source), and what we add |

## Architecture

```text
Meetily Pro 1.11 (local Agent API 127.0.0.1:8420)
   │ webhooks: recording.started / recording.stopped / summary.completed
   ▼
Meeting Summaries app (code/main, PyQt6 tray app, webhook listener on 127.0.0.1:8766)
   recording.started ─► window picker (in front, or last-used after 45 s) ─► 1 fps capture of that window
   recording.stopped ─► job queue (one at a time, persisted, resumable):
        Extract screens    distinct screens · content area per layout · RapidOCR
        Describe screens   Ollama vision model: what each screen shows
        Summarize          [MM:SS] speech + [SCREEN] lines by time ─► template (Detailed by default)
                           too long for the context: parts along the timeline ─► combine ─► template
        Write to Meetily   back up Meetily's summary ─► PUT ours (asks before replacing) ─► rename
   summary.completed ─► guard: Meetily replaced ours? put it back / ask
```

- **UI:** Overview (status, connection guide, events), Live (queue, steps, log, streaming summary), Meetings (summary edit and send, transcript, screens viewer, timeline, speakers, model input), New run, VLM test and Settings.
- **Workflow requirements:** these are met as follows.
  - Subscribe (one stored webhook), Verify (HMAC-SHA256, ±5 min), Deduplicate (`event_id` stored before the ack), Fetch (thin payloads, 409 retried) and Act (one worker, in order).
  - Events are parked while Meetily is offline.
  - Least privilege: reads use Meetily's read-only loopback token, and only writes use the `write` key.
  - No secrets in code.

## Stack

| Layer | Technology |
| --- | --- |
| Meeting core | Meetily Pro 1.11 Agent API (HTTP, webhooks) |
| App | Python 3.12, PyQt6 (tray, single instance, start with Windows) |
| Capture | Windows Graphics Capture (`windows-capture`), one window at 1 fps |
| Distinct screens | pHash with step + drift thresholds and text-containment merge of bullet builds (adapted from lecture-to-notes, MIT) |
| Content area | layouts split where the window edges change; per layout, pixels that differ from their median; browser edge bands when nothing moves |
| OCR | RapidOCR (ONNX, CPU), meeting-app UI words and URLs removed |
| Model | Ollama, one model for vision and text. Default `qwen3-vl:2b-instruct`, CPU or GPU (4 GB RTX 2050). The app starts Ollama when needed |
| Summary | Meetily's final-report prompt and templates plus our screen rules and Detailed template; Meetily's chunk → combine → report for long meetings |
| Tests | pytest, offline (fake Ollama HTTP server, fake Meetily client, offscreen Qt) |

## Decisions (and why)

| Decision | Why |
| --- | --- |
| **Fully offline: Ollama instead of Gemini** | Privacy, and no API keys or quotas. One model for vision and text keeps memory to one model on a 4 GB GPU |
| **Our own summary (not editing Meetily's)** | Meetily summarises without the screen, so moments like "this one went up 40%" are already lost from its summary. We write from the full transcript with the screens on the same timeline, in Meetily's format |
| **A desktop app, not a web UI** | Popups had to appear on top during a meeting, and processing needs a visible queue (back-to-back meetings) |
| **Every screen is described (pictures too)** | For a quiz, a product photo or a diagram, the picture is the content. Text-only OCR missed the answers marked on screen |
| **Content area per layout** | Browser tabs ("ChatGPT", "Inbox") were read as meeting content. One box can't fit both "page with tabs" and "full screen" |
| **Meetily's chunking for long meetings** | A local model's context is small. Meetily's tested method (0.35 tokens/char, context − 300, 100 overlap), split along our timeline so screens stay with their speech |
| **Guards for small models** | 2B models loop, invent decisions and copy placeholders, so these are removed after generation |
| **Write on `recording.stopped`** | Meetily only sends `summary.completed` when it generates its own summary, and hands-off recordings don't get one |
| **Ask before replacing Meetily's summary** | `PUT` has no undo. Meetily's version is backed up first in any case |

## References

- Meetily: https://meetily.ai/ · developer docs https://docs.meetily.ai/developers · source https://github.com/Zackriya-Solutions/meetily
- Workflows catalog: https://github.com/Zackriya-Solutions/meetily-workflows
- lecture-to-notes (MIT): https://github.com/drpwchen/lecture-to-notes
- Ollama: https://ollama.com
