"""Messages passed between the three co-pilot parts. See contracts/README.md.

1 -> 2   Segment       live transcript (ws /transcript)
2 -> 3   Suggestion    images the host might want to show (ws /suggestions)
3 -> 2   Select        host put an image on the canvas
3 -> 2   Dismiss       host waved a suggestion away

`seq` is set by the stream server when a message is published; producers leave it at 0.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields


@dataclass
class Segment:
    id: str                      # stable per segment; a later message with the same id replaces the text
    text: str
    start: float                 # seconds from the start of the meeting
    end: float
    final: bool = True           # False = partial, may still change. The engine only acts on finals
    speaker: str | None = None
    seq: int = 0
    type: str = "segment"


@dataclass
class Image:
    id: str
    url: str                     # full-size image, served by the engine over http
    thumb_url: str               # small version for the overlay
    width: int = 0
    height: int = 0
    source: str = "local"        # "local" (team image folder) | "web"
    source_ref: str = ""         # file path or page url, for credit/debugging
    caption: str = ""


@dataclass
class Suggestion:
    id: str
    topic: str
    images: list[Image]
    reason: str = ""             # why the engine thinks a visual helps here (shown as a hint)
    priority: str = "ambient"    # "ambient" (show quietly) | "elevated" (show prominently)
    source_segment_ids: list[str] = field(default_factory=list)
    expires_in: float = 45       # seconds; the overlay may hide it after this
    seq: int = 0
    type: str = "suggestion"


@dataclass
class Select:
    suggestion_id: str
    image_id: str
    type: str = "select"


@dataclass
class Dismiss:
    suggestion_id: str
    type: str = "dismiss"


_TYPES = {cls.__dataclass_fields__["type"].default: cls
          for cls in (Segment, Suggestion, Select, Dismiss)}


def to_dict(msg) -> dict:
    return msg if isinstance(msg, dict) else asdict(msg)


def dumps(msg) -> str:
    return json.dumps(to_dict(msg), ensure_ascii=False)


def from_dict(d: dict):
    """Dict -> dataclass by its "type". Unknown types or extra keys are tolerated."""
    cls = _TYPES.get(d.get("type"))
    if cls is None:
        return d
    known = {f.name for f in fields(cls)}
    kw = {k: v for k, v in d.items() if k in known}
    if cls is Suggestion:
        kw["images"] = [Image(**{k: v for k, v in i.items()
                                 if k in Image.__dataclass_fields__})
                        for i in kw.get("images", [])]
    return cls(**kw)


def loads(raw: str):
    return from_dict(json.loads(raw))
