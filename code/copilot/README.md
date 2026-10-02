# Live Co-pilot

While the host is talking, the co-pilot notices when a picture would help, finds a few images, and offers them in a host-only overlay. When the host clicks one, it appears on a **canvas** window that has been shared into the meeting since the start, so the host never has to switch what they're sharing.

```text
 1. transcript ──segments──► 2. image engine ──suggestions──► 3. GUI: overlay (host) ──click──► canvas (shared)
    Hari                        Shreevardhan    ◄──select/dismiss──  Mahreen
    ws :8771                    ws :8772 + http :8772/images
```

| Folder | Owner | What |
| --- | --- | --- |
| [`contracts/`](contracts/) | everyone | **The interface.** Message formats, ports, sample data, and the shared stream helper. Read [contracts/README.md](contracts/README.md) first. |
| [`transcript/`](transcript/) | Hari | Part 1. Runs in typing mode until the real source is plugged in. |
| [`engine/`](engine/) | Shreevardhan | Part 2. Working: cue + LLM detection, web image search, ~5–7 s from speech to suggestion. See [engine/README.md](engine/README.md). |
| [`gui/`](gui/) | Mahreen | Part 3. Stack still to be chosen. |
| [`mocks/`](mocks/) | everyone | Fake parts, so each part can be built without the others. |
| [`tests/`](tests/) | everyone | `python -m pytest -q tests` (offline). |

## Setup

Run all commands from this folder (`code/copilot`).
```bash
pip install -r requirements.txt
```

## Working on one part alone

| You're building | Run the fake upstream | Check your output with |
| --- | --- | --- |
| 1 transcript | (nothing) | `python -m mocks.tap transcript` |
| 2 engine | `python -m mocks.mock_transcript --loop` | `python -m mocks.tap suggestions --auto-select` |
| 3 GUI | `python -m mocks.mock_engine --loop` | the mock prints every select / dismiss it receives |

`--speed 3` makes either mock replay faster. `mocks/preview.html` is a throwaway overlay + canvas in one browser page, useful for seeing the whole chain work before the real GUI exists.

## Everything together

```powershell
.\run_all.ps1                   # real parts
.\run_all.ps1 -MockTranscript   # replace whichever part isn't ready yet
.\run_all.ps1 -MockEngine
```

## Rules

- Change `contracts/` only with the others' agreement. Update `messages.py`, the samples and the mocks in the same commit.
- Ignore message fields you don't recognise, so that adding a field never breaks another part.
- Everything stays on `127.0.0.1`. Any web image search sends only the keyword, never the transcript.
