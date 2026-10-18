"""Pixabay search candidates; the user must check each item's source and license.
Needs a free API key in config.json: pixabay_key (Settings > เครื่องนี้).
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from video_edit import VideoEditError

SIGNUP = {"pixabay": "https://pixabay.com/api/docs/"}


def _file_url(url: str) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        valid = (parsed.scheme == "https" and not parsed.username and not parsed.password
                 and parsed.port in (None, 443) and (host == "pixabay.com" or host.endswith(".pixabay.com")))
    except ValueError:
        valid = False
    if not valid:
        raise VideoEditError("ลิงก์ดาวน์โหลดต้องเป็น HTTPS ของ Pixabay")


class _StockRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _file_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "ClipKit", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _pick(files: list[dict[str, Any]], want_h: int = 1920) -> dict[str, Any] | None:
    """The smallest file that is still at least 1080 on its short side, else the biggest there is."""
    ok = [f for f in files if f.get("link") and min(f.get("width") or 0, f.get("height") or 0) >= 1080]
    pool = ok or [f for f in files if f.get("link")]
    if not pool:
        return None
    return min(pool, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0)) if ok else \
        max(pool, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))


def search(cfg: dict[str, Any], query: str, count: int = 6, portrait: bool = True) -> dict[str, Any]:
    """Pixabay video hits for a search (portrait first when asked)."""
    if not cfg.get("pixabay_key"):
        raise VideoEditError("ยังไม่ได้ใส่รหัส Pixabay — ไปที่ ตั้งค่า > เครื่องนี้ (สมัครฟรี)")
    q = urllib.parse.urlencode({"key": cfg["pixabay_key"], "q": query, "per_page": max(3, count * 2), "safesearch": "true"})
    try:
        hits = _get(f"https://pixabay.com/api/videos/?{q}").get("hits", [])
    except Exception as exc:
        raise VideoEditError(f"ค้นใน Pixabay ไม่ได้: {exc}") from exc
    items = []
    for v in hits:
        vids = v.get("videos") or {}
        f = _pick([{"link": x.get("url"), "width": x.get("width"), "height": x.get("height")} for x in vids.values()])
        if f:
            items.append({"source": "pixabay", "id": str(v["id"]), "title": v.get("tags", "Pixabay"),
                          "thumb": (vids.get("tiny") or {}).get("thumbnail") or "", "url": v.get("pageURL"),
                          "file": f["link"], "duration": v.get("duration"), "width": f["width"], "height": f["height"]})
    if portrait:  # Pixabay has no portrait filter: tall clips first, wide ones get cropped
        items.sort(key=lambda x: (x["height"] or 0) <= (x["width"] or 0))
    return {"items": items[:count], "missing_keys": [], "errors": []}


def download(folder: str, source: str, item_id: str, file_url: str) -> str:
    """Save one clip into <stock_video>/free (kept once; the same clip is not downloaded twice)."""
    if source not in SIGNUP or not item_id.isdigit():
        raise VideoEditError("unknown free stock item")
    _file_url(file_url)
    out = Path(folder) / "free"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{source}-{item_id}.mp4"
    if not target.is_file():
        req = urllib.request.Request(file_url, headers={"User-Agent": "ClipKit"})
        fd, temporary = tempfile.mkstemp(dir=out, prefix=target.name + ".", suffix=".part")
        part = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as f, urllib.request.build_opener(_StockRedirect()).open(req, timeout=120) as r:
                _file_url(r.geturl())
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            part.replace(target)
        except Exception as exc:
            part.unlink(missing_ok=True)
            if isinstance(exc, VideoEditError):
                raise
            raise VideoEditError(f"ดาวน์โหลด Pixabay ไม่สำเร็จ: {exc}") from exc
    return str(target)
