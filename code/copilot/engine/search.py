"""Web image search. Each provider returns Candidates in its own relevance order.

Providers, in preference order (override with COPILOT_SEARCH=ddg,wikimedia,...):
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


KIND_WORDS = {"diagram", "photo", "chart", "illustration", "architecture", "sequence", "picture",
              "image", "graph"}


def wikimedia(query: str, n: int) -> list[Candidate]:
    # Commons search needs every word to match file titles/descriptions, so drop the "kind" words
    # the LLM appends, and if that still finds nothing, retry with just the first three words.
    words = [w for w in query.split() if w.lower() not in KIND_WORDS] or query.split()
    keys = {w.lower().rstrip("s") for w in words if len(w) > 3}
    for q in dict.fromkeys([" ".join(words), " ".join(words[:3])]):
        # its full-text search also matches descriptions, which drags in unrelated photos;
        # keep files whose title shares a word with the query
        res = [c for c in _wikimedia(q, n)
               if keys & {w.lower().rstrip("s") for w in c.title.replace("_", " ").split()}]
        if res:
            return res
    return []


def _wikimedia(query: str, n: int) -> list[Candidate]:
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
GRACE = float(os.environ.get("COPILOT_SEARCH_GRACE", 1.5))  # after the first provider answers, wait this long for the other


def _one(name: str, query: str, n: int) -> list[Candidate]:
    try:
        return PROVIDERS[name](query, n)
    except Exception as e:
        _benched[name] = time.monotonic() + 60
        print(f"[search] {name} failed, benched 60s: {type(e).__name__}: {str(e)[:120]}")
        return []


def search_all(query: str, n: int = 20) -> tuple[list[Candidate], list[str]]:
    """Race the first two usable providers: when one answers, give the other GRACE seconds, then
    merge whatever is in (provider order, no duplicate URLs). Later providers are only asked if
    those came back empty. Returns (candidates, ["provider:count", ...])."""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    order = [p for p in provider_order() if time.monotonic() >= _benched.get(p, 0)]
    first, rest = order[:2], order[2:]
    results: dict[str, list[Candidate]] = {}
    if first:
        ex = ThreadPoolExecutor(len(first))
        futs = {ex.submit(_one, name, query, n): name for name in first}
        done, pending = wait(futs, return_when=FIRST_COMPLETED)
        if pending and not any(f.result() for f in done):
            done, pending = wait(futs)                    # first one was empty: wait for the other
        elif pending:
            more, pending = wait(pending, timeout=GRACE)
            done |= more
        ex.shutdown(wait=False, cancel_futures=True)      # a slow straggler is simply ignored
        results = {futs[f]: f.result() for f in done}
    if not any(results.values()):
        for name in rest:
            results[name] = _one(name, query, n)
            if results[name]:
                break
    seen, merged = set(), []
    for name in order:
        for c in results.get(name, []):
            if c.url not in seen:
                seen.add(c.url)
                merged.append(c)
    return merged, [f"{name}:{len(results[name])}" for name in order if name in results]
