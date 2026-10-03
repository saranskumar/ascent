# Summary Pipeline (Oct 3)

From a Meetily recording to a summary in Meetily, as built in [`code/main`](../../code/main/README.md). Capture and screen extraction are in [screen-capture.md](screen-capture.md).

## What we want (product view)

- **Reader:** someone who **missed the meeting**. They read the summary and never wonder "what was on screen?".
- **Text only:** the summary must pass the *clipboard test*, meaning it's understandable when pasted into Slack or email with no images.
- **Vague references resolved:** "as you can see", "this one" and "that number" become facts.
- **Points with answers:** questions with their answers, the steps of a demo, who does what, how the work flows.
- **Nothing invented:** nothing from the screen that nobody talked about, and no decisions or tasks that nobody said.
- **Hands-off:** pick the window once per meeting, everything else is automatic.

## Stages (one job per meeting, on the Live tab)

| Stage | What happens | Code |
| --- | --- | --- |
| Capture | `recording.started`: the window picker comes to the front; 1 fps recording of the chosen window | `controller.start_capture`, `capture.py` |
| Extract screens | distinct screens, content area only, OCR. The video is deleted afterwards | `extractor.py` |
| Describe screens | every screen goes to the vision model: kind of screen, all text exactly, objects with counts and marks, chart values | `controller._stage_describe`, `ollama.describe` |
| Summarize | timeline input → template report (in parts for long meetings) → clean-up and guards | `summarizer.py`, `prompts.py` |
| Write to Meetily | back up Meetily's summary → `PUT` → read back → rename a default-named meeting | `writeback.py`, `controller._stage_publish` |

Jobs run one at a time (one model in memory). They're saved in `data/jobs/` and resume at the unfinished stage after a restart. Each stage can be retried. A recording without a capture gets a transcript-only job (Summarize + Write). One with neither speech nor screens ends as done.

## Input: one timeline

Meetily's transcript segments become `[MM:SS] Speaker: text` lines, using the speaker names set on the Speakers tab. Each screen becomes a `[SCREEN]` line at the time it appeared, shifted by the **screen offset**. The offset is the time from Meetily's recording start (the `recording.started` event) to the capture start. The two are merged by time:

```text
[00:39] Speaker 69: Name the picture. What is the first letter?
[00:42] [SCREEN] (on screen 00:42-00:52) Shows: "A slide 'Name the picture, What is the first letter?' with a bunch of bananas; the word BANANA with the B in red." OCR: "Name the picture / What is the first letter? / B / ANANA"
[00:53] Speaker 69: Find the animal, find the missing letter.
```

The exact input for any meeting is on Meetings > Model input. The Live log shows each screen's description and "screen context given to the model".

## Prompt

- **System prompt:** Meetily's final-report prompt, verbatim apart from the title line. Its rules include "Only use information present in the source text" and "If a section has no relevant info, write *None noted in this section*". Our **screen rules** (in `prompts.py`) are added on top:
  - Spoken lines are the meeting. `[SCREEN]` lines are data, used only to make a spoken line clear.
  - When a speaker reads out or answers a question shown on screen, the answer marked on screen (tick, circle) is its answer.
  - Never mention anything that appears only on screen: apps, sites, tabs, people's looks.
  - Decisions and action items come only from what a speaker said.
  - Not a work meeting (for example a video playing)? Say so in one sentence and invent nothing.
- **Template** (`core/templates/*.json`, picked in Settings). The default is **Detailed**:

  | Section | Content |
  | --- | --- |
  | Summary | what the meeting was about and what came out of it |
  | Key Points | in order; each point followed by its answer or result |
  | Questions & Answers | `Question | Answer | Asked by | Time` |
  | Steps / Demo | numbered steps of anything demonstrated or explained |
  | Work Flow | what comes first, what depends on what, who hands what to whom |
  | Key Decisions | `Decision | Reason | Time` |
  | Action Items | `Owner | Task | Due | Reference Transcript Segment | Segment Time stamp` |

  Meetily's own templates (Standard, Project Sync, Daily Standup, Retrospective, Client / Sales) are available too.
- **User prompt:** `<transcript_chunks>…</transcript_chunks>`, then a one-line reminder of the key rule. Small models follow best what they read last.

## Long meetings: parts along the timeline (Meetily's method)

Local models have small contexts, so we use the method from Meetily's `summary/processor.rs`:

1. **Budget:** tokens ≈ characters × 0.35. A part may use the model context (default 16,384) minus the answer (`max_tokens`), the system prompt, and Meetily's 300-token margin. That's about 12,900 tokens, roughly 45–60 minutes of talk with screens. "Split long meetings at (tokens)" in Settings can force smaller parts.
2. **Split** the merged timeline into parts with about 100 tokens of overlap. Cuts fall only between lines, so each part holds the speech *and* the screens of the same stretch of time. A screen still showing when a part starts is repeated at the top of that part ("still on screen"), so "as you can see…" keeps its context.
3. **Summarize each part.** This uses Meetily's chunk prompt ("capture all key points, decisions, action items, and mentioned individuals"). We add: keep times, names, every question with its answer, steps in order, tasks with owners and due dates, decisions with reasons, hand-offs.
4. **Combine** the part summaries with Meetily's combine prompt (joined with `---`). If they still don't fit, combine in groups first, over several rounds.
5. **Fill the template** from the combined text, the same way as in one pass.

Each part's summary (with its time range) and the combined summary are in the log and in `summary_parts.json` (Meetings > Summary > **Show parts**). On a 6½-minute meeting forced into 8 parts this took 13 model calls. One pass gives the better report, which is why splitting only happens when needed.

## Clean-up and guards (small models)

| Problem seen live | Guard |
| --- | --- |
| The same sentence looped until the token limit | repeat penalty (Settings), repeated sentences dropped, cut-off last sentence trimmed |
| A closing "Note: this summary is based only on…" | trailing "Note" paragraphs removed |
| Decisions and tasks invented from the screen (e.g. from a kids' quiz video) | if nobody said anything like a decision or a task ("we'll", "let's", "can you", "by Friday"…), those sections become *None noted* |
| A meeting renamed "AI-Generated Title" | the prompt no longer shows that placeholder; placeholder titles are never used to rename |
| `<think>` blocks, ```` ```markdown ```` fences | removed (as Meetily does) |

## Write-back

- **Meetily has no summary:** ours is written. Meetily stores the summary without the `# Title` line, and the title renames a meeting that still has Meetily's default name ("New Meeting …", "[Recording] …").
- **Meetily has a different one:** the job waits on **Replace with ours / Keep Meetily's / Decide later** (Live, Overview, Meetings). Choosing "Keep" is remembered by a content fingerprint, so you aren't asked again.
- **Backup:** Meetily's summary is always backed up to the run's `backups/` before a `PUT`, since `PUT` has no undo.
- **Already ours:** an identical summary (compared by fingerprint, because Meetily reformats Markdown) isn't written again.
- **`summary.completed`:** if Meetily later replaces ours, the same rules apply.
- **Editing by hand:** Meetings > Summary > **Edit**, then **Save and send to Meetily**. That sends straight away, not through the queue, and asks before replacing.
