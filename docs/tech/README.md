# Meetily Visual Copilot - Technical Notes

## Architecture (high level)

```text
                    MEETILY CORE
         recording · Whisper · context · Agent API
                          │
           ┌──────────────┴──────────────┐
           ▼                             ▼
   INBOUND (Summarization)      OUTBOUND (Live Copilot)
   Screen → dHash → OCR         Whisper stream → topic/intent
        → timeline fusion            → local retrieval
        → Ollama synthesis           → side panel (2–3)
           │                             │
           ▼                             ▼
   Entity-grounded notes        Preview → 1-click Share
```

Shared layer: **multimodal context** (speech + screen).

## Stack

| Layer | Technology |
| --- | --- |
| Meeting core | [Meetily](https://meetily.ai/) (Zackriya) - transcript, summaries, Agent API / MCP |
| Speech | Whisper (via Meetily / local) |
| Keyframes | Screen capture + difference hashing (dHash) |
| OCR | Apple Vision / Tesseract (changed frames only) |
| LLM | Ollama (local grounded synthesis + intent) |
| Retrieval | Indexed local `/assets` + optional embeddings / vector cache |
| UI | Meetily-adjacent side panel / desktop companion (Tauri candidates) |
| Privacy | Local-first; no cloud required for the visual pipeline |

## Agent loop

1. **Observe** - live transcript, meeting context, screen context  
2. **Reason** - topic, explanatory intent, visual type needed  
3. **Retrieve / Create** - local assets (or generate if appropriate)  
4. **Act** - Preview · Copy · Share · Save  

## 30-hour MVP phases

| Phase | Goal |
| --- | --- |
| 1 | Meetily → app: transcript / context via Agent API or export hooks |
| 2 | Context extract: topic, entities, explanatory intent → visual type |
| 3 | Local retrieval: small curated `/assets` (architecture, algorithms, patterns) |
| 4 | Copilot UI: suggestion card with Preview + Share |
| 5 (stretch) | Screen grounding: keyframe + OCR on timeline |
| 6 (stretch) | Context-aware summary incorporating visual milestones |

## References

- Meetily: https://meetily.ai/  
- Docs: https://docs.meetily.ai/  
- Agent API: https://docs.meetily.ai/integrations/agent-api  
- Repo: https://github.com/Zackriya-Solutions/meetily  
