"""Work on CapCut projects that already exist: read a draft, put subtitles on
it, restyle them, trim its pauses, or copy a colour grade from another draft.

Every write backs the draft up first (draft_content.json.bak_<op>) and CapCut
must be closed while these run, otherwise it overwrites the file on exit.
"""
from __future__ import annotations

import atomic
import copy
import json
import cue_data
import math
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import video_edit as _ve  # noqa: F401  (puts scripts/capcut on sys.path)
import kitconfig
import project_lock
from video_edit import (capcut_drafts_root, US, VideoEditError,
                                 detect_pauses, quiet_spans, suggest_cuts)

GRADE_KEYS = ("effects", "hsl", "color_curves")


def _drafts_root(root: str | None = None) -> Path:
    path = Path(root or capcut_drafts_root())
    if not path.is_dir():
        raise VideoEditError(f"CapCut drafts folder not found: {path}")
    return path


SEAL = "clipkit_seal.json"


def _digest(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _seal(folder: Path, text: str) -> None:
    """Remember what ClipKit last wrote, so an edit made outside ClipKit is caught before ClipKit builds on it."""
    atomic.write_text((folder / SEAL), json.dumps({"draft_content": _digest(text)}), encoding="utf-8")


@project_lock.edit
def accept(folder: str) -> None:
    """The person changed the project on purpose (in CapCut): ClipKit carries on from that version."""
    f = Path(folder)
    _seal(f, (f / "draft_content.json").read_text(encoding="utf-8"))


def _load(folder: Path) -> dict[str, Any]:
    content = folder / "draft_content.json"
    if not content.is_file():
        raise VideoEditError(f"ไม่พบโปรเจกต์ที่โฟลเดอร์นี้ อาจถูกย้ายหรือลบไปแล้ว: {folder}")
    text = content.read_text(encoding="utf-8")
    seal = folder / SEAL
    if seal.is_file() and json.loads(seal.read_text(encoding="utf-8")).get("draft_content") != _digest(text):
        raise VideoEditError(
            f"โปรเจกต์ {folder.name} ถูกแก้นอก EditorKrab (CapCut หรือ AI ตัวอื่นแก้ไฟล์ draft_content.json เอง) "
            "EditorKrab จะไม่ทำต่อบนไฟล์นี้ เพราะอาจได้งานเพี้ยน: สร้างโปรเจกต์ใหม่จากไฟล์ดิบ หรือถ้าตั้งใจแก้ใน CapCut เอง "
            f'สั่ง  python scripts/accept_edit.py "{folder}"  แล้วทำต่อได้')
    return json.loads(text)


@project_lock.edit
def sync_timeline(folder: Path, text: str) -> None:
    """CapCut 9.x keeps the open timeline in Timelines/<main_timeline_id>/draft_content.json as well as the
    project's own draft_content.json; it loads that copy, so every edit is written to both."""
    proj = folder / "Timelines" / "project.json"
    if proj.is_file():
        tid = json.loads(proj.read_text(encoding="utf-8")).get("main_timeline_id")
        inner = folder / "Timelines" / str(tid)
        if tid and inner.is_dir():
            atomic.write_text((inner / "draft_content.json"), text, encoding="utf-8")


@project_lock.edit
def fresh_ids(folder: Path) -> str:
    """A cloned project gets its own timeline id (project.json, the Timelines/<id> folder, draft_content id):
    two projects sharing one id make CapCut open the other project's timeline or hang."""
    proj = folder / "Timelines" / "project.json"
    content = folder / "draft_content.json"
    new = str(uuid.uuid4()).upper()
    if proj.is_file():
        old = json.loads(proj.read_text(encoding="utf-8")).get("main_timeline_id")
        if old:
            atomic.write_text(proj, proj.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
            if (folder / "Timelines" / old).is_dir():
                (folder / "Timelines" / old).rename(folder / "Timelines" / new)
    if content.is_file():
        draft = json.loads(content.read_text(encoding="utf-8"))
        draft["id"] = new
        text = json.dumps(draft, ensure_ascii=False, indent=2)
        atomic.write_text(content, text, encoding="utf-8")
        sync_timeline(folder, text)
        _seal(folder, text)
    return new


CHANGES = "clipkit_changes.jsonl"


def _rows(draft: dict[str, Any]) -> dict[str, list[str]]:
    """The draft as editdata.py reads an approved clip (cuts, cards, inserts, audio, effects), one JSON row each."""
    import sqlite3
    import editdata
    con = sqlite3.connect(":memory:")
    con.executescript(editdata.SCHEMA)
    editdata.read(con.cursor(), 0, "", draft)
    out = {}
    for t in ("cuts", "cards", "inserts", "audio", "effects"):
        cur = con.execute(f"SELECT * FROM {t}")
        cols = [c[0] for c in cur.description][2:]  # without clip_id, version
        out[t] = [json.dumps(dict(zip(cols, [round(v, 2) if isinstance(v, float) else v for v in r[2:]])),
                             ensure_ascii=False, sort_keys=True) for r in cur]
    return out


HISTORY = "clipkit_history"


def _keep_version(folder: Path, text: str, op: str) -> str:
    """One gzipped copy of the draft per edit, like an autosave history; restore() puts any of them back."""
    import gzip
    d = folder / HISTORY
    d.mkdir(exist_ok=True)
    n = len(list(d.glob("*.json.gz"))) + 1  # numbered: two edits in one millisecond still sort in order
    name = f"{n:04d} {time.strftime('%Y%m%d-%H%M%S')} {op}.json.gz"
    import spelling
    rules = d / (name + ".corrections.json")
    atomic.write_text(rules, json.dumps(spelling._read(spelling._file(folder)), ensure_ascii=False))
    try:
        atomic.write_bytes(d / name, gzip.compress(text.encode("utf-8")))
    except Exception:
        rules.unlink(missing_ok=True)
        raise
    return name


def history(folder: str) -> list[str]:
    """The project's saved versions, oldest first."""
    d = Path(folder) / HISTORY
    return sorted(p.name for p in d.glob("*.json.gz")) if d.is_dir() else []


@project_lock.edit
def restore(folder: str, version: str) -> None:
    """Put a saved version back (itself recorded as a new edit, so nothing is lost)."""
    import gzip
    f = Path(folder)
    src = f / HISTORY / version
    if not src.is_file():
        raise VideoEditError(f"no saved version {version!r} in {f / HISTORY}")
    _load(f)  # refuses a project edited outside ClipKit
    import spelling
    rules = src.with_name(src.name + ".corrections.json")
    # Legacy snapshots did not record correction rules; retain those rules rather than invent history.
    _save(f, json.loads(gzip.decompress(src.read_bytes()).decode("utf-8")), "restore",
          corrections=spelling._read(rules) if rules.is_file() else None)


def _log_change(folder: Path, before_text: str, text: str, op: str) -> None:
    """Every edit ClipKit writes, as rows removed and added plus a saved version, in the project folder
    (stays on this machine). editdata.py extract loads the log into edits.sqlite, so the owner's own way of
    editing can be learned from it."""
    from collections import Counter
    before, after = json.loads(before_text), json.loads(text)
    b, a = _rows(before), _rows(after)
    removed = {t: [json.loads(r) for r in (Counter(b[t]) - Counter(a[t])).elements()] for t in b}
    added = {t: [json.loads(r) for r in (Counter(a[t]) - Counter(b[t])).elements()] for t in a}
    removed, added = {t: v for t, v in removed.items() if v}, {t: v for t, v in added.items() if v}
    if not removed and not added:
        return
    if not history(str(folder)):
        _keep_version(folder, before_text, "start")
    row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "op": op, "version": _keep_version(folder, text, op),
           "removed": removed, "added": added}
    with open(folder / CHANGES, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")



def _newest_draft_file(folder: Path) -> Path:
    """draft_content.json, or CapCut 9.x's Timelines/<id> copy when CapCut saved that one later."""
    content = folder / "draft_content.json"
    proj = folder / "Timelines" / "project.json"
    if proj.is_file():
        inner = folder / "Timelines" / str(json.loads(proj.read_text(encoding="utf-8")).get("main_timeline_id")) / "draft_content.json"
        if inner.is_file() and inner.stat().st_mtime > content.stat().st_mtime:
            return inner
    return content


@project_lock.edit
def record_outside(folder: str) -> str | None:
    """An edit saved by CapCut itself goes into the history and change log like ClipKit's own edits.
    Returns what would stop CapCut opening it (the last good version stays in the history), else None.
    The seal is left alone: ClipKit still will not build on it until accept_edit.py is run."""
    import gzip
    f = Path(folder)
    text = _newest_draft_file(f).read_text(encoding="utf-8")
    versions = history(folder)
    last = gzip.decompress((f / HISTORY / versions[-1]).read_bytes()).decode("utf-8") if versions else None
    if text == last:
        return None
    try:
        _check(json.loads(text))
    except (ValueError, KeyError, VideoEditError) as exc:  # ValueError: not JSON; KeyError: a segment lacks its times
        problem = f"{type(exc).__name__}: {exc}"
        with open(f / CHANGES, "a", encoding="utf-8") as out:
            out.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "op": "capcut", "problem": problem},
                                 ensure_ascii=False) + "\n")
        return problem
    if last is None:
        _keep_version(f, text, "start")
    else:
        _log_change(f, last, text, "capcut")
    return None


