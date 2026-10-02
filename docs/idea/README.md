# Meetily Visual Copilot - Idea

Detailed problem/solution write-up used for the PPT: **[problem-solution.md](problem-solution.md)** (adapted from `drops/initial_idea.md`).

## Core framing (one sentence)

**Meetily today has audio context only.** We add **two layers**:

1. **Visual context** - screen/video so Meetily can see the meeting and ground notes  
2. **Meeting assist** - Visual Copilot so Meetily can help live (suggest + share visuals)

## Positioning

We are **not** building a standalone vision app, image generator, or OCR demo.

We are building **two layers on Meetily**:

| Layer | Name | Job |
| --- | --- | --- |
| 1 | Visual context | See screen/video → fuse with transcript → grounded notes |
| 2 | Meeting assist | Detect visual need → retrieve diagram → 1-click share |

| Meetily today | Meetily + our layers |
| --- | --- |
| Audio only: Listen → Transcribe → Summarize | Audio + visual context + meeting assist |

**Track 1 fit:** Automate a workflow using Meetily’s existing meeting intelligence (transcript, context, Agent API), not an unrelated AI application.

## Problem

Meetings are not purely verbal. Speakers say things like:

- "Let me explain this architecture."
- "As you can see on this graph..."
- "We need a diagram for this."

**Current friction:** stop talking → search Google/files → open/share → resume. That breaks communication flow.

**After the meeting:** transcript + summary exist, but turning discussion into useful visual docs is still mostly manual.

**Insight:** If Meetily already understands what is being discussed, it should understand when a visual could help - and ground vague references like "as seen here" in what is actually on screen.

## Solution (bidirectional, 100% local)

Extend Meetily into a bidirectional multimodal assistant that runs locally without compromising privacy.

### Feature 1 - Screen-Grounded Multimodal Summarization (Inbound)

Audio-only assistants miss slide metrics, code diffs, and diagrams. Speakers use deictic references ("as seen here") that leave summaries vague.

| Mechanism | What it does |
| --- | --- |
| Adaptive keyframe extraction | Background observer + dHash detects slide/window transitions; changed frames go to local OCR (Apple Vision / Tesseract) |
| Chronological timeline fusion | Interleaves OCR / UI milestones into Meetily’s timestamped transcript before Ollama |
| Grounded synthesis | LLM cross-references verbal references against on-screen content → exact metrics and action items |

**Pitch line:** We ground Meetily’s understanding in the visual context of the meeting (OCR is an implementation detail).

### Feature 2 - Real-Time Contextual Visual Copilot (Outbound)

Speakers explain concepts verbally without ready visuals, then interrupt the call to search.

| Mechanism | What it does |
| --- | --- |
| Real-time topic extractor | In-memory buffer on Whisper stream detects explanatory intent |
| Local asset & diagram retrieval | Indexed local repo / vector cache surfaces 2–3 relevant visuals |
| 1-click share to meeting | Preview, copy to clipboard, or broadcast to screen share |

## Killer user journey (one scenario)

**Software architecture meeting**

1. Developer: "Let's discuss our three-tier architecture."
2. Meetily transcribes and keeps context.
3. Visual Copilot detects need → suggests architecture diagram.
4. Developer clicks **Share**.
5. Meeting continues without search friction.
6. After: grounded notes with visuals, decisions, and action items.

## Differentiation (judge Q&A)

| Question | Answer |
| --- | --- |
| Why not ChatGPT? | General AI waits for you to stop and ask. We are grounded in the live Meetily stream and act when a visual would help. |
| Why not Google Images? | The problem is not finding an image - it is finding the right visual without interrupting the conversation. |
| Why not an AI slide generator? | We assist **during** the meeting (Copilot) and **after** (grounded visual summary), not only post-hoc. |

## What this is / is not

| Not | Yes |
| --- | --- |
| AI image generator | Visual workflow agent for Meetily |
| Generic AI slide generator | Meeting-context → visual need → assist → act |
| "We added OCR" | Screen-grounded meeting intelligence |
| Local vision model for its own sake | Agentic automation on Meetily intelligence |

## Future scope

Today: live visual suggestions (Preview + Share)  
→ Next: meeting-grounded slide packs  
→ Then: automatic visual documentation  
→ Future: Meetily → visuals → slides → docs → Slack/Discord → follow-up

**Vision:** Meeting intelligence that does not only remember - it turns conversation into action.
