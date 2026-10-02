# Track 1: Meetily — Architecture & Integration Brief

Summary of requirements, integration architecture, security constraints, and contribution lifecycle for **ASCENT ’26 Track 1: Meetily**.

---

## 1. Core Mental Model & Scope
- **Meetily Pro** acts as the local intelligence source:
  - Local-first transcription & diarization.
  - Generates timestamped transcripts, summaries, speaker insights.
  - Exposes an **Automation / Agent API** on loopback (`http://127.0.0.1:8420`).
- **Our Solution**:
  - Lives in our own repository.
  - Consumes Meetily events and data via **Webhooks**, **HTTP REST API**, **CLI**, or **MCP**.
  - Transforms meeting intelligence into high-value downstream workflows.
  - Provides a standard `manifest.yaml` matching the `meetily-workflows` catalog schema.

---

## 2. Integration Interfaces

| Surface | Purpose / Mechanism |
|---|---|
| **Webhooks** | Event-driven notifications (e.g., `summary.completed`, `recording.stopped`, `job.completed`). Thin payloads with HMAC signatures. |
| **HTTP API** | Local REST gateway (`http://127.0.0.1:8420`). Used to fetch transcripts, summaries, speaker details, or trigger actions. |
| **SSE (Server-Sent Events)** | Bounded waits on job/transcription/summary terminal states without polling. |
| **CLI (`meetily-pro`)** | Shell/script automation. |
| **MCP Server** | Model Context Protocol integration for AI agent workflows (Claude, Cursor, custom agents). |

---

## 3. Mandatory 5-Stage Event Pattern
Any event-driven workflow must implement:
1. **Subscribe**: Register webhook destination for target event (e.g., `summary-ready`).
2. **Verify**: Validate incoming webhook payload HMAC signature with the shared secret.
3. **Deduplicate**: Check `event_id` idempotency (handle at-least-once delivery safely).
4. **Fetch**: Thin payload contains only `resource.id`; make authenticated API call to retrieve actual summary/transcript.
5. **Act**: Execute business logic / integration side effect.

---

## 4. Permission Scopes
- `read`: Read meetings, transcripts, summaries, status, exports, webhooks. (Default loopback credential).
- `record`: Start/stop/pause/resume recording (implies `read`).
- `write`: Rename meetings, edit speaker labels, set/regenerate summaries (implies `read`).
- `delete`: Delete meetings.

---

## 5. Submission Deliverables
1. **Working Implementation**: Standalone, robust code with clear setup and execution commands.
2. **Catalog Manifest (`manifest.yaml`)**: Spec-compliant metadata pointing to the repository.
3. **Engineering Checklist Compliance**:
   - HMAC verification + idempotent deduplication.
   - Least-privilege scope declaration.
   - Zero hardcoded secrets (environment variables only).
   - Error handling for offline Meetily desktop instances.