def watch(root: str, every: float = 5.0, settle: float = 3.0, report=print) -> None:
    """The bot: every few seconds, record each project CapCut has saved since the last look.
    A file is read only after it has not changed for `settle` seconds, so a save in progress is not caught half-way."""
    import hf_build
    seen: dict[Path, float] = {}
    while True:
        for c in Path(root).glob("*/draft_content.json"):
            f = c.parent
            m = _newest_draft_file(f).stat().st_mtime
            if seen.get(f) == m or time.time() - m < settle:
                continue
            try:
                problem = record_outside(str(f))
            except hf_build.BuildBusy:
                continue  # Retry after the editor/export releases its project reservation.
            seen[f] = m
            if problem:
                report(f"{time.strftime('%H:%M:%S')} {f.name}: CapCut would not open this - {problem}. "
                       f'Put back a good version: python scripts/draft_history.py "{f}"')
        time.sleep(every)


def _check(draft: dict[str, Any]) -> None:
    """Refuse a draft CapCut cannot open: a segment whose material is not there, or a negative time.
    Nothing is written, so the project stays at its last good version."""
    ids = {m.get("id") for v in draft.get("materials", {}).values() if isinstance(v, list)
           for m in v if isinstance(m, dict)}
    for n, tr in enumerate(draft.get("tracks", [])):
        for s in tr.get("segments", []):
            t = s["target_timerange"]
            where = f"track {n} ({tr.get('type')}) at {t['start'] / US:.2f}s"
            if s.get("material_id") not in ids:
                raise VideoEditError(f"draft would break: {where} points at a missing material {s.get('material_id')!r}")
            if t["start"] < 0 or t["duration"] < 0:
                raise VideoEditError(f"draft would break: {where} has a negative time")


