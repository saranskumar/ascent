# Meetily Visual Context Summaries

Team TUF · CSI SCT SB · SCTCE hackathon · Track 01, *Automate Workflow with Meetily.ai*.

Meetily understands meetings from what is **said**. When people share their screen, the summary misses what was **shown**. "As you can see, this went up 40%" loses its meaning, and the slide, the diagram and the answers marked on screen never reach the summary.

**Our project is a Windows desktop app that adds what was on screen to Meetily's summary, fully offline.**

- **Capture:** when Meetily starts recording, the app captures the window being presented.
- **Screens:** when the recording stops, it pulls out the distinct screens, reads their text, and has a local vision model describe each one.
- **Timeline:** it merges the screens with Meetily's transcript by time.
- **Summary:** a local model writes a detailed summary: key points with their answers, questions and answers, steps of a demo, the work flow, decisions, and who does what.
- **Write-back:** the summary goes back into Meetily, after Meetily's own version is backed up.

**The code is in [`code/main/`](code/main/README.md).**

## How it works

```text
Meetily Pro (local API 127.0.0.1:8420, webhooks)
   │ recording.started                 │ recording.stopped
   ▼                                   ▼
 pick the window (app comes      Live queue, one meeting at a time:
 to the front, or last-used)     1. Extract screens   1 fps capture → distinct screens → content area only → OCR
   │                             2. Describe screens  local vision model: what each screen shows
   ▼                             3. Summarize         speech + [SCREEN] lines by time → local model → Detailed template
 1 fps window capture               (long meetings: parts along the timeline → combined, as Meetily does)
                                 4. Write to Meetily  back up Meetily's summary → PUT ours (asks before replacing)
```

| | |
| --- | --- |
| App | PyQt6 tray app with Overview, Live, Meetings, New run, VLM test and Settings |
| Meeting data | Meetily Pro's local Agent API and webhooks (Subscribe, Verify HMAC, Deduplicate, Fetch, Act) |
| Capture | Windows Graphics Capture: one chosen window at 1 frame per second, even when covered |
| Screens | perceptual-hash dedup and bullet-build merging (from lecture-to-notes, MIT); content area per layout; RapidOCR |
| Model | one local Ollama model for screen descriptions and the summary. Default `qwen3-vl:2b-instruct` (runs on a 4 GB GPU or the CPU); any installed model can be picked |
| Summary | Meetily's prompts and templates plus our Detailed template; Meetily's chunk-and-combine method for long meetings |

## Quick start

```bash
cd code/main
pip install -r requirements.txt
ollama pull qwen3-vl:2b-instruct
cp .env.example .env           # MEETILY_PRO_TOKEN = a Meetily key with `write` scope
python run.py
```

The one-time Meetily setup (local API, webhook target `127.0.0.1:8766`, approval) is in [code/main/README.md](code/main/README.md#setup-once). The app's Overview tab checks each step live.

```bash
python -m pytest -q tests      # offline: fake Ollama and fake Meetily
```

## Docs

| Doc | What |
| --- | --- |
| [code/main/README.md](code/main/README.md) | Setup, tabs, behaviour, files |
| [docs/tech/README.md](docs/tech/README.md) | Architecture and stack |
| [docs/tech/summary-pipeline.md](docs/tech/summary-pipeline.md) | From recording to summary: stages, timeline, templates, long meetings, write-back |
| [docs/tech/screen-capture.md](docs/tech/screen-capture.md) | Capture, distinct screens, content area, screen descriptions |
| [docs/tech/meetily-api-findings.md](docs/tech/meetily-api-findings.md) | What Meetily Pro's API can and can't do (tested) |
| [docs/tech/meetily-summary-prompts.md](docs/tech/meetily-summary-prompts.md) | Meetily's prompts and templates, and what we add |
| [docs/idea/README.md](docs/idea/README.md) | Problem and idea (registration pitch) |

## Privacy

Nothing leaves the computer:

- Meetily's API, the webhooks and the app all run on `127.0.0.1`.
- OCR and the model (Ollama) are local.
- Only the window you pick is captured. The capture video is deleted after the screens are extracted, unless you choose to keep it.
- The only secret, the Meetily write key, lives in a gitignored `.env`.
