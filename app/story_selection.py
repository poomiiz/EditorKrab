"""Shared word-boundary selection and read-only provenance for every story channel."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from video_edit import VideoEditError

VERSION = 1
DURATION_REVIEW_MIN_S = 15.0
DURATION_REVIEW_FRACTION = 0.20
_SOURCE_CACHE: dict[str, dict] = {}
_EPSILON = 1e-6


def selection_file(video: str | Path) -> Path:
    return Path(str(video) + ".story-selection.json")


def _target(value: float | None) -> float | None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                              or not math.isfinite(value) or value <= 0):
        raise VideoEditError("ความยาวต้องเป็นจำนวนนาทีที่มากกว่า 0")
    return value


def source_revision(video: str | Path, *, include_stories: bool = False,
                    require_transcript: bool = True) -> str:
    """Hash current inputs without issuing a packet or writing any state/cache files.

    An existing packet's stat-validated source cache or the process cache avoids rereading
    unchanged full media. Missing transcript is allowed only when explicitly requested
    before ASR; existing transcript/meta are always included in that revision.
    """
    import web_ai_identity as identity
    key = identity._path_id(video)
    cache = identity._json(identity.state_dir("stories", video) / "source-cache.json", {})
    if not isinstance(cache, dict):
        raise VideoEditError("source-cache.json ต้องเป็น object")
    cache = {**cache, **_SOURCE_CACHE.get(key, {})}
    snapshot, updated = identity._snapshot("stories", video, None, cache,
                                           require_transcript=require_transcript)
    if include_stories:
        snapshot["files"]["selection:stories"] = identity._file(Path(str(video) + ".stories.json"))
        snapshot["files"]["selection:binding"] = identity._file(selection_file(video))
    _SOURCE_CACHE[key] = updated
    return identity._hash(snapshot)


def source_rows(phrases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose real source clocks; word n identifies the original ASR piece group start."""
    import video_edit
    rows = []
    for number, phrase in enumerate(phrases, 1):
        words = phrase.get("words", [])
        language = "th" if re.search(r"[ก-๛]", phrase["text"]) else video_edit.speech_language()
        starts = [n for n, whole in enumerate(video_edit._word_starts(words, language)) if whole] if words else []
        groups = []
        for a, b in zip(starts, starts[1:] + [len(words)]):
            groups.append({"n": a + 1, "end_n": b, "start": words[a][0], "end": words[b - 1][1],
                           "text": "".join(word[2] for word in words[a:b]).strip()})
        rows.append({"n": number, "cue_id": f"source-{number}", "start": phrase["start"],
                     "end": phrase["end"], "text": phrase["text"], "words": groups})
    return rows


def _anchor(value: Any, edge: str, rows: list[dict]) -> tuple[float, dict]:
    if not isinstance(value, dict) or set(value) - {"cue_id", "word"}:
        raise VideoEditError(f"{edge}_anchor ต้องเป็น {{cue_id, word?}} จากคำสั่ง AI")
    row = next((r for r in rows if r["cue_id"] == value.get("cue_id")), None)
    if row is None:
        raise VideoEditError(f"{edge}_anchor อ้างอิงบรรทัดที่ไม่มีอยู่")
    anchor = {"cue_id": row["cue_id"]}
    if "word" not in value:
        return float(row[edge]), anchor
    number = value["word"]
    if isinstance(number, bool) or not isinstance(number, int):
        raise VideoEditError(f"{edge}_anchor.word ต้องเป็นหมายเลขขอบคำเต็ม")
    word = next((w for w in row["words"] if w["n"] == number), None)
    if word is None:
        raise VideoEditError(f"{edge}_anchor.word ไม่ใช่ขอบคำเต็มในบรรทัดนี้")
    return float(word[edge]), {**anchor, "word": number}


def _edge(item: dict, edge: str, rows: list[dict]) -> tuple[float, dict]:
    value = item.get(edge)
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
        raise VideoEditError("เวลาต้องเป็นตัวเลขวินาทีที่ถูกต้อง")
    if edge + "_anchor" in item:
        actual, anchor = _anchor(item[edge + "_anchor"], edge, rows)
        if value is not None and abs(value - actual) > _EPSILON:
            raise VideoEditError(f"{edge} ไม่ตรงกับ {edge}_anchor · ใช้เวลาจากคำถอดเสียงจริง")
        return actual, anchor
    if value is None:
        raise VideoEditError(f"ต้องมี {edge} หรือ {edge}_anchor")
    # Prefer a real spoken word over a transport-row edge when both share a clock.
    for row in rows:
        for word in row["words"]:
            if abs(value - word[edge]) <= _EPSILON:
                return float(value), {"cue_id": row["cue_id"], "word": word["n"]}
    for row in rows:
        if abs(value - row[edge]) <= _EPSILON:
            return float(value), {"cue_id": row["cue_id"]}
    raise VideoEditError("ขอบเขตเรื่องต้องตรงจุดเริ่มหรือจบของคำเต็มในคำถอดเสียง")


