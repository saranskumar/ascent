"""Download search candidates, keep the ones that look presentable, save full + thumbnail JPEGs.

Filters (drops/initial_idea.md §2.3): no banners (aspect > 2.5), no icons (too small), must decode
as an image, no stock-photo sites (watermarks), no near-blank images, no near-duplicates.
"""
from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from PIL import Image as PImage, ImageOps, ImageStat

from .search import Candidate

BLOCKED_DOMAINS = (  # watermarked stock, or pages that serve login walls instead of images
    "shutterstock.", "dreamstime.", "alamy.", "istockphoto.", "gettyimages.", "depositphotos.",
    "123rf.", "stock.adobe.", "vectorstock.", "canstockphoto.", "pond5.", "bigstockphoto.",
    "agefotostock.", "stocksy.", "freepik.", "instagram.", "facebook.", "lookaside.",
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
}
MAX_BYTES = 10_000_000
FULL_MAX, THUMB_BOX = 1600, (320, 200)


@dataclass
class Fetched:
    cand: Candidate
    full: Path
    thumb: Path
    width: int
    height: int
    ahash: int


def size_ok(w: int, h: int, kind: str) -> bool:
    if not w or not h:
        return True                       # unknown before download; checked again after
    long, short = max(w, h), min(w, h)
    if long / short > 2.5:
        return False
    # diagrams and charts on the web are often small but still readable
    min_long = 400 if kind in ("diagram", "chart") else 500
    return long >= min_long and short >= 200


def blocked(c: Candidate) -> bool:
    hosts = (c.domain, c.url.lower())
    return any(b in h for b in BLOCKED_DOMAINS for h in hosts)


def ahash(im: PImage.Image) -> int:
    g = im.convert("L").resize((8, 8))
    px = list(g.tobytes())
    avg = sum(px) / 64
    return sum(1 << i for i, p in enumerate(px) if p > avg)


def similar(a: int, b: int, bits: int = 6) -> bool:
    return bin(a ^ b).count("1") <= bits


def _process(data: bytes, kind: str) -> tuple[PImage.Image, int] | str:
    """(image, hash) if usable, else the reason it was rejected."""
    try:
        im = PImage.open(io.BytesIO(data))
        im.seek(0)                        # first frame of GIFs
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = PImage.new("RGB", im.size, "white")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
    except Exception:
        return "not a readable image"
    if not size_ok(*im.size, kind):
        return f"too small or banner ({im.width}x{im.height})"
    if max(ImageStat.Stat(im.convert("L")).stddev) < 10:
        return "blank"
    return im, ahash(im)


async def _download(session: aiohttp.ClientSession, url: str) -> bytes | None:
    try:
        async with session.get(url, headers=HEADERS, allow_redirects=True) as r:
            if r.status != 200:
                return None
            if not r.headers.get("Content-Type", "image/").lower().startswith(("image/", "application/octet")):
                return None
            if int(r.headers.get("Content-Length") or 0) > MAX_BYTES:
                return None
            buf = bytearray()
            async for chunk in r.content.iter_chunked(64 * 1024):
                buf += chunk
                if len(buf) > MAX_BYTES:
                    return None
            return bytes(buf)
    except Exception:
        return None


async def fetch_best(cands: list[Candidate], kind: str, out_dir: Path, name: str, want: int = 3,
                     seen_hashes: list[int] = (), try_n: int = 10, soft: float = 2.0,
                     timeout: float = 6, report: list | None = None) -> list[Fetched]:
    """Download up to `try_n` candidates in parallel; return up to `want` good, distinct ones in
    search order (at most one per domain, so the host gets genuinely different options).
    After `soft` seconds, stop waiting for slow servers if enough downloads are already in.
    If `report` is given, one dict per candidate (with the reason it was kept or not) is appended."""
    recs = [{"url": c.url, "thumb": c.thumb, "page": c.page, "domain": c.domain,
             "provider": c.provider, "title": c.title[:90], "w": c.width, "h": c.height,
             "status": "not tried"} for c in cands]
    pool_idx = []
    for i, c in enumerate(cands):
        if blocked(c):
            recs[i]["status"] = "stock / blocked site"
        elif not size_ok(c.width, c.height, kind):
            recs[i]["status"] = "too small or banner"
        elif len(pool_idx) < try_n:
            pool_idx.append(i)
    if report is not None:
        report.extend(recs)
    picked = await _run(cands, recs, pool_idx, kind, out_dir, name, want, seen_hashes, soft, timeout)
    if picked:
        return picked
    # Nothing acceptable: better a watermarked or smallish image the host can judge than nothing.
    # Retry with the candidates we rejected up front (stock sites, small sizes; never banners).
    rest = [i for i, r in enumerate(recs)
            if r["status"] == "stock / blocked site"
            or (r["status"] == "too small or banner" and max(cands[i].width, cands[i].height) >= 300
                and max(cands[i].width, cands[i].height) <= 2.5 * max(1, min(cands[i].width, cands[i].height)))][:try_n]
    return await _run(cands, recs, rest, kind, out_dir, name, want, seen_hashes, soft, timeout, fallback=True)


async def _run(cands, recs, pool_idx, kind, out_dir, name, want, seen_hashes, soft, timeout,
               fallback: bool = False) -> list[Fetched]:
    if not pool_idx:
        return []
    pool = [cands[i] for i in pool_idx]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout, sock_connect=3)) as s:
        tasks = [asyncio.create_task(_download(s, c.url)) for c in pool]
        done, pending = await asyncio.wait(tasks, timeout=soft)
        if pending and sum(1 for t in done if t.result()) < want + 1:
            done, pending = await asyncio.wait(tasks, timeout=timeout - soft)
        for t in pending:
            t.cancel()
        datas = [t.result() if t in done else ("slow" if t in pending else None) for t in tasks]

    picked: list[Fetched] = []
    domains: set[str] = set()
    hashes = list(seen_hashes)
    for i, c, data in zip(pool_idx, pool, datas):
        rec = recs[i]
        if data == "slow":
            rec["status"] = "too slow, skipped"
            continue
        if not data:
            rec["status"] = "download failed"
            continue
        if len(picked) >= want:
            rec["status"] = "spare (enough already)"
            continue
        if c.domain in domains and c.provider != "wikimedia":
            rec["status"] = "same site as a picked one"
            continue
        res = await asyncio.to_thread(_process, data, kind)
        if isinstance(res, str):
            rec["status"] = res
            continue
        im, h = res
        if any(similar(h, o) for o in hashes):
            rec["status"] = "duplicate"
            continue
        n = len(picked) + 1
        full, thumb = out_dir / f"{name}-{n}.jpg", out_dir / f"{name}-{n}_thumb.jpg"
        await asyncio.to_thread(_save, im, full, thumb)
        w, hh = im.size if max(im.size) <= FULL_MAX else _fit(im.size, FULL_MAX)
        picked.append(Fetched(c, full, thumb, w, hh, h))
        rec["status"] = "picked (fallback: was rejected)" if fallback else "picked"
        rec["w"], rec["h"] = im.size
        domains.add(c.domain)
        hashes.append(h)
    return picked


def _fit(size: tuple[int, int], m: int) -> tuple[int, int]:
    w, h = size
    s = m / max(w, h)
    return round(w * s), round(h * s)


def _save(im: PImage.Image, full: Path, thumb: Path) -> None:
    big = im.copy()
    big.thumbnail((FULL_MAX, FULL_MAX))
    big.save(full, "JPEG", quality=88)
    small = im.copy()
    small.thumbnail(THUMB_BOX)
    small.save(thumb, "JPEG", quality=82)
