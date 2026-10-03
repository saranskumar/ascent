# Summary templates

One JSON file per template (Settings > Model > Summary template). The file name is the template id.

| File | From |
| --- | --- |
| `detailed.json` | ours (default): key points with answers, questions & answers, steps / demo, work flow, decisions, action items |
| `standard_meeting.json`, `project_sync.json`, `daily_standup.json`, `retrospective.json`, `sales_marketing_client_call.json` | copied unchanged from [Meetily](https://github.com/Zackriya-Solutions/meetily) `frontend/src-tauri/templates/` (commit a2cb62e, MIT License, (c) 2024 Zackriya Solutions) |

Format (same as Meetily's): `{"name", "description", "sections": [{"title", "instruction", "format", "item_format"?}]}`.
`item_format` is a Markdown table header the model fills row by row. A new `.json` dropped here shows up in Settings.