def validate(items: Any, phrases: list[dict[str, Any]], *, max_minutes: float | None = None) -> list[dict[str, Any]]:
    """Validate without semantic guessing, snapping, truncation, splitting or approval."""
    import video_edit
    _target(max_minutes)
    if not isinstance(items, list) or not items or not phrases:
        raise VideoEditError("stories ต้องเป็นรายการเรื่องอย่างน้อย 1 เรื่อง และมีบทถอดเสียง")
    rows, out = source_rows(phrases), []
    limit = max(p["end"] for p in phrases)
    for number, item in enumerate(items, 1):
        if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not item["title"].strip():
            raise VideoEditError(f"เรื่องที่ {number}: ต้องมี title")
        start, start_anchor = _edge(item, "start", rows)
        end, end_anchor = _edge(item, "end", rows)
        if not 0 <= start < end <= limit + _EPSILON:
            raise VideoEditError(f"เรื่องที่ {number}: เวลาเริ่ม–จบต้องอยู่ภายในฟุตและเรียงจากน้อยไปมาก")
        if out and start < out[-1]["end"] - _EPSILON:
            raise VideoEditError(f"เรื่องที่ {number}: ช่วงซ้ำ ทับกัน หรือไม่ได้เรียงตามเวลา")
        if not video_edit.slice_transcript(phrases, start, end):
            raise VideoEditError(f"เรื่องที่ {number}: ไม่มีเสียงพูดในช่วงนั้น")
        text = {}
        for field in ("summary", "reason", "duration_reason"):
            value = item.get(field, "")
            if not isinstance(value, str):
                raise VideoEditError(f"เรื่องที่ {number}: {field} ต้องเป็นข้อความ")
            text[field] = value.strip()
        actual, target = end - start, max_minutes * 60 if max_minutes is not None else None
        delta = actual - target if target is not None else None
        review = target is not None and abs(delta) > max(DURATION_REVIEW_MIN_S, DURATION_REVIEW_FRACTION * target)
        out.append({"title": item["title"].strip(), **text, "start": start, "end": end,
                    "start_anchor": start_anchor, "end_anchor": end_anchor,
                    "duration_s": round(actual, 6), "target_s": target,
                    "delta_s": round(delta, 6) if delta is not None else None, "review_required": review})
    return out


def selection_binding(video: str | Path, stories_file: Path, *, input_revision: str | None = None) -> dict:
    """Metadata staged beside the list and published by the same durable transaction."""
    import web_ai_identity as identity
    return {"version": VERSION, "input_revision": input_revision or source_revision(video),
            "stories_sha256": identity._file(stories_file, required=True)}


def read_selection(video: str | Path, phrases: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Absent, unbound or stale selections are historical; malformed files still raise."""
    import web_ai_identity as identity
    stories = Path(str(video) + ".stories.json")
    if not stories.is_file():
        return None
    items = identity._json(stories)
    binding = identity._json(selection_file(video))
    if binding is None:
        # A malformed local answer remains an error, even before explicit revalidation/binding.
        validate(items, phrases)
        return None
    if not isinstance(binding, dict) or binding.get("version") != VERSION or any(
            not isinstance(binding.get(k), str) or not re.fullmatch(r"[0-9a-f]{64}", binding[k])
            for k in ("input_revision", "stories_sha256")):
        raise VideoEditError("ข้อมูลอ้างอิงรายการเรื่องไม่ถูกต้อง · ตรวจและบันทึกรายการใหม่")
    if binding["stories_sha256"] != identity._file(stories) or binding["input_revision"] != source_revision(video):
        return None
    current = identity._json(Path(str(video) + ".transcript.json"))
    if identity._hash(current) != identity._hash(phrases):
        return None
    target = identity._preferences(identity._canonical(video), "stories", None, identity._canonical(video))["max_minutes"]
    return validate(items, phrases, max_minutes=target)
