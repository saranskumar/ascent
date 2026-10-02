# Part 2: Image engine (Shreevardhan)

Reads segments from part 1, decides when a picture would help, finds 3 images on the web, and publishes `suggestion` messages on `ws://127.0.0.1:8772/suggestions`. Images are saved to `.cache/images` and served at `http://127.0.0.1:8772/images/`.

```bash
python -m engine.main
```

## How it works

```text
final segment ──► cue phrase? ──yes──► fast path: LLM now, focused on that line ─────► elevated
                       │no
                       └──────────► slow path: LLM on last 30 s (≥5 s apart) ────────► ambient
                                         │ need = {topic, query, kind, reason}
                                         ▼
                topic already suggested in the last 3 min? ──► drop
                                         ▼
          search: Serper + DuckDuckGo raced (Wikimedia if both empty) ──► ~40 candidates
                                         ▼
    download ≤14 in parallel ──► filter ──► 3 images, at most 1 per site ──► publish
```

| File | What |
| --- | --- |
| [`detect.py`](detect.py) | Cue phrases (`let me explain`, `looks like a`, `if you plot`, `picture a`…), the LLM prompt, and a no-LLM fallback (words after the cue) |
| [`llm.py`](llm.py) | `gemini-3.5-flash-lite` (~1 s), then `gemini-flash-lite-latest`, then local llama.cpp. A backend that fails or hits a rate limit is paused for a while instead of being retried on every call. |
| [`search.py`](search.py) | Serper (Google Images, needs a key), DuckDuckGo (`ddgs`, keyless), Wikimedia Commons (keyless). Only the query is sent, never the transcript. For Wikimedia the query is simplified first (kind words like "diagram" dropped, then the first 3 words), and results whose title shares no word with the query are discarded. |
| [`fetch.py`](fetch.py) | Downloads candidates and rejects: banners (aspect > 2.5), images too small, stock-photo sites (watermarks), blank images, files that aren't images, and near-duplicates (also of images shown earlier). Saves a JPEG of max 1600 px plus a 320×200 thumbnail. |
| [`main.py`](main.py) | `Engine` (paths, topic cooldown, upgrading to elevated, feedback) and the server wiring |
| [`replay.py`](replay.py) | Runs the engine on a transcript file with no servers, and writes a `.cache/replay.html` contact sheet |

`select` is logged; `dismiss` blocks that topic for 3 more minutes. Every decision is appended to `.cache/engine_log.jsonl`.

## Setup

`pip install -r requirements.txt`. Keys are read from the real environment, then `code/copilot/.env`, then `code/extraction-test/.env` (both gitignored):

```text
GEMINI_API_KEY=...      # optional: without it, only the local LLM is used
SERPER_API_KEY=...      # optional: without it, search is DuckDuckGo + Wikimedia only
```

| Env var | Default | |
| --- | --- | --- |
| `COPILOT_GEMINI_MODELS` | `gemini-3.5-flash-lite,gemini-flash-lite-latest` | tried in order |
| `COPILOT_LLM` | `auto` | `gemini` / `local` to force one |
| `COPILOT_LOCAL_LLM_URL` / `COPILOT_LOCAL_MODEL` | `http://127.0.0.1:8080/v1/chat/completions` / `qwen3-8b-Q4_K_M` | any OpenAI-compatible server |
| `COPILOT_SEARCH` | `serper,ddg,wikimedia` (serper only if keyed) | e.g. `ddg,wikimedia` |
| `COPILOT_SEARCH_GRACE` | `1.5` | seconds to wait for the slower search provider |

Don't use `gemini-3.5-flash` / `3.8-flash` here: they take 15 s+ per call, and the free tier allows only 20 requests a day on 3.8.

## Develop and test

```bash
python -m pytest -q tests                                     # offline: no network or LLM
python -m engine.replay                                       # sample talk, real LLM + search, writes .cache/replay.html
python -m engine.replay --file engine/testdata/smalltalk.jsonl --target NUL   # should stay quiet
python -m mocks.mock_transcript --loop                        # + python -m engine.main, + mocks/preview.html
python -m mocks.tap suggestions --auto-select                 # fake GUI
```

## Results (Oct 2, sample talks)

| Talk | Suggestions | Speech → suggestion |
| --- | --- | --- |
| `contracts/samples/transcript.jsonl` (outage → circuit breaker → backoff → keyboard) | 5, all relevant, 3 images each | 4.6–6.9 s |
| `engine/testdata/robotics.jsonl` | 5 (drivetrain, mecanum, cycloidal drive, Pi + CAN, lidar) | 6–11 s |
| `engine/testdata/smalltalk.jsonl` (standup, logistics) | 0 ✓ | |

Rough time budget: LLM ~1 s, search ~1.8 s, downloads ~2–2.5 s. On the slow path, add up to 1 s waiting for the speaker to finish the sentence.

The local fallback (`qwen3-8b` via llama.cpp) gives the same quality. It's only fast enough if llama.cpp runs on the GPU (~60 tok/s); on CPU it's ~7 tok/s, or 8–12 s per call.

## Ideas not done yet

- Upgrade an already-published `ambient` suggestion to `elevated` when a cue phrase arrives later. This needs a contract change (a suggestion revision message).
- Use `select` as a ranking signal, e.g. prefer sites or diagram styles the host picks.
- Prefetch on partial segments to save ~2 s. The contract says to act on finals only, so this needs agreement first.
- Brave as another search provider. Add a function to `PROVIDERS` in `search.py`.
