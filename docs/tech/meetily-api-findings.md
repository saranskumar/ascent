# Meetily API Findings (tested Oct 2)

What we verified against **Meetily Pro 1.11.0** running on our laptop, plus what we read in the open-source (Community) code. This is the ground truth the rest of the design is built on.

Sources: [API reference](https://docs.meetily.ai/developers/api-reference) · [Events](https://docs.meetily.ai/developers/events) · [Authentication](https://docs.meetily.ai/developers/authentication) · [CLI](https://docs.meetily.ai/developers/cli) · [meetily-workflows README](https://github.com/Zackriya-Solutions/meetily-workflows) · full route list saved in [`copilot/openapi.json`](../../copilot/openapi.json)

---

## TL;DR

| Question | Answer |
| --- | --- |
| Can we read the transcript **during** a recording? | **No.** Every transcript route returns `409 recording_in_progress` until `recording.stopped`. |
| Is there a live signal at all? | Yes: the `transcript.updated` webhook fires as the live transcript advances, but it carries **no text**. |
| Can we write a summary back into Meetily? | **Yes.** `PUT /v1/meetings/{id}/summary` with `{ "text": "..." }` (needs `write` scope, no undo). |
| Can we edit or append to the transcript? | **No.** Transcripts are read-only through the API. |
| Can we make Meetily's own summarizer see our visual context? | **No.** `regenerate` only takes `language`, `model`, `model_name`, `template_id`, with no custom text or prompt. |
| Is there a recording-start event? | **Yes**, `recording.started` (we earlier thought there wasn't). |

---

## Access

- Local gateway: `http://127.0.0.1:8420`, off by default. Turn on in **Settings > Integrations**.
- **Loopback token** (read-only): `%APPDATA%\pro.meetily.ai\gateway-token`, written by the app when "Allow the CLI on this computer" is on.
- **Write / record / delete** need a key you create: **Settings > Integrations > Apps & scripts > Create key**, tick the scopes, turn on its *Allow* switch. The loopback token gets `403 insufficient_scope` on these.

| Scope | Allows |
| --- | --- |
| `read` | meetings, transcripts, summaries, status, exports, webhooks |
| `record` | start / stop / pause / resume recording |
| `write` | rename, speaker labels, **set / regenerate summaries**, jobs, settings |
| `delete` | delete meetings |

## Routes we care about

| Route | Scope | Notes |
| --- | --- | --- |
| `GET /v1/recording` | read | `{state, active_meeting_id, last_error}`; how we learn the live meeting id |
| `POST /v1/recording/start` · `/stop` | record | lets our app start Meetily itself if needed |
| `GET /v1/meetings/{id}/transcript` | read | `{meeting_id, title, segments[]}`; **409 while recording** |
| `GET /v1/meetings/{id}` · `/export?format=json` | read | also **409 while recording** |
| `GET /v1/search?q=` | read | does **not** return the active meeting's segments |
| `GET /v1/meetings/{id}/summary` | read | `{meeting_id, status, result?, error?, regeneration_failed?, updated_at}` |
| `PUT /v1/meetings/{id}/summary` | write | body `{text}`; overwrites outright, no undo |
| `POST /v1/meetings/{id}/summary/regenerate` | write | optional `language`, `model`, `model_name`, `template_id`; reruns Meetily's engine on the original transcript |
| `POST /v1/webhooks` | read | register a webhook (destinations need approval in the app) |

**TranscriptSegmentDto:** `id`, `text`, `timestamp`, `audio_start_time`, `audio_end_time`, `duration`, `speaker`, `assigned_meeting_speaker_id`, `detected_meeting_speaker_id`, `words[]`. `audio_start_time` is seconds since the recording started.

## Events (webhooks)

`recording.{started, stopped, paused, resumed, failed, error, stop_failed}` · `job.{submitted, completed, failed, cancelled, paused, resumed, removed}` · `summary.{completed, failed}` · `transcript.updated`

- `transcript.updated`: "fires as the live transcript advances during a recording", notification only, no text.
- `transcription.completed` / `transcript-ready`: **no producer**; don't rely on it.
- Payloads are thin: `{schema_version, event_id, event, occurred_at, resource:{kind,id}, delivery_id}`. Always fetch content by id.

## What the hackathon expects of a workflow

From `drops/Meetily_Workflows.pdf` and `guidelines/00-track-1-brief-and-rules.md`:

1. **Subscribe → Verify (HMAC) → Deduplicate (`event_id`) → Fetch → Act**
2. Least-privilege scopes, no hard-coded secrets (env vars only)
3. Handle Meetily being offline
4. Own repo + `manifest.yaml` for the `meetily-workflows` catalog

> Note: the event page (1111chapter2csi.online/events/ascent) never mentioned Pro, the Agent API or the workflows catalog; this guide arrived on hackathon day. The organizers have since said it's fine to modify the open-source code.

## Live-transcript experiments

Scripts in [`copilot/`](../../copilot/):

| Script | What it does | Result |
| --- | --- | --- |
| `live_transcript.py` | waits for a recording, polls the transcript every 1 s, prints new segments with approximate lag | `409 recording_in_progress` for the whole recording |
| `probe_live.py <word>` | during a recording, tries meeting detail, export, and search for a spoken word | meeting **409**, export **409**, search **0 hits** in the active meeting, even though the Meetily window was showing the word live |

**Conclusion:** Meetily Pro keeps the live transcript inside the app and only persists/exposes it after the recording stops.

## Meetily's summary engine (Community source, for reference)

From `frontend/src-tauri/src/summary/` in [Zackriya-Solutions/meetily](https://github.com/Zackriya-Solutions/meetily) ([DeepWiki](https://deepwiki.com/Zackriya-Solutions/meetily/5-ai-summary-system)). Pro is a separate codebase, so treat this as indicative only.

- **Text in, text out.** Messages are plain strings (`llm_client.rs`); no image input for any provider.
- **Input** is one string of `[MM:SS] text` lines built in `useSummaryGeneration.ts` from `audio_start_time`.
- **Passes** (`processor.rs`): token estimate = chars × 0.35. Chunking (with 100-token overlap) only for Ollama / built-in model over the threshold, then chunk-summary → combine → fill template.
- **Templates** are JSON in `frontend/src-tauri/templates/` (sections with `title`, `instruction`, `format`, `item_format`).
- **Live text inside the app:** every new segment is emitted as a Tauri `transcript-update` event in `audio/transcription/worker.rs`. This is where a live endpoint could be added if we modify the open-source app (see [copilot-live-transcript.md](copilot-live-transcript.md)).
