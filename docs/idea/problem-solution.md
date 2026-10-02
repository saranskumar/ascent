# Problem and Solution (from initial idea)

Source material adapted from `drops/initial_idea.md` for **Track 1: Automate Workflow with Meetily.ai**.

Product name in submission: **Meetily Visual Copilot**.

## Core framing

**Meetily today = audio context only** (listen → transcribe → summarize).

**We add two layers on Meetily:**

| Layer | Name | What it does |
| --- | --- | --- |
| 1 | **Visual context** | Screen/video capture → keyframes → OCR → fuse with transcript → grounded notes |
| 2 | **Meeting assist** | Intent from live transcript → retrieve visuals → side panel → 1-click share |

Layer 1 gives Meetily **eyes**. Layer 2 gives it **hands** in the meeting.

---

## Problem (detailed)

### Why meetings break without visuals

Meetings are multimodal. Speakers constantly rely on things that are **shown**, not only said:

- "Let me explain this architecture."
- "As you can see on this graph..."
- "Look at this split keyboard layout."
- "...so we switched to a cycloidal drive, like this."
- "As you can see, this error threshold is completely breached."

The visual is part of the communication itself. Without it, the explanation is incomplete.

### Pain 1 - Live friction (outbound gap)

When a speaker needs a diagram or reference mid-call, the current workflow is:

```text
Explain concept → realize visual is needed → stop talking
→ search Google / files / slides → open / download → share → resume
```

That friction:

- breaks conversational momentum
- forces tab-switching and window dragging during screen share
- exposes privacy risk (messy desktop, wrong windows)
- leaves the audience waiting

Existing tools can transcribe. They do **not** proactively prepare the right visual from live meeting context.

### Pain 2 - Vague notes after the meeting (inbound gap)

Audio-only assistants miss critical on-screen context:

- slide metrics
- code diffs
- architecture diagrams
- dashboard spikes and thresholds

When speakers use **deictic references** ("as seen here", "this spike", "this architecture"), the transcript alone cannot resolve what "here" meant.

Example of the failure mode:

| What was said | What was on screen | What a shallow summary keeps |
| --- | --- | --- |
| "This error threshold is completely breached." | Datadog: auth-worker · HTTP 504: 12.4% · Threshold: 1.0% | Vague: "error threshold breached" |
| "We need to switch to this architecture pattern." | Circuit Breaker Pattern diagram | Vague: "switch architecture" |

What we need instead: **"p99 / HTTP 504 hit 12.4% on auth-worker (threshold 1.0%); adopt circuit breaker."**

### The clipboard test (summary quality bar)

A good post-meeting note must:

- never say "as seen in the chart above" or "refer to slide 4"
- translate visuals into **explicit facts**
- remain 100% understandable when pasted into Slack, email, or Jira **with zero images attached**

Today, Meetily (and most meeting AIs) stop at transcript → summary. They do not ground that summary in what was on screen, and they do not automate visual assistance during the call.

### Core insight

> If Meetily already understands what is being discussed, it should understand **when a visual could help**, and it should resolve **"as seen here"** against the actual screen.

---

## Solution (detailed)

Extend Meetily into a **bidirectional multimodal assistant** that runs **100% locally**.

### Outbound - Real-Time Contextual Visual Copilot

**Job:** detect explanatory intent from Meetily's live transcript and surface ready-to-share visuals without interrupting the meeting.

Mechanisms (product + engineering detail from initial idea):

| Mechanism | Detail |
| --- | --- |
| Topic / intent extraction | Rolling focus on nearby noun phrases; linguistic anchors like "look at this", "like a...", "here is the..." |
| Explanatory pause cues | Prosodic pause after a concrete noun (~600-1000ms) elevates a suggestion |
| Local retrieval | Indexed local assets first; optional curated diagram/search cache; quality filters (reject banners, tiny icons, bad MIME) |
| Side panel | 2-3 candidates: ghost (ambient) vs elevated (focused) |
| 1-click act | Preview, copy, or share/broadcast into the meeting (screen share / stage) |

Human stays in control. AI prepares; user shares.

### Inbound - Screen-Grounded Multimodal Summarization

**Job:** ground Meetily's notes in visual context so deictic speech becomes entity-level facts.

| Mechanism | Detail |
| --- | --- |
| Adaptive keyframes | Background observer + dHash only when slide/window changes |
| Local OCR | Apple Vision / Tesseract on changed frames |
| Timeline fusion | Interleave OCR milestones with Meetily timestamped transcript |
| Grounded synthesis (Ollama) | Cross-reference speech against screen text → actionable notes |
| One-anchor rule | At most 0-1 supporting image per bullet (dwell time + semantic overlap) |

Interleaved ingestion example:

```text
[11:04:12] Speaker: "As you can see, this error threshold is completely breached."
Context on screen: { window: "Datadog", text: "auth-worker | HTTP 504: 12.4% | Threshold: 1.0%" }

[11:04:30] Speaker: "We need to switch to this architecture pattern instead."
Context on screen: { stage_projection: "Circuit Breaker Pattern" }
```

LLM directive (summary): resolve deictic references into factual assertions; output clean Markdown that passes the clipboard test.

### Product character

| Meetily today | Meetily + Visual Copilot |
| --- | --- |
| Listen → Transcribe → Summarize | Listen → Understand → Assist → Act |

This is a **workflow automation on Meetily**, not a standalone vision model.

---

## Mapping to registration slides

| Slide | Uses this doc |
| --- | --- |
| 2 · Problem | Pain 1 + Pain 2 + deictic failure + friction flow |
| 3 · Solution | Inbound / Outbound mechanisms |
| 5 · Stack | Whisper, dHash, OCR, Ollama, local retrieval, Meetily |
| 6 · Journey | Architecture meeting + grounded after-notes |
| 7 · Architecture | Dual-lane inbound/outbound on Meetily core |
