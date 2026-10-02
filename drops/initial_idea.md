
# Product Architecture Specification: AI Visual Co-Presenter & Visually-Grounded Summaries

**Document Version:** 1.0.0

**Target Platform:** Tauri v2 + Rust Core, Next.js / TypeScript / Tailwind CSS

**Execution Environment:** Local-First (Whisper.cpp, Apple Vision / Tesseract, Ollama)

---

## 1. Executive Overview

This specification defines two tightly coupled features designed to revolutionize live remote presentations and post-meeting documentation:

1. **The Real-Time Visual Co-Presenter:** An ambient, dual-window desktop companion that listens to spoken dialogue, detects visual intent and prosodic pauses, and surfaces verified, presentation-grade images (diagrams, schematics, clean physical cutouts) for instant single-keystroke projection to an audience.
2. **Visually-Grounded Executive Summaries:** A post-meeting synthesis pipeline that leverages interleaved on-screen OCR and stage projection logs to resolve visual deictic references into dense, self-contained plain text, accompanied by a non-intrusive split-pane visual reel strictly capped at one anchor image per point.

---

## 2. Feature 1: Real-Time Visual Co-Presenter

### 2.1 The Dual-Window Presentation Model

To eliminate the clutter, latency, and privacy risks of live tab-switching and window dragging, the UI is split across two decoupled Tauri windows:

* **Audience Stage Window (`/stage`):**
* Target for screen-sharing software (Zoom, Google Meet, Microsoft Teams).
* Frameless, borderless, zero UI chrome.
* Renders visual assets centered inside an adaptive canvas container (subtle dark slate `#0f172a`, rounded corners, ambient drop-shadow) with smooth crossfades.


* **Presenter Cockpit Window (`/cockpit`):**
* Displayed on the presenter's private screen.
* Displays a linear, horizontal reel:
* **Left (History Reel):** Dimmed (40%) stack of past projected visuals for instant backwards navigation (`Left Arrow`).
* **Center (Active Card):** Exactly what is currently live on the Audience Stage.
* **Right (Next Up / Filmstrip):** Incoming suggestions (Ghost or Elevated state).





### 2.2 Intent Detection & Linguistic Grounding

The pipeline avoids naive keyword matching and broad conversation history to prevent displaying stale context.

1. **Immediate Focus Head Binding:**
* A rolling 3–5 token window captures the syntactic head noun immediately adjacent to the trigger.
* *Forward-pointing (Cataphoric):* *"Look at [this split keyboard layout]."*
* *Backward-pointing (Anaphoric):* *"...so we switched to [a cycloidal drive], like this."*


2. **The Two-Tier Elevation Model:**
* **Tier 1: Ambient Ghost State:** During natural, uninterrupted speech, incoming concrete nouns populate the candidate slot at 30% opacity with zero intrusive audio/visual chimes.
* **Tier 2: Elevated / Focused State:** The card smoothly escalates to 100% opacity with a subtle border glow when:
* An explicit linguistic anchor is detected (*"look at this"*, *"like a..."*, *"here is the..."*).
* **Prosodic Pause:** The speaker delivers a concrete noun and pauses for **600ms – 1000ms** (acoustic emphasis).




3. **Leading-Edge Micro-Prefetch (Queue Depth = 1):**
* Speculative prefetching fires *only* for the noun phrase currently crossing the speaker's lips. If the speaker changes direction without elevating the topic, the speculative cache entry is discarded within 3–4 seconds.
* If a sudden semantic shift occurs upon an intent marker, a Just-in-Time (JIT) query executes (<250ms latency).



### 2.3 Visual Retrieval, Filtering, and Studio Formatting

To ensure every fetched image looks like an intentional presentation slide:

* **Dual-Source Pipeline:**
* **Local Assets (`~/Assets`):** In-memory fuzzy path/metadata index (<50ms retrieval).
* **Curated Web Engine (Brave Image Search / Serper / Wikimedia Commons):** Modifiers bias results toward presentation-ready assets:
* *Everyday Objects:* `[Entity] + "studio product photography isolated white background OR transparent PNG"`
* *Technical Systems:* `[Entity] + "architecture diagram transparent OR schematic"`




* **Local Proxy & Verification Guardrail (Rust):**
* Images are fetched asynchronously by Rust via `reqwest` and stored in temporary local storage (`~/.cache/meetily/`).
* Bypasses third-party CORS, CORB, and hotlink protection.
* Rejects images failing quality filters: aspect ratios > 2.5:1 (banners), dimensions < 400px (icons/avatars), or invalid MIME types.



### 2.4 Control Scheme & Safety Ergonomics

* **`Space` or `1` / `2` / `3`:** Project the staged candidate to the Audience Stage.
* **`Left Arrow` / `Right Arrow`:** Step backward and forward through the visual reel.
* **`B` or `Esc` (Safety Blind / Panic Shutter):** Instantly triggers a soft-edge shutter wipe on the Stage into a neutral branded slate without interrupting screen-sharing.
* **Drop Anywhere / `Cmd + V` (Scratchpad):** The entire Cockpit window serves as a drop target. Dragging a screenshot or pressing `Cmd + V` instantly stages that asset into Slot 1 at 100% elevation.

---

## 3. Feature 2: Visually-Grounded Executive Summaries

### 3.1 The "Clipboard Test" Philosophy