def _save(folder: Path, draft: dict[str, Any], op: str, *, corrections: dict | None = None,
          sidecars: dict[str, Any] | None = None) -> None:
    _check(draft)
    cue_sidecars = {"clipkit_words.json", "clipkit_punch.json", "clipkit_caption.json", "clipkit_cues.json"}
    if set(sidecars or {}) - cue_sidecars - {"clipkit_placed.json", "clipkit_overlays.json", "clipkit_style.json"}:
        raise VideoEditError("Unsupported project sidecar")
    import spelling
    rules_file = spelling._file(folder)
    spelling._read(rules_file)  # Reject broken rules before changing the visible draft.
    import web_ai_transaction
    saved = {name: file.read_bytes() for name, file in web_ai_transaction._files(folder).items()} if corrections is not None or sidecars is not None else None
    content = folder / "draft_content.json"
    backup = content.with_suffix(f".json.bak_{op}")
    text = json.dumps(draft, ensure_ascii=False, indent=2)
    before = content.read_text(encoding="utf-8")
    try:
        if not backup.exists():
            shutil.copy2(content, backup)
        if corrections is not None and not history(str(folder)):
            _keep_version(folder, before, "start")
        if corrections is not None:
            atomic.write_text(rules_file, json.dumps(corrections, ensure_ascii=False, indent=1))
        atomic.write_text(content, text, encoding="utf-8")
        sync_timeline(folder, text)
        _seal(folder, text)
        _log_change(folder, before, text, op)
        for name, data in (sidecars or {}).items():
            file = folder / name
            if not file.is_file() or json.loads(file.read_text(encoding="utf-8")) != data:
                if name in cue_sidecars:
                    cue_data.save(folder, name, data)
                else:
                    atomic.write_text(file, json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        meta_path = folder / "draft_meta_info.json"
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if "tm_duration" in meta:
                meta["tm_duration"] = draft.get("duration", meta["tm_duration"])
                meta["tm_draft_modified"] = int(time.time() * US)
                atomic.write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        if saved is not None:
            for name, file in web_ai_transaction._files(folder).items():
                if name not in saved:
                    file.unlink()
            for name, data in saved.items():
                atomic.write_bytes(folder / name, data)
        raise


def _text_track(draft: dict[str, Any]) -> dict[str, Any] | None:
    return next((t for t in draft["tracks"] if t["type"] == "text"), None)


def _video_track(draft: dict[str, Any]) -> dict[str, Any]:
    track = next((t for t in draft["tracks"] if t["type"] == "video"), None)
    if track is None or not track["segments"]:
        raise VideoEditError("draft has no video track")
    return track


def _index(draft: dict[str, Any]) -> dict[str, tuple[str, dict]]:
    index: dict[str, tuple[str, dict]] = {}
    for key, value in draft["materials"].items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and "id" in item:
                    index[item["id"]] = (key, item)
    return index


def list_drafts(root: str | None = None) -> list[dict[str, Any]]:
    """Every draft in the folder with the numbers that matter for editing."""
    out = []
    for folder in sorted(_drafts_root(root).iterdir()):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            draft = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        video = next((t for t in draft.get("tracks", []) if t["type"] == "video"), None)
        text = next((t for t in draft.get("tracks", []) if t["type"] == "text"), None)
        sources = {m.get("path") for m in draft.get("materials", {}).get("videos", [])}
        sources = {source for source in sources if isinstance(source, str) and Path(source).is_file()}
        out.append({
            "name": folder.name,
            "path": str(folder),
            "duration": round(draft.get("duration", 0) / US, 2),
            "segments": len(video["segments"]) if video else 0,
            "subtitles": len(text["segments"]) if text else 0,
            "has_grade": bool(draft.get("materials", {}).get("color_curves")),
            "sources": [s for s in sources if s],
            "modified": int((folder / "draft_content.json").stat().st_mtime),
        })
    return sorted(out, key=lambda d: -d["modified"])


def read_draft(path: str) -> dict[str, Any]:
    """Timeline of one draft: its cuts, its subtitles, and where the video came from."""
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    video = _video_track(draft)
    text = _text_track(draft)

    clips = [{
        "source_start": round(s["source_timerange"]["start"] / US, 2),
        "duration": round(s["source_timerange"]["duration"] / US, 2),
        "timeline_start": round(s["target_timerange"]["start"] / US, 2),
    } for s in video["segments"]]

    subs = []
    if text:
        for seg in sorted(text["segments"], key=lambda s: s["target_timerange"]["start"]):
            material = index.get(seg["material_id"], (None, None))[1]
            if not material:
                continue
            body = json.loads(material["content"])
            subs.append({
                "start": seg["target_timerange"]["start"] / US,
                "end": (seg["target_timerange"]["start"] + seg["target_timerange"]["duration"]) / US,
                "text": body.get("text", ""),
                "size": material.get("font_size"),
                "color": material.get("text_color"),
                "stroke": material.get("border_width"),
                "y": round(seg["clip"]["transform"]["y"], 3),
                "x": round(seg["clip"]["transform"].get("x", 0.0), 3),
                "rotation": round(seg["clip"].get("rotation", 0.0), 1),
                "id": seg["id"],
            })

    source = next((m.get("path") for m in draft["materials"].get("videos", []) if m.get("path")), None)
    return {
        "name": folder.name,
        "path": str(folder),
        "duration": round(draft.get("duration", 0) / US, 2),
        "source": source,
        "clips": clips,
        "subtitles": subs,
    }


def timeline_to_source(draft: dict[str, Any]) -> list[tuple[float, float, float]]:
    """(timeline_start, timeline_end, source_start) for every clip on the track."""
    pieces = []
    for seg in _video_track(draft)["segments"]:
        tl_start = seg["target_timerange"]["start"] / US
        tl_dur = seg["target_timerange"]["duration"] / US
        src_start = seg.get("source_timerange", {}).get("start", 0) / US
        pieces.append((tl_start, tl_start + tl_dur, src_start))
    return pieces


@project_lock.edit
def set_subtitles(path: str, subs: list[dict[str, Any]], size: float | None = None,
                  y: float | None = None, color: str | None = None,
                  stroke: float | None = None, replace: bool = True) -> dict[str, Any]:
    """Put a subtitle list onto an existing draft, keeping its styling unless
    new values are given. Times are on the draft's own timeline."""
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    text = _text_track(draft)
    old_rows = read_draft(path)["subtitles"]
    limit = draft.get("duration", 0) / US
    previous = 0.0
    if not isinstance(subs, list) or any(not isinstance(sub, dict) for sub in subs):
        raise VideoEditError("ซับต้องเป็นรายการช่วงข้อความ")
    for sub in subs:
        start, end = sub.get("start"), sub.get("end")
        if (any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) for t in (start, end))
                or not 0 <= start < end <= limit or start < previous - 1e-6):
            raise VideoEditError("ช่วงซับต้องเรียง ไม่ทับ และอยู่ภายในวิดีโอ · ไม่ปรับเวลาหรือตัดช่วงให้อัตโนมัติ")
        if not isinstance(sub.get("text"), str) or not sub["text"].strip():
            raise VideoEditError("แต่ละช่วงซับต้องมีข้อความ · ลบช่วงออกจากรายการเมื่อต้องการเอาออก")
        previous = end
    carried_words = (cue_data.load(folder, "clipkit_words.json", old_rows)
                     if not replace or not all(s.get("id") and s.get("words") is not None for s in subs) else {})
    supplied_ids = [s["id"] for s in subs if s.get("id")]
    if any(not isinstance(i, str) for i in supplied_ids) or len(set(supplied_ids)) != len(supplied_ids):
        raise VideoEditError("cue ID ของซับต้องไม่ซ้ำกัน")
    if not replace and set(supplied_ids) & {s["id"] for s in (text or {}).get("segments", [])}:
        raise VideoEditError("cue ID นี้มีอยู่แล้ว · โหลดซับใหม่ก่อนเพิ่ม")
    for sub in subs:
        words = sub.get("words")
        if words is None:
            continue
        if not isinstance(words, list) or any(not isinstance(w, list) or len(w) != 3
                or not isinstance(w[2], str) or any(isinstance(t, bool) or not isinstance(t, (int, float))
                or not math.isfinite(t) for t in w[:2]) or not 0 <= w[0] <= w[1] for w in words):
            raise VideoEditError("เวลาระดับคำของซับไม่ถูกต้อง")
    if text is None:
        text = {"type": "text", "attribute": 0, "flag": 0, "segments": [],
                "id": str(uuid.uuid4()).upper(), "is_default_name": True}
        draft["tracks"].append(text)

    if text["segments"]:
        template_seg = copy.deepcopy(text["segments"][0])
        template_mat = copy.deepcopy(index[template_seg["material_id"]][1])
    else:
        # A project created straight from a video file has no subtitle yet;
        # borrow the look from any project that does rather than refusing.
        template_seg, template_mat = _borrow_text_style()
    keep = [] if replace else list(text["segments"])
    if replace:
        kept_ids = set()
    else:
        kept_ids = {s["material_id"] for s in keep}
    draft["materials"]["texts"] = [m for m in draft["materials"]["texts"]
                                   if m["id"] in kept_ids]

    made = []
    for sub in subs:
        body_text = sub["text"].strip()
        start, end = float(sub["start"]), float(sub["end"])

        # a line may carry its own look (the emphasised ones do)
        own = sub.get("style") or {}
        line_size = own.get("size", size)
        line_color = own.get("color", color)
        line_stroke = own.get("stroke", stroke)
        line_y = own.get("y", y)

        material = copy.deepcopy(template_mat)
        material["id"] = str(uuid.uuid4()).upper()
        material["name"] = "Subtitle"
        body = json.loads(material["content"])
        body["text"] = body_text
        for style in body["styles"]:
            style["range"] = [0, len(body_text)]
            if line_size is not None:
                style["size"] = line_size
            if line_color:
                style["fill"]["content"]["solid"]["color"] = [
                    int(line_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            if line_stroke is not None:
                for line in style.get("strokes", []):
                    line["width"] = line_stroke
        material["content"] = json.dumps(body, ensure_ascii=False)
        if line_size is not None:
            material["font_size"] = float(line_size)
            material["text_size"] = line_size
        if line_color:
            material["text_color"] = line_color
        if line_stroke is not None:
            material["border_width"] = line_stroke
        draft["materials"]["texts"].append(material)

        segment = copy.deepcopy(template_seg)
        matches = [row for row in old_rows if abs(row["start"] - start) < .01 and abs(row["end"] - end) < .01]
        segment["id"] = sub.get("id") or (matches[0]["id"] if replace and len(matches) == 1 else str(uuid.uuid4()).upper())
        segment["material_id"] = material["id"]
        segment["target_timerange"] = {"start": int(round(start * US)),
                                       "duration": int(round((end - start) * US))}
        segment["clip"] = copy.deepcopy(template_seg["clip"])
        if line_y is not None:
            segment["clip"]["transform"] = {"x": 0.0, "y": line_y}
        segment["render_index"] = 14001
        made.append(segment)
        if sub.get("words") is not None:
            carried_words[cue_data.key(segment["id"])] = sub["words"]

    text["segments"] = sorted(keep + made, key=lambda s: s["target_timerange"]["start"])
    if len({s["id"] for s in text["segments"]}) != len(text["segments"]):
        raise VideoEditError("cue ID ของซับซ้ำกัน · ตรวจช่วงซับก่อนบันทึก")
    active_ids = {cue_data.key(s["id"]) for s in text["segments"]}
    carried_words = {key: value for key, value in carried_words.items() if key in active_ids}
    candidate_index = _index(draft)
    rows = [{"id": segment["id"], "start": segment["target_timerange"]["start"] / US,
             "end": (segment["target_timerange"]["start"] + segment["target_timerange"]["duration"]) / US,
             "text": json.loads(candidate_index[segment["material_id"]][1]["content"])["text"]}
            for segment in text["segments"]]
    import render
    style_file = folder / "clipkit_style.json"
    project_style = json.loads(style_file.read_text(encoding="utf-8")) if style_file.is_file() else {}
    canvas = draft["canvas_config"]
    render.validate_pair_cues(rows, carried_words, cue_data.load(folder, "clipkit_punch.json", old_rows),
                              style=project_style, W=canvas["width"], H=canvas["height"],
                              captions=cue_data.load(folder, "clipkit_caption.json", old_rows))
    word_updates = {"clipkit_words.json": carried_words} if carried_words or (folder / "clipkit_words.json").is_file() else {}
    _save(folder, draft, "subs", sidecars=word_updates)
    return {"name": folder.name, "subtitles": len(text["segments"]), "added": len(made)}


@project_lock.edit
def merge_subtitles(path: str, groups: list[tuple[int, int]], *, dry: bool = False) -> list[dict[str, Any]]:
    """Merge explicit consecutive line ranges; preserve their real word clock and the head's styling."""
    folder = Path(path)
    draft = _load(folder)
    track, index = _text_track(draft), _index(draft)
    rows = read_draft(path)["subtitles"]
    if track is None or not rows:
        raise VideoEditError("ยังไม่มีซับให้รวม")
    previous = 0
    for first, last in groups:
        if (isinstance(first, bool) or isinstance(last, bool) or not isinstance(first, int) or not isinstance(last, int)
                or first <= previous or not first < last <= len(rows)):
            raise VideoEditError("ช่วงรวมซับต้องเรียง ไม่ซ้อนกัน และมีอย่างน้อย 2 บรรทัด")
        previous = last
    ends = dict(groups)
    words_file = folder / "clipkit_words.json"
    said = cue_data.load(folder, "clipkit_words.json", rows)
    planned, segments, associations = [], [], {}
    by_id = {s["id"]: s for s in track["segments"]}
    n = 1
    while n <= len(rows):
        last = ends.get(n, n)
        members = rows[n - 1:last]
        head = {**members[0], "n": n, "through": last}
        if last > n:
            head.update(text=" ".join(s["text"].replace("\n", " ") for s in members), end=members[-1]["end"])
        clock = []
        for row in members:
            if not said.get(cue_data.key(row["id"])):
                clock = []
                break
            offset = row["start"] - head["start"]
            clock += [[round(a + offset, 6), round(b + offset, 6), word] for a, b, word in said[cue_data.key(row["id"])]]
        head["words"] = clock
        planned.append(head)
        if not dry:
            segment = by_id[head["id"]]
            if last > n:
                associations[head["id"]] = [row["id"] for row in members]
                material = copy.deepcopy(index[segment["material_id"]][1])
                material["id"] = segment["material_id"] = str(uuid.uuid4()).upper()
                draft["materials"]["texts"].append(material)
                body = json.loads(material["content"])
                body["text"] = head["text"]
                for style in body.get("styles", []):
                    style["range"] = [0, len(head["text"])]
                material["content"] = json.dumps(body, ensure_ascii=False)
                segment["target_timerange"]["duration"] = round((head["end"] - head["start"]) * US)
                said[cue_data.key(head["id"])] = clock
            segments.append(segment)
        n = last + 1
    if not dry and groups:
        track["segments"] = segments
        _save(folder, draft, "merge_subs")
        cue_data.save(folder, "clipkit_words.json", said)
        cue_data.record_sources(folder, associations)
        atomic.write_text(folder / "clipkit_lines.json", json.dumps([s["text"] for s in planned], ensure_ascii=False), encoding="utf-8")
    return planned


def _borrow_text_style(root: str | None = None) -> tuple[dict, dict]:
    """A subtitle segment + material to model new subtitles on."""
    for folder in sorted(_drafts_root(root).iterdir(),
                         key=lambda f: -(f / "draft_content.json").stat().st_mtime
                         if (f / "draft_content.json").is_file() else 0):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            other = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        track = _text_track(other)
        if not track or not track["segments"]:
            continue
        index = _index(other)
        seg = copy.deepcopy(track["segments"][0])
        material = index.get(seg["material_id"], (None, None))[1]
        if material:
            # Borrow typography only; helper IDs belong to the other project's materials.
            seg["extra_material_refs"], seg["keyframe_refs"], seg["common_keyframes"] = [], [], []
            return seg, copy.deepcopy(material)
    raise VideoEditError("no project on this machine has a subtitle to copy the "
                         "styling from — add one subtitle in CapCut once")


@project_lock.edit
def restyle_subtitles(path: str, size: float | None = None, y: float | None = None,
                      color: str | None = None, stroke: float | None = None,
                      line_h: float | None = None) -> dict[str, Any]:
    """Change how the existing subtitles look without touching their text or timing. line_h (line height in font
    sizes) goes to CapCut as line_spacing and to clipkit_style.json for the MP4 export."""
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    text = _text_track(draft)
    if text is None:
        raise VideoEditError("this draft has no subtitles")

    changed = 0
    for segment in text["segments"]:
        material = index.get(segment["material_id"], (None, None))[1]
        if not material:
            continue
        body = json.loads(material["content"])
        for style in body["styles"]:
            if size is not None:
                style["size"] = size
            if color:
                style["fill"]["content"]["solid"]["color"] = [
                    int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            if stroke is not None:
                for line in style.get("strokes", []):
                    line["width"] = stroke
        material["content"] = json.dumps(body, ensure_ascii=False)
        if size is not None:
            material["font_size"] = float(size)
            material["text_size"] = size
        if color:
            material["text_color"] = color
        if stroke is not None:
            material["border_width"] = stroke
        if line_h is not None:
            material["line_spacing"] = round(line_h - 1.18, 3)
        if y is not None:
            segment["clip"]["transform"] = {"x": 0.0, "y": y}
        changed += 1

    _save(folder, draft, "style")
    if line_h is not None:
        style_file = folder / "clipkit_style.json"
        style = json.loads(style_file.read_text(encoding="utf-8")) if style_file.is_file() else {}
        atomic.write_text(style_file, json.dumps({**style, "line_h": line_h}, ensure_ascii=False), encoding="utf-8")
    return {"name": folder.name, "restyled": changed}


TIDY_MODES = ("lines", "split", "shrink")


@project_lock.edit
def tidy_subtitles(path: str, mode: str = "lines") -> dict[str, Any]:
    """Make long subtitles readable before export (render.split_sub): two lines broken where the Thai reads right
    ("lines"), a new subtitle from that point ("split", also when two lines still overflow), or one smaller line
    ("shrink"). First, lines that run straight on hand a dangling joining word to the next line (render.carry_joiners).
    Each line keeps its own look and position; its word times (clipkit_words.json) follow the new text."""
    import render
    if mode not in TIDY_MODES:
        raise VideoEditError(f"unknown mode: {mode} (use {', '.join(TIDY_MODES)})")
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    track = _text_track(draft)
    if track is None or not track["segments"]:
        raise VideoEditError("ยังไม่มีซับ — ถอดเสียงก่อน")
    W, H = draft["canvas_config"]["width"], draft["canvas_config"]["height"]
    said_file, style_file = folder / "clipkit_words.json", folder / "clipkit_style.json"
    said = cue_data.load(folder, "clipkit_words.json", read_draft(path)["subtitles"])
    box_w = render.safe_box(json.loads(style_file.read_text(encoding="utf-8")) if style_file.is_file() else {})["w"]
    rows = []
    for seg in sorted(track["segments"], key=lambda s: s["target_timerange"]["start"]):
        material = index.get(seg["material_id"], (None, None))[1]
        if not material:
            continue
        text = json.loads(material["content"]).get("text", "")
        start = seg["target_timerange"]["start"] / US
        rows.append({"seg": seg, "material": material, "start": start, "spoken": said.get(cue_data.key(seg["id"])),
                     "end": start + seg["target_timerange"]["duration"] / US, "text": text})
    moved = render.carry_joiners(rows)
    segments, words, two, split, shrunk = [], dict(said), 0, 0, 0
    associations = {}
    for row in rows:
        fdir, px = render.text_px(row["material"], row["seg"], W, H)
        pieces = render.split_sub(row["text"], row["spoken"], row["start"], row["end"], fdir, px, W * box_w, mode)
        split += len(pieces) - 1
        for k, (a, b, text, spoken, factor) in enumerate(pieces):
            seg, material = row["seg"], row["material"]
            if k:
                seg, material = copy.deepcopy(seg), copy.deepcopy(material)
                seg["id"], material["id"] = str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()
                seg["material_id"] = material["id"]
                draft["materials"]["texts"].append(material)
            if len(pieces) > 1:
                associations[seg["id"]] = [row["seg"]["id"]]
            body = json.loads(material["content"])
            body["text"] = text
            for style in body["styles"]:
                style["range"] = [0, len(text)]
                if factor < 1:
                    style["size"] = round(style.get("size", material.get("font_size") or 15) * factor, 2)
            if factor < 1:
                material["font_size"] = material["text_size"] = round((material.get("font_size") or 15) * factor, 2)
                shrunk += 1
            material["content"] = json.dumps(body, ensure_ascii=False)
            seg["target_timerange"] = {"start": int(round(a * US)), "duration": int(round((b - a) * US))}
            two += "\n" in text
            if spoken:
                words[cue_data.key(seg["id"])] = spoken
            segments.append(seg)
    track["segments"] = segments
    _save(folder, draft, "tidy")
    cue_data.save(folder, "clipkit_words.json", words)
    cue_data.record_sources(folder, associations)
    return {"name": folder.name, "mode": mode, "subtitles": len(segments), "two_lines": two, "split": split,
            "shrunk": shrunk, "moved_words": moved}


class NeedsAgent(VideoEditError):
    """This step is done by the agent in chat; the message is the command to paste there."""


SUBS_PRESET = {"size": 14.0, "y": -0.44, "color": "#ffffff", "stroke": 0.08}   # plain white; two lines stay inside render.SAFE_BOTTOM


@project_lock.edit
def subtitles_language(path: str, lang: str) -> dict[str, Any]:
    """Plain white subtitles in Thai or English with the recommended look. English is translated from the
    Thai lines (kept in clipkit_subs_th.json beside the draft, so switching back restores the original)."""
    folder = Path(path)
    current = read_draft(path)["subtitles"]
    if not current:
        raise VideoEditError("ยังไม่มีซับ — ถอดเสียงก่อน")
    keep = folder / "clipkit_subs_th.json"
    if lang == "en":
        if not keep.is_file():
            atomic.write_text(keep, json.dumps(current, ensure_ascii=False), encoding="utf-8")
        thai = json.loads(keep.read_text(encoding="utf-8"))
        en_file = folder / "clipkit_subs_en.json"   # written by the agent: one English line per Thai line
        if not en_file.is_file():
            raise NeedsAgent(f'ClipKit: แปลซับเป็นอังกฤษ "{folder}"')
        texts = json.loads(en_file.read_text(encoding="utf-8-sig"))
        if not isinstance(texts, list) or len(texts) != len(thai):
            raise VideoEditError(f"clipkit_subs_en.json has {len(texts) if isinstance(texts, list) else '?'} lines "
                                 f"for {len(thai)} Thai lines - ask the agent to translate again")
        subs = [{**s, "text": str(t)} for s, t in zip(thai, texts)]
    elif lang == "th":
        src = json.loads(keep.read_text(encoding="utf-8")) if keep.is_file() else current
        subs = [dict(s) for s in src]
    else:
        raise VideoEditError(f"unknown language: {lang}")
    out = set_subtitles(path, subs, replace=True, **SUBS_PRESET)
    return {**out, "lang": lang, "lines": len(subs)}


@project_lock.edit
def set_text_layout(path: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Place each subtitle where the editor put it on the 9:16 preview: position (x, y in -1..1),
    tilt in degrees and font size. Only the listed segments change; a backup is kept as .bak_layout."""
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    text = _text_track(draft)
    if text is None:
        raise VideoEditError("this draft has no subtitles")
    by_id = {s["id"]: s for s in text["segments"]}
    missing = [i["id"] for i in items if i["id"] not in by_id]
    if missing:
        raise VideoEditError(f"subtitles not in this draft (reload the project): {missing[:3]}")
    for item in items:
        seg = by_id[item["id"]]
        seg["clip"]["transform"] = {"x": float(item["x"]), "y": float(item["y"])}
        seg["clip"]["rotation"] = float(item.get("rotation", 0.0))
        if item.get("size"):
            material = index.get(seg["material_id"], (None, None))[1]
            if material:
                body = json.loads(material["content"])
                for style in body["styles"]:
                    style["size"] = float(item["size"])
                material["content"] = json.dumps(body, ensure_ascii=False)
                material["font_size"] = float(item["size"])
                material["text_size"] = float(item["size"])
    _save(folder, draft, "layout")
    return {"name": folder.name, "placed": len(items)}


WORD_PAD = 0.10  # silence left before and after a word at a cut


@project_lock.edit
def trim_pauses(path: str, keep: float = 0.25, min_gain: float = 0.30,
                apply: bool = True, ranges: list[tuple[float, float]] | None = None) -> dict[str, Any]:
    """Find the pauses left in a draft's timeline and cut them out, sliding the
    subtitles along so they stay on the words. ranges = cut exactly these timeline spans instead (the editor's
    "cut this out"); a subtitle wholly inside one goes with it."""
    import web_ai_transaction
    if apply and not web_ai_transaction.staged(path):
        return web_ai_transaction.manual(path, lambda work: trim_pauses(str(work), keep=keep,
            min_gain=min_gain, apply=True, ranges=ranges))
    folder = Path(path)
    draft = _load(folder)
    video = _video_track(draft)
    text = _text_track(draft)
    source = next((m.get("path") for m in draft["materials"].get("videos", []) if m.get("path")), None)
    if not source or not Path(source).is_file():
        raise VideoEditError(f"source video not found: {source}")

    cuts: list[dict[str, float]] = []
    total = sum(s["source_timerange"]["duration"] for s in video["segments"]) / US
    words_file = folder / "clipkit_words.json"
    said = cue_data.load(folder, "clipkit_words.json", read_draft(path)["subtitles"])
    # breaths and pauses against the clip's own noise floor (a fixed silence gate finds nothing under café
    # noise), ~0.1 s left each side. Tuned on café footage: 41 cuts / 20 s out of 118 s with every word
    # still there on re-transcription - a hand edit of the same talk has 40.
    cursor = 0.0
    for a, b in ranges or []:
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) for t in (a, b)) or b <= a:
            raise VideoEditError("ช่วงตัดต้องมีเวลาเริ่มก่อนเวลาจบ และเป็นวินาทีที่ถูกต้อง")
        a, b = max(0.0, a), min(total, b)
        if b - a > 0.05:
            cuts.append({"start": round(a, 3), "end": round(b, 3), "gain": round(b - a, 3)})
    for segment in ([] if ranges is not None else video["segments"]):
        start = segment["source_timerange"]["start"] / US
        length = segment["source_timerange"]["duration"] / US
        for q in quiet_spans(source, start, start + length):
            if q["length"] - 2 * WORD_PAD >= 0.1:
                cuts.append({"start": round(cursor + q["start"] + WORD_PAD, 2), "end": round(cursor + q["end"] - WORD_PAD, 2),
                             "gain": round(q["length"] - 2 * WORD_PAD, 2)})
        cursor += length
    if ranges is None and said:
        protected = sorted((sub["start"] + a - WORD_PAD, sub["start"] + b + WORD_PAD)
                           for sub in read_draft(path)["subtitles"] for a, b, _ in said.get(cue_data.key(sub["id"]), []))
        safe = []
        for cut in cuts:
            parts = [(cut["start"], cut["end"])]
            for a, b in protected:
                parts = [(lo, hi) for start, end in parts
                         for lo, hi in ((start, min(end, a)), (max(start, b), end)) if hi > lo]
            safe.extend({"start": round(a, 6), "end": round(b, 6), "gain": b - a}
                        for a, b in parts if b - a >= 0.1)
        cuts = safe
    merged = []
    for cut in sorted(cuts, key=lambda c: c["start"]):
        if not all(math.isfinite(cut[k]) for k in ("start", "end")):
            raise VideoEditError("เวลาช่วงเงียบไม่ถูกต้อง · ถอดเสียงหรือตรวจช่วงตัดใหม่")
        cut["start"], cut["end"] = max(0.0, cut["start"]), min(total, cut["end"])
        if cut["end"] <= cut["start"]:
            continue
        if merged and cut["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], cut["end"])
        else:
            merged.append({"start": cut["start"], "end": cut["end"]})
    cuts = [{**c, "gain": c["end"] - c["start"]} for c in merged]
    removed = round(sum(c["gain"] for c in cuts), 2)

    preview = {"name": folder.name, "length": round(total, 2), "cuts": cuts,
               "removed": removed, "result_length": round(total - removed, 2)}
    if not apply or not cuts:
        return preview
    if removed >= total:
        raise VideoEditError("ช่วงตัดลบวิดีโอทั้งหมด · เลือกช่วงใหม่ให้เหลือเนื้อหา")
    if any(t.get("segments") and t is not video and t.get("type") != "text" for t in draft["tracks"]):
        raise VideoEditError("มีแทร็กเสียงหรือภาพเพิ่มเติมในโปรเจกต์ · ตัดเนื้อหาใน EditorKrab ก่อนเพิ่มแทร็กเหล่านี้ แล้วสร้างโปรเจกต์ CapCut ใหม่")

    def shifted(t: float) -> float:
        return t - sum(max(0.0, min(t, c["end"]) - c["start"]) for c in cuts)

    sidecars, regenerate = {}, []
    for name, key, duration_key in (("clipkit_overlays.json", None, "duration"), ("clipkit_placed.json", "broll", "dur")):
        file = folder / name
        if not file.is_file():
            continue
        data = json.loads(file.read_text(encoding="utf-8"))
        if key and not isinstance(data, dict):
            raise VideoEditError(f"{name}: รูปแบบภาพประกอบไม่ถูกต้อง")
        items = data.get(key, []) if key else data
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise VideoEditError(f"{name}: ต้องเป็นรายการ motion หรือภาพประกอบ")
        kept = []
        for item in items:
            a, duration = item.get("start"), item.get(duration_key)
            if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) for t in (a, duration)) or a < 0 or duration <= 0:
                raise VideoEditError(f"{name}: เวลาไม่ถูกต้อง · แก้ motion หรือภาพประกอบก่อนตัด")
            b = a + duration
            if any(c["start"] <= a and b <= c["end"] for c in cuts):
                continue
            if any(c["start"] < b and a < c["end"] for c in cuts):
                if key is None and isinstance(item.get("recipe"), dict):
                    regenerate.append((copy.deepcopy(item), item))
                else:
                    raise VideoEditError(f"ช่วงตัดทับ {name} · ลบ motion หรือภาพประกอบช่วงนี้ก่อนตัด แล้วสร้างใหม่หลังตัดเพื่อให้ตรงเสียง")
            item["start"] = round(shifted(a), 6)
            item[duration_key] = round(shifted(b) - shifted(a), 6)
            if isinstance(item.get("anchor"), dict) and item["anchor"].get("kind") == "timeline":
                item["anchor"] = {**item["anchor"], "start": item["start"]}
            kept.append(item)
        if key:
            data[key] = kept
        else:
            data = kept
        sidecars[name] = data
    original_subs = read_draft(path)["subtitles"]

    pieces = timeline_to_source(draft)
    base = copy.deepcopy(video["segments"][0])
    index = _index(draft)
    grade_template = [index[r] for r in base["extra_material_refs"] if r in index]

    keeps = []
    at = 0.0
    for cut in cuts:
        if cut["start"] > at:
            keeps.append((at, cut["start"]))
        at = max(at, cut["end"])
    if at < total:
        keeps.append((at, total))

    segments = []
    timeline = 0
    for a, b in keeps:
        for p0, p1, src0 in pieces:
            lo, hi = max(a, p0), min(b, p1)
            if hi - lo <= 0.01:
                continue
            segment = copy.deepcopy(base)
            segment["id"] = str(uuid.uuid4()).upper()
            segment["source_timerange"] = {"start": int(round((src0 + (lo - p0)) * US)),
                                           "duration": int(round((hi - lo) * US))}
            segment["target_timerange"] = {"start": timeline,
                                           "duration": int(round((hi - lo) * US))}
            timeline += int(round((hi - lo) * US))
            refs = []
            for key, item in grade_template:
                clone = copy.deepcopy(item)
                clone["id"] = str(uuid.uuid4()).upper()
                draft["materials"].setdefault(key, []).append(clone)
                refs.append(clone["id"])
            segment["extra_material_refs"] = refs
            segments.append(segment)

    for old in video["segments"]:
        for ref in old["extra_material_refs"]:
            key = index.get(ref, (None, None))[0]
            if key:
                draft["materials"][key] = [m for m in draft["materials"][key]
                                           if m.get("id") != ref]
    video["segments"] = segments

    for text in (t for t in draft["tracks"] if t["type"] == "text"):
        kept = []
        for segment in text["segments"]:
            span = segment["target_timerange"]
            a = shifted(span["start"] / US)
            b = shifted((span["start"] + span["duration"]) / US)
            span["start"] = int(round(a * US))
            span["duration"] = min(int(round((b - a) * US)), timeline - span["start"])
            if span["duration"] > 0:
                kept.append(segment)
        ordered = sorted(kept, key=lambda s: s["target_timerange"]["start"])
        for first, second in zip(ordered, ordered[1:]):
            end = first["target_timerange"]["start"] + first["target_timerange"]["duration"]
            if end > second["target_timerange"]["start"]:
                first["target_timerange"]["duration"] = second["target_timerange"]["start"] - first["target_timerange"]["start"]
        text["segments"] = [s for s in ordered if s["target_timerange"]["duration"] > 0]

    if words_file.is_file():  # word times are relative to their line, and cuts inside a line move them too
        for sub in original_subs:
            if cue_data.key(sub["id"]) in said:
                s0 = shifted(sub["start"])
                said[cue_data.key(sub["id"])] = [[round(shifted(sub["start"] + a) - s0, 6), round(shifted(sub["start"] + b) - s0, 6), w]
                                     for a, b, w in said[cue_data.key(sub["id"])]]
        sidecars = {"clipkit_words.json": said, **sidecars}
    draft["duration"] = timeline
    style_file = folder / "clipkit_style.json"
    if style_file.is_file():
        style = json.loads(style_file.read_text(encoding="utf-8"))
        if "motion_subject_zones" in style:
            zones = style["motion_subject_zones"]
            if not isinstance(zones, list) or any(not isinstance(zone, dict) or
                    any(isinstance(zone.get(key), bool) or not isinstance(zone.get(key), (int, float)) or not math.isfinite(zone[key])
                        for key in ("start", "end")) or not 0 <= zone["start"] < zone["end"] <= total for zone in zones):
                raise VideoEditError("เขตตัวแบบมีเวลาไม่ถูกต้อง · ตรวจพื้นที่ motion ก่อนตัด")
            style["motion_subject_zones"] = [{**zone, "start": shifted(zone["start"]), "end": shifted(zone["end"])}
                                             for zone in zones if shifted(zone["end"]) > shifted(zone["start"])]
            sidecars["clipkit_style.json"] = style
    import motion_plan
    with motion_plan.regenerate_cut(folder, draft, sidecars, regenerate, cuts, original_subs) as updated:
        _save(folder, draft, "trim", sidecars=updated)
    preview.update(applied=True, segments=len(segments),
                   result_length=round(timeline / US, 2))
    return preview


@project_lock.edit("target_path")
def copy_grade(source_path: str, target_path: str) -> dict[str, Any]:
    """Copy the colour grade (adjust effects, HSL, curves) from one draft onto
    every clip of another."""
    src_folder, dst_folder = Path(source_path), Path(target_path)
    src, dst = _load(src_folder), _load(dst_folder)
    src_index = _index(src)
    src_seg = _video_track(src)["segments"][0]
    template = [src_index[r] for r in src_seg["extra_material_refs"]
                if r in src_index and src_index[r][0] in GRADE_KEYS]
    if not template:
        raise VideoEditError(f"{src_folder.name} has no colour grade to copy")

    # The on/off switches matter as much as the values: a reference can attach
    # adjust effects but leave the adjust chain disabled, and copying only the
    # numbers would then apply a grade the reference never shows.
    flags = {k: src_seg.get(k) for k in
             ("enable_adjust", "enable_color_curves", "enable_smart_color_adjust",
              "enable_lut", "enable_hsl", "enable_hsl_curves", "enable_color_wheels",
              "enable_color_match_adjust", "enable_color_correct_adjust")
             if k in src_seg}

    dst_index = _index(dst)
    for segment in _video_track(dst)["segments"]:
        kept = []
        for ref in segment["extra_material_refs"]:
            key = dst_index.get(ref, (None, None))[0]
            if key in GRADE_KEYS:
                dst["materials"][key] = [m for m in dst["materials"][key] if m.get("id") != ref]
                continue
            kept.append(ref)
        for key, item in template:
            clone = copy.deepcopy(item)
            clone["id"] = str(uuid.uuid4()).upper()
            dst["materials"].setdefault(key, []).append(clone)
            kept.append(clone["id"])
        segment["extra_material_refs"] = kept
        segment.update(flags)

    _save(dst_folder, dst, "grade")
    return {"from": src_folder.name, "to": dst_folder.name,
            "clips": len(_video_track(dst)["segments"]),
            "copied": len(template), "flags": flags}


_TRANSCRIPT_MEDIA_CACHE: dict[str, dict] = {}


def _from_transcript(source: str, start: float, end: float, *, corrections: dict | None = None) -> list[dict[str, Any]] | None:
    """Reuse the source transcript at complete-word edges, including edges inside an ASR row; None = transcribe."""
    from video_edit import slice_transcript, transcript_file
    import web_ai_identity as identity

    start, end = round(start, 6), round(end, 6)

    tf = transcript_file(source)
    meta, part = tf.with_suffix(".meta.json"), tf.with_suffix(".part.json")
    if not tf.is_file() or not meta.is_file() or part.exists():
        return None
    media, cached = identity._media(Path(source), _TRANSCRIPT_MEDIA_CACHE)
    _TRANSCRIPT_MEDIA_CACHE[media["path_id"]] = cached
    before = (identity._stat(tf), identity._stat(meta))
    try:
        provenance = json.loads(meta.read_text(encoding="utf-8"))
        phrases = json.loads(tf.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VideoEditError("อ่าน transcript หรือข้อมูลต้นทางไม่สำเร็จ · ถอดเสียงใหม่ก่อนใช้ซับ") from exc
    if not isinstance(provenance, dict) or provenance.get("schema_version") != 2 or provenance.get("sha256") != media["sha256"]:
        return None
    if not phrases or any("words" not in p for p in phrases):
        return None
    try:
        phrases = slice_transcript(phrases, start, end)
    except VideoEditError:
        return None
    if (part.exists() or identity._stat(Path(source)) != media["stat"]
            or (identity._stat(tf), identity._stat(meta)) != before):
        raise VideoEditError("ฟุตหรือ transcript เปลี่ยนระหว่างอ่าน · ลองถอดเสียงใหม่")
    import spelling
    known = spelling.load() if corrections is None else corrections
    return [{"start": round(p["start"] - start, 2), "end": round(p["end"] - start, 2),
             "text": spelling.fix("".join(w for _, _, w in p["words"]) or p["text"], known),
             "words": [[round(a - start, 2), round(b - start, 2), w] for a, b, w in p["words"]]}
            for p in phrases if start <= p["start"] and p["end"] <= end]


def transcribe_draft(path: str) -> dict[str, Any]:
    """Transcribe what the draft actually plays — segment by segment, with the
    times already converted to the draft's timeline."""
    from video_edit import transcribe

    folder = Path(path)
    import spelling
    corrections = spelling.load(folder)
    draft = _load(folder)
    source = next((m.get("path") for m in draft["materials"].get("videos", []) if m.get("path")), None)
    if not source or not Path(source).is_file():
        raise VideoEditError(f"source video not found: {source}")

    import web_ai_identity as identity
    with project_lock.writer(source, recover=False):
        source_stat = identity._stat(Path(source))
        lines: list[dict[str, Any]] = []
        cursor = 0.0
        for segment in _video_track(draft)["segments"]:
            start = segment["source_timerange"]["start"] / US
            length = segment["source_timerange"]["duration"] / US
            for phrase in _from_transcript(source, start, start + length, corrections={}) or transcribe(source, start, start + length):
                cue_id = str(uuid.uuid4()).upper()
                spoken = [[round(a - phrase["start"], 2), round(b - phrase["start"], 2), w]
                          for a, b, w in phrase.get("words", [])]
                lines.append({"id": cue_id, "words": spoken, "start": round(cursor + phrase["start"], 2),
                              "end": round(cursor + phrase["end"], 2),
                              "text": spelling.fix("".join(w for _, _, w in phrase.get("words", [])) or phrase["text"], corrections)})
            cursor += length
        if identity._stat(Path(source)) != source_stat:
            raise VideoEditError("ฟุตเปลี่ยนระหว่างถอดเสียง · ถอดเสียงใหม่ก่อนบันทึกซับ")

    lines.sort(key=lambda p: p["start"])
    for first, second in zip(lines, lines[1:]):
        if first["end"] > second["start"]:
            first["end"] = round(max(second["start"] - 0.03, first["start"] + 0.3), 2)
    return {"name": folder.name, "count": len(lines), "subtitles": lines,
            "timeline": round(cursor, 2)}


# ── animations, reused from the projects that already have them ──────

def list_animations(root: str | None = None) -> list[dict[str, Any]]:
    """Animations available locally, learned from the projects on this machine.

    CapCut animations reference a file in its own cache, so only ones already
    downloaded here can be reused — hence reading them out of real drafts.
    """
    seen: dict[str, dict[str, Any]] = {}
    for folder in sorted(_drafts_root(root).iterdir()):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            draft = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        track_of = {}
        for track in draft.get("tracks", []):
            for segment in track.get("segments", []):
                for ref in segment.get("extra_material_refs", []):
                    track_of[ref] = track["type"]
        for material in draft["materials"].get("material_animations") or []:
            for animation in material.get("animations") or []:
                name = animation.get("name")
                if not name or not animation.get("path"):
                    continue
                target = track_of.get(material["id"], "video")
                key = f"{name}|{target}"
                entry = seen.setdefault(key, {
                    "name": name, "target": target,
                    "kind": animation.get("type"),
                    "category": animation.get("category_name"),
                    "duration": round(animation.get("duration", 500000) / US, 2),
                    "source": folder.name, "used": 0,
                    "available": Path(animation["path"]).exists(),
                })
                entry["used"] += 1
    return sorted(seen.values(), key=lambda a: (-a["used"], a["name"]))


def _find_animation(name: str, target: str, root: str | None = None) -> dict[str, Any]:
    for folder in sorted(_drafts_root(root).iterdir()):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            draft = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        track_of = {}
        for track in draft.get("tracks", []):
            for segment in track.get("segments", []):
                for ref in segment.get("extra_material_refs", []):
                    track_of[ref] = track["type"]
        for material in draft["materials"].get("material_animations") or []:
            names = [a.get("name") for a in material.get("animations") or []]
            if name in names and track_of.get(material["id"], "video") == target:
                return copy.deepcopy(material)
    raise VideoEditError(f"animation '{name}' for {target} not found in any project — "
                         f"add it once in CapCut, then it can be reused")


@project_lock.edit
def apply_animation(path: str, name: str, target: str = "text",
                    duration: float | None = None) -> dict[str, Any]:
    """Put one animation on every clip (or every subtitle) of a draft."""
    if target not in ("text", "video"):
        raise VideoEditError("target must be 'text' or 'video'")

    folder = Path(path)
    draft = _load(folder)
    template = _find_animation(name, target)
    if duration is not None:
        for animation in template.get("animations") or []:
            animation["duration"] = int(round(duration * US))

    index = _index(draft)
    track = _text_track(draft) if target == "text" else _video_track(draft)
    if track is None or not track["segments"]:
        raise VideoEditError(f"draft has nothing on the {target} track")

    applied = 0
    for segment in track["segments"]:
        kept = []
        for ref in segment["extra_material_refs"]:
            if index.get(ref, (None,))[0] == "material_animations":
                draft["materials"]["material_animations"] = [
                    m for m in draft["materials"]["material_animations"] if m["id"] != ref]
                continue
            kept.append(ref)
        clone = copy.deepcopy(template)
        clone["id"] = str(uuid.uuid4()).upper()
        draft["materials"].setdefault("material_animations", []).append(clone)
        # CapCut keeps the animation slot right after speed/placeholder
        kept.insert(min(2, len(kept)), clone["id"])
        segment["extra_material_refs"] = kept
        applied += 1

    _save(folder, draft, "anim")
    return {"name": folder.name, "animation": name, "target": target,
            "applied": applied,
            "duration": round((template["animations"][0]["duration"]) / US, 2)
            if template.get("animations") else None}


@project_lock.edit
def clear_animations(path: str, target: str = "text") -> dict[str, Any]:
    """Take the animations back off, in case the look is not wanted."""
    folder = Path(path)
    draft = _load(folder)
    index = _index(draft)
    track = _text_track(draft) if target == "text" else _video_track(draft)
    if track is None:
        raise VideoEditError(f"draft has no {target} track")
    removed = 0
    for segment in track["segments"]:
        kept = []
        for ref in segment["extra_material_refs"]:
            if index.get(ref, (None,))[0] == "material_animations":
                draft["materials"]["material_animations"] = [
                    m for m in draft["materials"]["material_animations"] if m["id"] != ref]
                removed += 1
                continue
            kept.append(ref)
        segment["extra_material_refs"] = kept
    _save(folder, draft, "animclear")
    return {"name": folder.name, "target": target, "removed": removed}


# ── sound effects, cloned from a project that already uses them ──────

def list_sounds(root: str | None = None) -> list[dict[str, Any]]:
    """Sound effects used in the projects here, with whether the file still exists."""
    seen: dict[str, dict[str, Any]] = {}
    for folder in sorted(_drafts_root(root).iterdir()):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            draft = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        for audio in draft["materials"].get("audios") or []:
            name = audio.get("name") or Path(audio.get("path", "")).name
            if not name or not audio.get("path"):
                continue
            entry = seen.setdefault(name, {
                "name": name, "path": audio["path"],
                "duration": round(audio.get("duration", 0) / US, 2),
                "kind": audio.get("type"), "source": folder.name, "used": 0,
                "available": Path(audio["path"]).exists(),
            })
            entry["used"] += 1
    return sorted(seen.values(), key=lambda a: -a["used"])


def _borrow_sound(name: str, root: str | None = None) -> tuple[dict, dict, list[dict]]:
    """An audio material + segment shape + its little helper materials."""
    for folder in sorted(_drafts_root(root).iterdir()):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            other = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        match = next((a for a in other["materials"].get("audios") or []
                      if (a.get("name") or Path(a.get("path", "")).name) == name), None)
        if not match:
            continue
        index = _index(other)
        for track in other["tracks"]:
            if track["type"] != "audio":
                continue
            seg = next((s for s in track["segments"] if s["material_id"] == match["id"]), None)
            if not seg:
                continue
            helpers = [copy.deepcopy(index[r][1]) for r in seg["extra_material_refs"] if r in index]
            helper_keys = [index[r][0] for r in seg["extra_material_refs"] if r in index]
            return (copy.deepcopy(match), copy.deepcopy(seg),
                    list(zip(helper_keys, helpers)))
    raise VideoEditError(f"sound '{name}' is not used in any project here")


@project_lock.edit
def add_sound_on_subtitles(path: str, sound: str, volume: float = 0.35,
                           every_line: bool = True, offset: float = 0.0) -> dict[str, Any]:
    """Drop a sound effect at the start of each subtitle — the pairing used in
    hand-edited projects, where a text animation gets a pop underneath it."""
    folder = Path(path)
    draft = _load(folder)
    text = _text_track(draft)
    if text is None or not text["segments"]:
        raise VideoEditError("this project has no subtitles yet")

    material, seg_shape, helpers = _borrow_sound(sound)
    if not Path(material.get("path", "")).exists():
        raise VideoEditError(f"sound file missing on disk: {material.get('path')}")

    material["id"] = str(uuid.uuid4()).upper()
    draft["materials"].setdefault("audios", []).append(material)
    length = material.get("duration", int(0.7 * US))

    starts = []
    previous_end = -99.0
    for seg in sorted(text["segments"], key=lambda s: s["target_timerange"]["start"]):
        start = seg["target_timerange"]["start"] / US + offset
        if not every_line and start - previous_end < 2.0:
            continue
        starts.append(max(0.0, start))
        previous_end = seg["target_timerange"]["start"] / US + \
            seg["target_timerange"]["duration"] / US

    limit = draft.get("duration", 0)
    segments = []
    for start in starts:
        begin = int(round(start * US))
        if begin >= limit:
            continue
        seg = copy.deepcopy(seg_shape)
        seg["id"] = str(uuid.uuid4()).upper()
        seg["material_id"] = material["id"]
        seg["source_timerange"] = {"start": 0, "duration": min(length, limit - begin)}
        seg["target_timerange"] = {"start": begin, "duration": min(length, limit - begin)}
        seg["volume"] = volume
        seg["last_nonzero_volume"] = volume
        refs = []
        for key, helper in helpers:
            clone = copy.deepcopy(helper)
            clone["id"] = str(uuid.uuid4()).upper()
            draft["materials"].setdefault(key, []).append(clone)
            refs.append(clone["id"])
        seg["extra_material_refs"] = refs
        segments.append(seg)

    track = {"type": "audio", "attribute": 0, "flag": 0, "is_default_name": False,
             "id": str(uuid.uuid4()).upper(), "segments": segments}
    draft["tracks"].append(track)
    _save(folder, draft, "sfx")
    return {"name": folder.name, "sound": sound, "added": len(segments),
            "volume": volume, "file": material.get("path")}


@project_lock.edit
def clear_sounds(path: str) -> dict[str, Any]:
    """Remove audio tracks this tool added (keeps the clip's own sound)."""
    folder = Path(path)
    draft = _load(folder)
    before = len(draft["tracks"])
    draft["tracks"] = [t for t in draft["tracks"] if t["type"] != "audio"]
    _save(folder, draft, "sfxclear")
    return {"name": folder.name, "removed_tracks": before - len(draft["tracks"])}


# ── background music library ─────────────────────────────────────────

def music_dirs() -> list[str]:
    """Music library folders; read on use so the app starts before Settings sets stock_music."""
    return [d for d in os.environ.get("MUSIC_DIRS", "").split(";") if d] or [kitconfig.need("stock_music")]
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
_MOODS = {
    "epic": "อลังการ", "cinematic": "ภาพยนตร์", "uplifting": "ฮึกเหิม", "inspiring": "สร้างแรงบันดาลใจ",
    "piano": "เปียโน", "lofi": "โลไฟ ชิล", "chill": "โลไฟ ชิล", "ambient": "บรรยากาศ ชิล",
    "corporate": "องค์กร", "minimal": "มินิมอล", "technology": "เทคโนโลยี",
    "emotional": "ซึ้ง", "hopeful": "มีความหวัง", "calm": "สงบ",
    "luxury": "หรู", "elegant": "เรียบหรู", "upbeat": "จังหวะสนุก",
    "team": "ทีมเวิร์ก", "motivation": "ปลุกใจ", "warm": "อบอุ่น",
}


def _music_meta(path: Path) -> dict[str, Any]:
    from video_edit import FFPROBE, NO_WINDOW
    import subprocess
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, creationflags=NO_WINDOW, text=True, timeout=60)
    try:
        duration = round(float(out.stdout.strip()), 1)
    except ValueError as exc:
        raise VideoEditError(f"ffprobe could not read duration of {path}: {out.stderr.strip()[-300:]}") from exc
    name = path.stem.lower()
    moods = sorted({thai for key, thai in _MOODS.items() if key in name})
    bpm = None
    match = re.search(r"(\d{2,3})bpm", name)
    if match:
        bpm = int(match.group(1))
    return {"name": path.name, "path": str(path), "duration": duration,
            "moods": moods, "bpm": bpm, "folder": path.parent.name}


def list_music(dirs: list[str] | None = None) -> list[dict[str, Any]]:
    """Every music file in the known folders, with a rough mood read off the name."""
    found: list[dict[str, Any]] = []
    for base in (dirs or music_dirs()):
        root = Path(base)
        if not root.is_dir():
            continue
        for entry in sorted(root.rglob("*")):
            if entry.is_file() and entry.suffix.lower() in AUDIO_SUFFIXES:
                if "_ไม่ผ่าน" in entry.parts or entry.parent.name == "sfx":
                    continue
                found.append(_music_meta(entry))
    return found


@project_lock.edit
def add_music(path: str, music_path: str, volume: float = 0.10,
              fade_out: float = 5.0, start: float = 0.0,
              fade_in: float | None = None) -> dict[str, Any]:
    """Lay a music bed under the whole project, the way finished clips
    were done by hand: about 10% volume, a ~5s fade at the end, and a ~3s fade
    in when the song is entered part-way rather than from its first bar."""
    if fade_in is None:
        fade_in = 3.0 if start > 0 else 0.0
    folder = Path(path)
    music = Path(music_path)
    if not music.is_file():
        raise VideoEditError(f"music file not found: {music_path}")

    draft = _load(folder)
    timeline = draft.get("duration", 0)
    if timeline <= 0:
        raise VideoEditError("project timeline is empty")

    meta = _music_meta(music)
    source_len = int(round(meta["duration"] * US)) or timeline
    material, seg_shape, helpers = _borrow_sound_any()

    material = copy.deepcopy(material)
    material.update({
        "id": str(uuid.uuid4()).upper(),
        "name": music.stem,
        "path": str(music.as_posix()),
        "duration": source_len,
        # what CapCut itself writes for a song imported from disk
        "type": "extract_music",
        "category_name": "local",
        "local_material_id": str(uuid.uuid4()).upper(),
    })
    draft["materials"].setdefault("audios", []).append(material)

    begin = int(round(start * US))
    length = min(timeline, source_len - begin) if source_len > begin else timeline
    seg = copy.deepcopy(seg_shape)
    seg["id"] = str(uuid.uuid4()).upper()
    seg["material_id"] = material["id"]
    seg["source_timerange"] = {"start": begin, "duration": length}
    seg["target_timerange"] = {"start": 0, "duration": length}
    seg["volume"] = volume
    seg["last_nonzero_volume"] = volume
    refs = []
    for key, helper in helpers:
        clone = copy.deepcopy(helper)
        clone["id"] = str(uuid.uuid4()).upper()
        draft["materials"].setdefault(key, []).append(clone)
        refs.append(clone["id"])

    if fade_out > 0 or fade_in > 0:
        fade = {"id": str(uuid.uuid4()).upper(), "type": "audio_fade",
                "fade_in_duration": int(round(fade_in * US)),
                "fade_out_duration": int(round(fade_out * US)),
                "fade_type": 0}
        draft["materials"].setdefault("audio_fades", []).append(fade)
        # CapCut keeps the fade third, after speeds and placeholder_infos
        refs.insert(min(2, len(refs)), fade["id"])
    seg["extra_material_refs"] = refs

    draft["tracks"].append({"type": "audio", "attribute": 0, "flag": 0,
                            "is_default_name": False,
                            "id": str(uuid.uuid4()).upper(), "segments": [seg]})
    _save(folder, draft, "music")
    return {"name": folder.name, "music": music.name, "volume": volume,
            "covers": round(length / US, 2), "timeline": round(timeline / US, 2),
            "looped": source_len < timeline}


def _borrow_sound_any(root: str | None = None) -> tuple[dict, dict, list]:
    """Any audio material/segment shape to model a new one on."""
    for folder in sorted(_drafts_root(root).iterdir()):
        if not (folder / "draft_content.json").is_file():
            continue
        try:
            other = _load(folder)
        except (VideoEditError, json.JSONDecodeError):
            continue
        index = _index(other)
        for track in other["tracks"]:
            if track["type"] != "audio" or not track["segments"]:
                continue
            seg = track["segments"][0]
            material = index.get(seg["material_id"], (None, None))[1]
            if not material:
                continue
            helpers = [(index[r][0], copy.deepcopy(index[r][1]))
                       for r in seg["extra_material_refs"]
                       if r in index and index[r][0] != "audio_fades"]
            return copy.deepcopy(material), copy.deepcopy(seg), helpers
    raise VideoEditError("no project here has an audio clip to model on")


# ── filling in the subtitles for footage added after the first pass ───

def subtitle_gaps(path: str, min_gap: float = 1.0, pad: float = 0.15) -> list[dict[str, Any]]:
    """Stretches of the timeline that play sound but carry no subtitle.

    This is what happens after someone lengthens a clip or drops a new one in:
    the old lines keep their place, and the new footage is left bare.
    """
    folder = Path(path)
    draft = _load(folder)
    video = _video_track(draft)
    max_video = max(((s["target_timerange"]["start"] + s["target_timerange"]["duration"]) / US)
                    for s in video.get("segments", [])) if video and video.get("segments") else 0.0
    limit = max(draft.get("duration", 0) / US, max_video)

    covered: list[tuple[float, float]] = []
    texts_dict = {t["id"]: t.get("content", "") for t in draft.get("materials", {}).get("texts", [])}

    for track in draft.get("tracks", []):
        if track.get("type") == "text":
            for seg in track.get("segments", []):
                mat_id = seg.get("material_id")
                content = texts_dict.get(mat_id, "")
                has_text = True
                if content:
                    try:
                        data = json.loads(content)
                        if not str(data.get("text", "")).strip():
                            has_text = False
                    except Exception:
                        pass
                if not has_text:
                    continue
                start = seg["target_timerange"]["start"] / US
                end = start + seg["target_timerange"]["duration"] / US
                covered.append((max(0.0, start - pad), end + pad))
    covered.sort()

    merged: list[list[float]] = []
    for start, end in covered:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    gaps = []
    cursor = 0.0
    for start, end in merged + [[limit, limit]]:
        if start - cursor >= min_gap:
            gaps.append({"start": round(cursor, 2), "end": round(start, 2),
                         "duration": round(start - cursor, 2)})
        cursor = max(cursor, end)
    return gaps


@project_lock.edit
def fill_subtitle_gaps(path: str, min_gap: float = 1.0, preview: bool = False,
                       pad: float = 0.15) -> dict[str, Any]:
    """Transcribe only the bare stretches and add those lines, leaving every
    subtitle that is already on the timeline exactly where it is."""
    from video_edit import transcribe

    folder = Path(path)
    draft = _load(folder)
    source = next((m.get("path") for m in draft["materials"].get("videos", []) if m.get("path")), None)
    if not source or not Path(source).is_file():
        raise VideoEditError(f"source video not found: {source}")

    gaps = subtitle_gaps(path, min_gap=min_gap, pad=pad)
    pieces = timeline_to_source(draft)

    lines: list[dict[str, Any]] = []
    for gap in gaps:
        for tl_start, tl_end, src_start in pieces:
            lo = max(gap["start"], tl_start)
            hi = min(gap["end"], tl_end)
            if hi - lo < 0.3:
                continue
            offset = src_start + (lo - tl_start)
            for phrase in transcribe(source, offset, offset + (hi - lo)):
                start = lo + phrase["start"]
                end = min(lo + phrase["end"], hi)
                if end - start < 0.3:
                    continue
                lines.append({"id": str(uuid.uuid4()).upper(), "start": round(start, 2), "end": round(end, 2),
                              "text": phrase["text"], "words": [[round(a - phrase["start"], 2), round(b - phrase["start"], 2), w]
                                  for a, b, w in phrase.get("words", [])]})

    lines.sort(key=lambda p: p["start"])
    for first, second in zip(lines, lines[1:]):
        if first["end"] > second["start"]:
            first["end"] = round(max(second["start"] - 0.03, first["start"] + 0.3), 2)

    result = {"name": folder.name, "gaps": gaps, "lines": lines,
              "found": len(lines), "added": 0}
    if preview or not lines:
        return result

    written = set_subtitles(path, lines, replace=False)
    result["added"] = written["added"]
    result["subtitles"] = written["subtitles"]
    return result


def relisten(path: str, text: str, pad: float = 0.4, cue_id: str | None = None) -> str:
    """What the speech model hears again over one subtitle line's time (a little wider), for a line that reads
    cut short or missing words. Nothing on the timeline changes."""
    from video_edit import transcribe

    draft = _load(Path(path))
    source = next((m.get("path") for m in draft["materials"].get("videos", []) if m.get("path")), None)
    if not source or not Path(source).is_file():
        raise VideoEditError(f"source video not found: {source}")
    line = cue_data.select(read_draft(path)["subtitles"], text, cue_id)
    heard = []
    for tl_start, tl_end, src_start in timeline_to_source(draft):
        lo, hi = max(line["start"] - pad, tl_start), min(line["end"] + pad, tl_end)
        if hi - lo >= 0.3:
            offset = src_start + (lo - tl_start)
            heard += [p["text"] for p in transcribe(source, offset, offset + (hi - lo))]
    return " ".join(heard)


@project_lock.edit
def set_line_time(path: str, text: str, start: float, end: float, cue_id: str | None = None) -> dict[str, Any]:
    """Move / stretch one subtitle line on the timeline (the timeline editor)."""
    if end - start < 0.3:
        raise VideoEditError("a subtitle line must last at least 0.3 s")
    folder = Path(path)
    selected = cue_data.select(read_draft(path)["subtitles"], text, cue_id)
    draft = _load(folder)
    index = _index(draft)
    for seg in (_text_track(draft) or {}).get("segments", []):
        m = index.get(seg["material_id"], (None, None))[1]
        if m and seg["id"] == selected["id"]:
            seg["target_timerange"] = {"start": int(round(start * US)), "duration": int(round((end - start) * US))}
            _save(folder, draft, "linetime")
            return {"start": start, "end": end}
    raise VideoEditError(f"subtitle line not found: {text}")


def _repick(picked: list, old: str, new: str) -> list:
    """Carry the agent's [[lead, punch], ...] picks for a line over to its fixed text: the changed letters go
    into the piece where the change starts (render._pair compares the pieces with spaces dropped)."""
    tight = lambda t: "".join(t.split())  # noqa: E731
    a, b = tight(old), tight(new)
    p = 0
    while p < min(len(a), len(b)) and a[p] == b[p]:
        p += 1
    q = 0
    while q < min(len(a), len(b)) - p and a[-1 - q] == b[-1 - q]:
        q += 1
    cut_end, put = len(a) - q, b[p:len(b) - q]
    out, at = [], 0
    for pair in picked:
        pieces = []
        for piece in pair[:2]:
            t = tight(piece)
            lo, hi = at, at + len(t)
            at = hi
            if hi < p or lo > cut_end or (lo == hi and lo != p):
                pieces.append(piece)
                continue
            keep_head, keep_tail = t[:max(0, p - lo)], t[max(0, cut_end - lo):]
            pieces.append(keep_head + (put if lo <= p <= hi else "") + keep_tail)
            if lo <= p <= hi:
                put = ""  # placed once
        out.append(pieces + list(pair[2:]))
    if tight("".join(x[0] + x[1] for x in out)) != b:
        raise VideoEditError(f"could not carry the punch picks over to the fixed line: {new}")
    return out


@project_lock.edit
def set_line_text(path: str, old: str, new: str, cue_id: str | None = None, *, learn_global: bool = False,
                  picks_override: list[list[str | dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Change one subtitle line's words (the timeline editor). The ClipKit side files keyed by the line text
    (word times, punch picks) are moved to the new text so they stay attached."""
    folder = Path(path)
    import spelling
    spelling.load(folder)  # Validate both retained machine rules and this project's overrides first.
    corrections, learned = spelling._updated(old, new, spelling._read(spelling._file(folder)))
    global_rules = spelling._updated(old, new, spelling._read(spelling.FILE))[0] if learn_global else None
    rows = read_draft(path)["subtitles"]
    selected = cue_data.select(rows, old, cue_id)
    sidecars = {name: cue_data.load(folder, name, rows) for name in
                ("clipkit_words.json", "clipkit_punch.json", "clipkit_caption.json")}
    selected_key = cue_data.key(selected["id"])
    if picks_override is not None:
        import subtitle_plan
        sidecars["clipkit_punch.json"][selected_key] = subtitle_plan.validate_items(new, picks_override, cue_id=selected["id"])
    elif selected_key in sidecars["clipkit_punch.json"]:
        sidecars["clipkit_punch.json"][selected_key] = _repick(sidecars["clipkit_punch.json"][selected_key], old, new)
    import web_ai_json
    web_ai_json.validate_subtitles(path, picks=sidecars["clipkit_punch.json"],
        rows=[{**r, "text": new} if r["id"] == selected["id"] else r for r in rows])
    draft = _load(folder)
    index = _index(draft)
    text = _text_track(draft)
    hit = 0
    for seg in (text or {}).get("segments", []):
        m = index.get(seg["material_id"], (None, None))[1]
        if not m:
            continue
        body = json.loads(m["content"])
        if seg["id"] != selected["id"]:
            continue
        if sum(s["material_id"] == seg["material_id"] for s in text["segments"]) > 1:
            m = copy.deepcopy(m)
            m["id"] = seg["material_id"] = str(uuid.uuid4()).upper()
            draft["materials"]["texts"].append(m)
        body["text"] = new
        for st in body.get("styles", []):
            st["range"] = [0, len(new)]
        m["content"] = json.dumps(body, ensure_ascii=False)
        hit += 1
    if not hit:
        raise VideoEditError(f"subtitle line not found: {old}")
    _save(folder, draft, "linetext", corrections=corrections if learned else None,
          sidecars={name: data for name, data in sidecars.items() if (folder / name).is_file()
                    or name == "clipkit_punch.json" and picks_override is not None})
    if learn_global and learned:
        atomic.write_text(spelling.FILE, json.dumps(global_rules, ensure_ascii=False, indent=1))
    return {"changed": hit}
