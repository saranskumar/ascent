"""Web image search. Each provider returns Candidates in its own relevance order.

Providers, tried in order until one returns results (override with COPILOT_SEARCH=ddg,wikimedia,...):
    serper     Google Images via serper.dev     needs SERPER_API_KEY
    ddg        DuckDuckGo images via `ddgs`      keyless, unofficial (can be rate-limited)
    wikimedia  Wikimedia Commons API             keyless, good for diagrams, weak relevance
Only the search query leaves the machine, never the transcript.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

UA = "ascent-copilot/0.1 (hackathon prototype; local meeting assistant)"


@dataclass
class Candidate:
    url: str
    page: str = ""
    title: str = ""
    width: int = 0          # 0 = unknown until downloaded
    height: int = 0
    thumb: str = ""
    provider: str = ""

    @property
    def domain(self) -> str:
        return urlparse(self.page or self.url).netloc.lower().removeprefix("www.")


def serper(query: str, n: int) -> list[Candidate]:
    key = os.environ.get("SERPER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("SERPER_API_KEY not set")
    req = urllib.request.Request("https://google.serper.dev/images",
                                 data=json.dumps({"q": query, "num": n}).encode(),
                                 headers={"X-API-KEY": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        d = json.load(r)
    return [Candidate(url=i["imageUrl"], page=i.get("link", ""), title=i.get("title", ""),
                      width=i.get("imageWidth") or 0, height=i.get("imageHeight") or 0,
                      thumb=i.get("thumbnailUrl", ""), provider="serper")
            for i in d.get("images", []) if i.get("imageUrl")]


def ddg(query: str, n: int) -> list[Candidate]:
    from ddgs import DDGS
    rows = DDGS(timeout=8).images(query, max_results=n, safesearch="moderate")
    return [Candidate(url=r["image"], page=r.get("url", ""), title=r.get("title", ""),
                      width=int(r.get("width") or 0), height=int(r.get("height") or 0),
                      thumb=r.get("thumbnail", ""), provider="ddg")
            for r in rows if r.get("image")]


def wikimedia(query: str, n: int) -> list[Candidate]:
    p = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
         "gsrsearch": f"{query} filetype:bitmap|drawing", "gsrlimit": n,
         "prop": "imageinfo", "iiprop": "url|size|mime", "iiurlwidth": 1280}
    req = urllib.request.Request("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(p),
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=8) as r:
        pages = (json.load(r).get("query") or {}).get("pages") or {}
    out = []
    for pg in sorted(pages.values(), key=lambda p: p.get("index", 0)):
        ii = (pg.get("imageinfo") or [{}])[0]
        url = ii.get("thumburl") or ii.get("url")   # thumburl renders SVG diagrams as PNG
        if url:
            out.append(Candidate(url=url, page=ii.get("descriptionurl", ""),
                                 title=pg.get("title", "").removeprefix("File:"),
                                 width=ii.get("thumbwidth") or ii.get("width") or 0,
                                 height=ii.get("thumbheight") or ii.get("height") or 0,
                                 thumb=url, provider="wikimedia"))
    return out


PROVIDERS = {"serper": serper, "ddg": ddg, "wikimedia": wikimedia}


def provider_order() -> list[str]:
    env = os.environ.get("COPILOT_SEARCH")
    if env:
        return [p.strip() for p in env.split(",") if p.strip() in PROVIDERS]
    order = ["ddg", "wikimedia"]
    if os.environ.get("SERPER_API_KEY", "").strip():
        order.insert(0, "serper")
    return order


_benched: dict[str, float] = {}


def search(query: str, n: int = 20, skip: set[str] = frozenset()) -> tuple[list[Candidate], str]:
    """First provider (not in `skip`) that returns results. Returns (candidates, provider name)."""
    for name in provider_order():
        if name in skip or time.monotonic() < _benched.get(name, 0):
            continue
        try:
            res = PROVIDERS[name](query, n)
        except Exception as e:
            _benched[name] = time.monotonic() + 60
            print(f"[search] {name} failed, benched 60s: {type(e).__name__}: {str(e)[:120]}")
            continue
        if res:
            return res, name
    return [], ""