A post-meeting summary must remain a portable, executive-grade document:

* **Strict Decoupling:** The generated text must never reference visuals directly (*"as seen in the chart above"* or *"refer to slide 4"* are forbidden).
* **Information Translation:** Visual context (from screen OCR or projected Stage slides) is translated into dense, explicit factual statements (e.g., translating *"this spike here"* into *"p99 read latency surged to 420ms"*).
* If copied into an email, Jira ticket, or Slack channel, the plain text must be 100% understandable without images attached.

### 3.2 UI Structure: Split-Pane Inspector

* **Left Pane (Plain Markdown Text):** Distraction-free, scannable text summary with timestamp anchors (`[12:04]`). Includes a top-level `[Copy Summary]` button that extracts pure Markdown.
* **Right Pane (Synchronized Visual Context Reel):** An optional, collapsible side drawer that scrolls in lockstep with the text summary, displaying the single verified image anchor associated with that section.

### 3.3 The One-Anchor Selection Law

A summarized bullet condensing 5 minutes of speech and 4 slides must not be overloaded with images.

* **Hard Rule:** **0 or 1 image per bullet point.** Default to 0.
* **Candidate Scoring Engine:** When a topic window contains multiple slides, the single winning anchor frame is selected by:
1. **Dwell Time:** Slide with the longest active presentation duration (>45s).
2. **Semantic Overlap:** High keyword/embedding match between the final generated bullet point and the slide's OCR text.
3. **Density Threshold:** Slides with fewer than 4 distinct words (title/transition slides) or low visual complexity are disqualified.



### 3.4 Interleaved LLM Ingestion Contract

Visual context is fed into the local LLM (Ollama) as interleaved structural metadata:

```text
[11:04:12] Speaker: "As you can see, this error threshold is completely breached."
Context on screen: { id: "SLIDE_4", window: "Datadog", text: "Service: auth-worker | HTTP 504: 12.4% | Threshold: 1.0%" }

[11:04:30] Speaker: "We need to switch to this architecture pattern instead."
Context on screen: { id: "STAGE_REF_1", stage_projection: "Circuit Breaker Pattern" }

```

**LLM System Directive:**

> *"Resolve deictic references into factual assertions. Output clean markdown. For each bullet point, append at most one supporting visual anchor identifier using the syntax `<!-- anchor: SLIDE_ID -->` only when essential for technical verification, otherwise output `<!-- anchor: none -->`."*

The frontend parses and strips these comments from the text buffer, rendering clean Markdown on the left while populating the side reel on the right.

---

## 4. End-to-End System Architecture

```
                                  [PRESENTER MICROPHONE]
                                            │
                                            ▼
                                   [Whisper.cpp (STT)]
                                            │
                             Timestamped Speech Tokens
                                            │
                     ┌──────────────────────┴──────────────────────┐
                     ▼                                             ▼
        [Leading-Edge Noun Parser]                    [Prosodic Pause Detector]
                     │                                             │
         Subject Query Extraction                           Silence >700ms
                     │                                             │
                     ▼                                             ▼
         [Image Search & Proxy] ───────────────► ┌─────────────────────────────────┐
     (Local Assets / Brave / Wikimedia)          │    PRESENTER COCKPIT (/cockpit) │
                     │                           │  - History Reel (Left Arrow)    │
            Cached Disk Asset                    │  - Ghost / Elevated Cards       │
                     │                           │  - Safety Blind Toggle ('B')    │
                     └─────────────────────────► └────────────────┬────────────────┘
                                                                  │
                                                        [Space / 1] Keypress
                                                                  │
                                                                  ▼
                                                      [AUDIENCE STAGE (/stage)]
                                                        (Clean Shared Canvas)
                                                                  │
                                                       (Meeting Ends / Stop)
                                                                  │
                                                                  ▼
[Screen Capture OCR (xcap)] ──► [Interleaved Buffer] ◄── [Projected Stage Logs]
                                        │
                                        ▼
                               [Ollama LLM Engine]
                                        │
                           Grounded Summary + Anchors
                                        │
                                        ▼
                         ┌──────────────────────────────┐
                         │      SPLIT-PANE VIEWER       │
                         │ Left: Pure Portable Markdown │
                         │ Right: Single Anchor Reel    │
                         └──────────────────────────────┘

```

---

## 5. Technical Deliverable Summary

| Component | Technology | Role |
| --- | --- | --- |
| **Shell & Core** | Tauri v2 (`src-tauri`), Rust | Multi-window routing, IPC events, hotkey management, process isolation. |
| **Capture & OCR** | `xcap`, Apple Vision / `tesseract-rs`, `image_hasher` | Screen keyframe extraction, perceptual dHash deduplication, OCR parsing. |
| **Audio & Speech** | Whisper.cpp (Metal / CUDA bindings) | Low-latency local audio transcription, VAD pause monitoring. |
| **Search & Proxy** | `reqwest`, `tokio::fs` | Local asset indexing, web search query formulation, download proxying, size/ratio validation. |
| **Frontend UI** | Next.js 14, Tailwind CSS, TypeScript | Cockpit HUD, borderless Stage projection window, Split-Pane summary inspector. |
| **Inference** | Ollama (`llama3.1:8b` or `qwen2.5:7b`) | Post-meeting multimodal plain-text synthesis and single-anchor frame mapping. |