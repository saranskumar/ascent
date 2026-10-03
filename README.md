# Meetily Visual Copilot

Team TUF · CSI SCT SB · SCTCE hackathon · Track 01, *Automate Workflow with Meetily.ai*.

Meetily understands meetings from audio. We add two layers on top of it:

1. **Visual context summary** (priority): a Meetily workflow that captures what was shown on screen, OCRs it, interleaves it with the transcript, and writes a better summary back into Meetily.
2. **Live co-pilot**: while the host talks, it notices when a picture would help, finds a few images, and offers them in a host-only overlay. One click puts the image on a canvas window that is already shared in the meeting.

## Status (Oct 3)

| Piece | Status | Code |
| --- | --- | --- |
| Visual summary (capture → OCR → Gemini → `PUT` into Meetily) | Working, with tests | [`code/main/`](code/main/README.md) | The production desktop app (PyQt6, fully offline); [architecture](code/main/ARCHITECTURE.md) |
| [`code/extraction-test/`](code/extraction-test/README.md) |
| Co-pilot image engine (cue + LLM detection, web image search, ~5-7 s speech → suggestion) | Working | [`code/copilot/engine/`](code/copilot/engine/README.md) |
| Co-pilot transcript source | Working: bridges into Meetily Pro's live transcript (WebView2 debug port 9222), falls back to the microphone (`faster-whisper`), or typing mode | [`code/copilot/transcript/`](code/copilot/transcript/README.md) |
| Co-pilot GUI (overlay + canvas) | Working: PySide6 overlay (hidden from screen capture) and shareable canvas | [`code/copilot/gui/`](code/copilot/gui/README.md) |

## Repo layout

| Path | What |
| --- | --- |
| [`code/extraction-test/`](code/extraction-test/README.md) | Visual context summary workflow (`vcs/`, `cli.py`, `manifest.yaml`); [architecture](code/extraction-test/ARCHITECTURE.md) |
| [`code/copilot/`](code/copilot/README.md) | Live co-pilot: contracts, transcript, engine, GUI, mocks, tests; [architecture](code/copilot/ARCHITECTURE.md) |
| [`docs/`](docs/README.md) | Idea, tech notes, Meetily API findings, team |
| [`copilot/`](copilot/) | Early live-transcript experiments and the Meetily `openapi.json` |
| [`presentation/`](presentation/registration/index.html) | Registration deck and submission PDF |
| [`GD/`](GD/README.md), `drops/`, `guidelines/` | Team discussions, organiser material, track rules |

## Quick start

Python 3.10+. Each project has its own `requirements.txt` and README.

```bash
# Live co-pilot (run from code/copilot)
pip install -r requirements.txt
python -m pytest -q tests          # offline
python -m mocks.mock_transcript --loop   # fake transcript
python -m engine.main                    # image engine on ws://127.0.0.1:8772
python -m gui                            # overlay + canvas (needs an engine or mock_engine running)
```

```powershell
.\run_all.ps1 -MockTranscript    # whole chain, replacing whichever part isn't ready
```

```bash
# Visual summary (run from code/extraction-test)
pip install -r requirements.txt
cp .env.example .env             # add GEMINI_API_KEY and MEETILY_PRO_TOKEN
python -m pytest -q
```

Secrets live in gitignored `.env` files or environment variables, never in code. Optional keys: `GEMINI_API_KEY` (LLM), `SERPER_API_KEY` (better image search; without it the engine uses DuckDuckGo and Wikimedia).

## Docs

Start at [`docs/README.md`](docs/README.md); the current plan is in [`docs/tech/README.md`](docs/tech/README.md). For how the code fits together, see the architecture overviews for the [live co-pilot](code/copilot/ARCHITECTURE.md) the [visual summary workflow](code/extraction-test/ARCHITECTURE.md) and the [main app](code/main/ARCHITECTURE.md).

## Privacy

Capture, OCR and transcription stay local. Gemini is used for summarisation and cue/query detection. The co-pilot's web image search sends only a short keyword query, never the transcript. All co-pilot services bind to `127.0.0.1`.
