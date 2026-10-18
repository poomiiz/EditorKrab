"""Subtitle sidecars attach to occurrences, never to repeated display wording."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import atomic
from video_edit import VideoEditError


def key(cue_id: str) -> str:
    if not isinstance(cue_id, str) or not cue_id:
        raise VideoEditError("ซับไม่มี cue ID · โหลดโปรเจกต์ใหม่ก่อนแก้ไข")
    return "cue:" + cue_id


def load(folder: str | Path, filename: str, rows: list[dict]) -> dict[str, Any]:
    file = Path(folder) / filename
    if not file.is_file():
        return {}
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise VideoEditError(f"{filename}: ต้องเป็นข้อมูลซับแยกตาม cue")
    counts = Counter(row["text"] for row in rows)
    if len({key(row["id"]) for row in rows}) != len(rows):
        raise VideoEditError("cue ID ของซับซ้ำกัน · ตรวจร่างก่อนอ่านเวลาคำ")
    out = {}
    for row in rows:
        cue = key(row["id"])
        if cue in data:
            out[cue] = data[cue]
        elif row["text"] in data:
            if counts[row["text"]] > 1:
                raise VideoEditError(f"{filename}: ซับเดิมพูดซ้ำและไม่มีเวลาแยกแต่ละช่วง · ถอดเสียงหรือเลือกคำเน้นใหม่: {row['text']}")
            out[cue] = data[row["text"]]
    return out


def save(folder: str | Path, filename: str, data: dict[str, Any]) -> None:
    if any(not k.startswith("cue:") or not k[4:] for k in data):
        raise VideoEditError(f"{filename}: ต้องระบุ cue ID สำหรับแต่ละช่วงซับ")
    file = Path(folder) / filename
    if file.is_file():
        old = file.read_text(encoding="utf-8")
        original = json.loads(old)
        backup = file.with_name(file.name + ".legacy.bak")
        if any(not k.startswith("cue:") for k in original) and not backup.exists():
            atomic.write_text(backup, old)
    atomic.write_text(file, json.dumps(data, ensure_ascii=False, indent=1))


def select(rows: list[dict], text: str, cue_id: str | None = None) -> dict:
    matches = [row for row in rows if (row["id"] == cue_id if cue_id else row["text"] == text)]
    if len(matches) != 1:
        raise VideoEditError("เลือกช่วงซับให้ชัดเจนด้วย cue ID · ข้อความซ้ำหรือไม่พบซับแล้ว")
    if matches[0]["text"] != text:
        raise VideoEditError("ข้อความซับเปลี่ยนแล้ว · โหลดโปรเจกต์ใหม่ก่อนแก้ไข")
    return matches[0]


def phrase_key(phrase: dict) -> str:
    return key(phrase["cue_id"]) if phrase.get("cue_id") else phrase["line"]


def record_sources(folder: str | Path, associations: dict[str, list[str]]) -> None:
    file = Path(folder) / "clipkit_cues.json"
    data = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}
    if not isinstance(data, dict) or any(not isinstance(value, dict)
            or not isinstance(value.get("source_cues"), list)
            or any(not isinstance(i, str) or not i for i in value["source_cues"]) for value in data.values()):
        raise VideoEditError("clipkit_cues.json: ข้อมูลอ้างอิงช่วงซับไม่ถูกต้อง")
    original = dict(data)
    for child, parents in associations.items():
        sources = [source for parent in parents for source in original.get(key(parent), {"source_cues": [parent]})["source_cues"]]
        data[key(child)] = {"source_cues": list(dict.fromkeys(sources))}
    if associations:
        save(folder, file.name, data)
