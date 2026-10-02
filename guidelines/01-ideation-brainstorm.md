# GD 01: Ideation & Concept Brainstorming

- **Track**: ASCENT ’26 — Track 1: Meetily
- **Focus**: Building a high-impact add-on / workflow for Meetily Pro
- **Architecture Pattern**: `Subscribe → Verify HMAC → Deduplicate → Fetch API → Act`

---

## 💡 Evaluation Criteria for a Winning Hackathon Entry
1. **Solves Real Friction**: Transcripts and summaries often rot in desktop folders. The value comes from turning conversation into automated execution.
2. **Deep API Utilization**: Uses Meetily Pro's unique strengths (local privacy, speaker identification, webhooks, REST API, write-back capability, and MCP).
3. **Robust Engineering**: Implements proper HMAC verification, deduplication, retry safety, and zero secret leaks.
4. **Wow Factor & Demo-ability**: Instant end-to-end visual feedback when a simulated meeting ends.

---

## 🚀 Candidate Product Concepts

### Option A: **Meetily SprintSync (Meeting → Linear/GitHub Issues & Standup Digest)**
- **Target Audience**: Engineering teams, product managers, hackathon teams.
- **Workflow**:
  1. Triggered on `summary-ready`.
  2. Extracts actionable technical decisions, bug reports, and features from the transcript/summary.
  3. Maps speaker diarization labels directly to GitHub/GitLab usernames.
  4. Automatically generates structured GitHub/Linear issues with acceptance criteria and milestones.
  5. Uses Meetily's `write` scope to append created issue links back into the Meetily meeting notes.
- **Why it wins**: High utility, immediate visual proof, bidirectional Meetily API usage (`read` + `write`).

---

### Option B: **Meetily Cortex (Cross-Meeting Knowledge Graph & Commitment Tracker)**
- **Target Audience**: Founders, managers, busy professionals attending 10+ meetings a week.
- **Workflow**:
  1. Every meeting summary updates a local knowledge base / vector index (keeping Meetily's privacy-first ethos).
  2. Tracks "Who committed to do what by when" across all historical meetings.
  3. Conflict detection: Detects contradictory decisions between meetings (e.g., *"In Monday's sync, Alex agreed to launch on the 10th, but in Thursday's sync, launch was moved without Alex's awareness"*).
  4. Exposes an interactive web dashboard + MCP tool for Claude/Cursor to query cross-meeting memory.
- **Why it wins**: Novel, solves long-term meeting amnesia, upholds 100% local privacy.

---

### Option C: **Meetily Pulse & Action Dispatcher (Multi-Channel Follow-ups & Calendar Automation)**
- **Target Audience**: Client-facing teams, consultants, sales, project leads.
- **Workflow**:
  1. Triggered on `summary-ready`.
  2. Generates tailored 1-click follow-up email drafts for each individual attendee highlighting only the items assigned to them.
  3. Automatically schedules calendar events / reminder blocks for detected deadlines.
  4. Posts executive tl;dr summaries to Slack / Discord / Telegram with direct deep-links.
- **Why it wins**: Extremely polished, immediate practical utility, easy to showcase live.

---

## 📊 Comparison Matrix

| Concept | Feasibility (Hackathon) | Innovation / Wow Factor | Meetily API Depth | Privacy Alignment |
|---|---|---|---|---|
| **A. SprintSync** | ⭐⭐⭐⭐⭐ (Very High) | ⭐⭐⭐⭐ (High) | ⭐⭐⭐⭐⭐ (Read + Write) | ⭐⭐⭐⭐ |
| **B. Cortex** | ⭐⭐⭐⭐ (Moderate-High) | ⭐⭐⭐⭐⭐ (Very High) | ⭐⭐⭐⭐ (Read + MCP) | ⭐⭐⭐⭐⭐ (100% Local) |
| **C. Pulse** | ⭐⭐⭐⭐⭐ (Very High) | ⭐⭐⭐⭐ (High) | ⭐⭐⭐⭐ (Read) | ⭐⭐⭐⭐ |
