"""Bind an AI packet to its exact source and editing state; verification never writes."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path
from typing import Any

import atomic
from video_edit import VideoEditError

VERSION = 2
KIT = Path(__file__).resolve().parents[1]
PRESETS = KIT / "presets"
MOTION = KIT / "motion"
PROMPTS = KIT / "prompts"
DEFAULT_FONT = KIT / "fonts" / "Kanit-Bold.ttf"
SIDECARS = ("draft_content.json", "clipkit.json", "clipkit_words.json", "clipkit_cues.json",
            "clipkit_punch.json", "clipkit_caption.json", "clipkit_hook.json", "clipkit_style.json",
            "clipkit_overlays.json", "clipkit_placed.json", "clipkit_broll.json", "clipkit_seal.json", "clipkit_corrections.json",
            "clipkit_insert_assets.json")
DEPENDENCIES = ("web_ai_identity.py", "web_ai_json.py", "web_ai_transaction.py", "video_edit.py",
                "capcut_edit.py", "cue_data.py", "render.py", "video_editor.py", "kit_settings.py",
                "story_selection.py", "subtitle_plan.py", "spelling.py", "insert_plan.py", "insert_geometry.py", "motion_plan.py",
                "motion_runtime.js", "hf_build.py", "capcut_build.py", "capcut_templates.json",
                "draft_quality.py", "delivery_manifest.py")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _canonical(path: str | Path) -> Path:
    return Path(os.path.normcase(str(Path(path).resolve())))


def _path_id(path: str | Path) -> str:
    return _hash(str(_canonical(path)))


def state_dir(step: str, target: str | Path) -> Path:
    if step not in ("stories", "clip"):
        raise VideoEditError(f"unknown step: {step}")
    path = _canonical(target)
    return path.with_name(path.name + ".web-ai-state") if step == "stories" else path / ".clipkit-web-ai"


def _json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise VideoEditError(f"อ่านข้อมูลอ้างอิง AI ไม่สำเร็จ: {path.name}") from exc


def _file(path: Path, required: bool = False) -> str | None:
    if not path.is_file():
        if required:
            raise VideoEditError(f"ไม่พบไฟล์ที่คำสั่ง AI ต้องอ้างอิง: {path.name}")
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stat(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]


def _media(path: Path, cache: dict[str, dict]) -> tuple[dict, dict]:
    path = _canonical(path)
    if not path.is_file():
        raise VideoEditError(f"ไม่พบฟุตต้นทาง: {path.name} · เชื่อมต่อไฟล์ก่อนส่งคำสั่ง AI ใหม่")
    key, before = _path_id(path), _stat(path)
    previous = cache.get(key, {})
    if not isinstance(previous, dict):
        raise VideoEditError("ข้อมูลอ้างอิงฟุตสำหรับ AI ไม่ถูกต้อง · ส่งออกคำสั่ง AI ใหม่")
    if previous.get("stat") == before and re.fullmatch(r"[0-9a-f]{64}", str(previous.get("sha256", ""))):
        sha = previous["sha256"]
    else:
        # ASR metadata has no source stat, so its SHA alone cannot prove a copied/replaced file is fresh.
        sha = _file(path, required=True)
    if _stat(path) != before:
        raise VideoEditError("ฟุตต้นทางกำลังเปลี่ยน · รอให้บันทึกไฟล์เสร็จแล้วส่งคำสั่ง AI ใหม่")
    identity = {"path_id": key, "sha256": sha, "stat": before}
    return identity, {"stat": before, "sha256": sha, "path": str(path)}


def _preferences(target: Path, step: str, values: dict | None, raw: Path | None) -> dict:
    if values is not None and (not isinstance(values, dict) or set(values) - {"max_minutes"}):
        raise VideoEditError("preferences ต้องมีเฉพาะ max_minutes")
    source = target if step == "stories" else raw
    saved = _json(Path(str(source) + ".story-preferences.json"), {}) if source else {}
    if not isinstance(saved, dict):
        raise VideoEditError("story-preferences.json ต้องเป็น object")
    value = values.get("max_minutes", saved.get("max_minutes")) if values is not None else saved.get("max_minutes")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                              or not math.isfinite(value) or value <= 0):
        raise VideoEditError("ความยาวต้องเป็นจำนวนนาทีที่มากกว่า 0")
    return {"max_minutes": value}


def _referenced(path: str | Path, folder: Path) -> Path:
    if not isinstance(path, (str, Path)) or not str(path).strip():
        raise VideoEditError("โปรเจกต์มีพาธไฟล์อ้างอิงไม่ถูกต้อง")
    file = Path(path)
    return _canonical(file if file.is_absolute() else folder / file)


def _snapshot(step: str, target: str | Path, preferences: dict | None, cache: dict,
              *, require_transcript: bool = True, operation: str | None = None) -> tuple[dict, dict]:
    if operation is not None and (step != "clip" or operation not in {"inserts", "manual"}):
        raise VideoEditError("operation ของคำสั่ง AI ไม่ถูกต้อง")
    target = _canonical(target)
    if step == "stories" and require_transcript and Path(str(target) + ".transcript.part.json").exists():
        raise VideoEditError("การถอดเสียงยังไม่เสร็จ · ทำต่อให้ครบก่อนใช้รายการเรื่องหรือคำสั่ง AI")
    files, sources, updated = {}, {}, {}
    if step == "stories" and not require_transcript:
        pending = Path(str(target) + ".transcript.part.json")
        if pending.exists():
            files["source:.transcript.part.json"] = _file(pending, required=True)
    raw = target if step == "stories" else None
    if step == "clip":
        files = {name: _file(target / name, required=name == "draft_content.json") for name in SIDECARS}
        files["Timelines/project.json"] = _file(target / "Timelines" / "project.json")
        for file in sorted(target.glob("Timelines/*/draft_content.json")):
            files[file.relative_to(target).as_posix()] = _file(file, required=True)
        draft = _json(target / "draft_content.json")
        meta = _json(target / "clipkit.json", {})
        if not isinstance(draft, dict) or not isinstance(meta, dict):
            raise VideoEditError("ข้อมูลโปรเจกต์สำหรับ AI ต้องเป็น object")
        if meta.get("raw"):
            raw = _referenced(meta["raw"], target)
        materials = draft.get("materials", {})
        if not isinstance(materials, dict):
            raise VideoEditError("draft materials ต้องเป็น object")
        if any(not isinstance(materials.get(group, []), list) for group in ("videos", "audios", "texts")):
            raise VideoEditError("draft materials ต้องเป็นรายการไฟล์")
        media = [m["path"] for group in ("videos", "audios") for m in materials.get(group, [])
                 if isinstance(m, dict) and m.get("path")]
        import insert_plan
        assets = insert_plan.catalog(target)
        media += [asset["file"] for asset in assets["assets"].values()]
        for name in ("clipkit_broll.json", "clipkit_placed.json", "clipkit_overlays.json"):
            saved = _json(target / name)
            items = (saved.values() if name == "clipkit_broll.json" and isinstance(saved, dict) else
                     saved.get("broll", []) if name == "clipkit_placed.json" and isinstance(saved, dict) else
                     saved if isinstance(saved, list) else [])
            media += [item["file"] for item in items if isinstance(item, dict) and item.get("file")]
            if name == "clipkit_placed.json" and isinstance(saved, dict) and isinstance(saved.get("music"), dict) and saved["music"].get("file"):
                media.append(saved["music"]["file"])
        style = _json(target / "clipkit_style.json", {})
        if not isinstance(style, dict):
            raise VideoEditError("clipkit_style.json ต้องเป็น object")
        preset = PRESETS / f"{style.get('preset') or 'default'}.json"
        files["preset"] = _file(preset, required=True)
        preset_data = _json(preset)
        if not isinstance(preset_data, dict):
            raise VideoEditError("preset สำหรับ AI ต้องเป็น object")
        fonts = [str(DEFAULT_FONT)]
        if preset_data.get("font"):
            fonts.append(preset_data["font"])
        for material in materials.get("texts", []):
            if not isinstance(material, dict):
                raise VideoEditError("ข้อมูลฟอนต์ในซับไม่ถูกต้อง")
            if material.get("font_path"):
                fonts.append(material["font_path"])
            content = material.get("content")
            if content:
                try:
                    parsed = json.loads(content)
                    styles = parsed.get("styles", []) if isinstance(parsed, dict) else None
                    if not isinstance(styles, list):
                        raise ValueError("styles must be an array")
                except (TypeError, ValueError) as exc:
                    raise VideoEditError("ข้อมูลฟอนต์ในซับไม่ถูกต้อง") from exc
                fonts.extend(s["font"]["path"] for s in styles if isinstance(s, dict)
                             and isinstance(s.get("font"), dict) and s["font"].get("path"))
        for font in fonts:
            file = _referenced(font, target)
            files["font:" + _path_id(file)] = _file(file)
        for page in sorted(MOTION.glob("*/index.html")):
            if 'id="clipkit-motion"' not in page.read_text(encoding="utf-8"):
                continue
            for file in sorted(page.parent.rglob("*")):
                if file.is_file() and file.suffix.lower() in {".html", ".js", ".mjs", ".css", ".json", ".ttf", ".otf", ".woff", ".woff2"}:
                    files["motion:" + file.relative_to(MOTION).as_posix()] = _file(file)
    else:
        media = []
    if raw is not None:
        media.append(str(raw))
        for suffix in (".transcript.json", ".transcript.meta.json", ".brief.txt", ".story-preferences.json"):
            files["source:" + suffix] = _file(Path(str(raw) + suffix), required=require_transcript and step == "stories" and suffix == ".transcript.json")
    for name in sorted(set(media)):
        source, cached = _media(_referenced(name, target if step == "clip" else target.parent), cache)
        sources[source["path_id"]] = source
        updated[source["path_id"]] = cached
    if step == "stories" and require_transcript:
        asr = _json(Path(str(raw) + ".transcript.meta.json"))
        if asr is not None and not isinstance(asr, dict):
            raise VideoEditError("ข้อมูลอ้างอิงการถอดเสียงต้องเป็น object")
        if isinstance(asr, dict) and asr.get("schema_version") == 2 and asr.get("sha256") != sources[_path_id(raw)]["sha256"]:
            raise VideoEditError("คำถอดเสียงไม่ตรงกับฟุตต้นทาง · ถอดเสียงฟุตล่าสุดก่อนเลือกเรื่อง")
    for name in ("web_ai/schema.md", f"web_ai/{'stories' if step == 'stories' else 'inserts' if operation == 'inserts' else 'clip'}.md", "story_split.md"):
        files["prompt:" + name] = _file(PROMPTS / name, required=True)
    for name in DEPENDENCIES:
        files["code:" + name] = _file(KIT / "app" / name, required=True)
    prefs = _preferences(target, step, preferences, raw)
    snapshot = {"version": VERSION, "step": step, "target_id": _path_id(target),
                "files": files, "sources": sources, "preferences": prefs}
    if operation is not None:
        snapshot["operation"] = operation
    return snapshot, updated


def export_check(target: str | Path, output: str | Path | None = None):
    """Capture export inputs without issuing a packet; call again before publishing the staged output."""
    before, sources = _snapshot("clip", target, None, {})
    import web_ai_transaction as transaction
    metadata = {name: transaction._hash(file) for name, file in transaction._files(Path(target)).items()}
    old_output = transaction._hash(Path(output)) if output is not None else None
    motion_inputs = {}
    motions = _json(_canonical(target) / "clipkit_overlays.json", [])
    if not isinstance(motions, list):
        raise VideoEditError("motion list must be an array")
    for item in motions:
        recipe = item.get("recipe") if isinstance(item, dict) else None
        if isinstance(recipe, dict) and isinstance(recipe.get("inputs"), dict):
            for value in recipe["inputs"].values():
                if not isinstance(value, str):
                    raise VideoEditError("motion input ต้องเป็นพาธไฟล์")
                file = _referenced(value, _canonical(target))
                motion_inputs[file] = _file(file, required=True)
    resolved_media, media_reads = {}, {}
    media_bound = False
    def bind_media(files: dict[str, str | Path]):
        nonlocal media_bound
        for published, file in files.items():
            if not Path(published).is_absolute():
                raise VideoEditError("Resolved media needs an absolute published path")
            source, destination = _canonical(file), str(_canonical(published))
            digest = _file(source, required=True)
            if (source in media_reads and media_reads[source] != digest
                    or destination in resolved_media and resolved_media[destination] != digest):
                raise VideoEditError("Resolved media changed while preparing export")
            media_reads[source], resolved_media[destination] = digest, digest
        media_bound = True
    def check():
        after, _ = _snapshot("clip", target, None, sources)
        if (_hash(after) != _hash(before) or
                {name: transaction._hash(file) for name, file in transaction._files(Path(target)).items()} != metadata or
                output is not None and transaction._hash(Path(output)) != old_output or
                any(_file(file, required=True) != digest for file, digest in motion_inputs.items()) or
                any(_file(file, required=True) != digest for file, digest in media_reads.items())):
            raise VideoEditError("ข้อมูลโปรเจกต์หรือไฟล์อ้างอิงเปลี่ยนระหว่างส่งออก · เก็บไฟล์เดิมไว้และส่งออกใหม่")
        evidence = {"snapshot": before, "metadata": metadata,
                    "motion_inputs": {_path_id(file): digest for file, digest in motion_inputs.items()}}
        if media_bound:
            evidence["resolved_media"] = dict(resolved_media)
        return evidence
    check.bind_media = bind_media  # Backend registers chosen files after first-use generation, before consumption.
    return check


def issue(step: str, target: str | Path, preferences: dict | None = None, *, operation: str | None = None) -> dict[str, Any]:
    folder = state_dir(step, target)
    cache = _json(folder / "source-cache.json", {})
    if not isinstance(cache, dict):
        raise VideoEditError("source-cache.json ต้องเป็น object")
    snapshot, updated = _snapshot(step, target, preferences, cache, operation=operation)
    identity = {"packet_id": uuid.uuid4().hex, "revision": _hash(snapshot),
                "source_id": _hash(snapshot["sources"]), "preferences": snapshot["preferences"]}
    if operation is not None:
        identity["operation"] = operation
    packets = folder / "packets"
    packets.mkdir(parents=True, exist_ok=True)
    record = {"step": step, "target_id": _path_id(target), "identity": identity, "sources": updated}
    atomic.write_text(packets / (identity["packet_id"] + ".json"), json.dumps(record, ensure_ascii=False))
    atomic.write_text(folder / "source-cache.json", json.dumps(updated))
    active = "active-manual.json" if operation == "manual" else "active.json"
    atomic.write_text(folder / active, json.dumps({"packet_id": identity["packet_id"]}))
    return identity


def verify(step: str, target: str | Path, answer: dict) -> dict[str, Any]:
    folder = state_dir(step, target)
    if answer.get("clipkit_web_ai") != VERSION:
        raise VideoEditError("คำตอบ AI รุ่นเก่าไม่มีข้อมูลอ้างอิงล่าสุด · ส่งออกคำสั่ง AI ใหม่")
    packet = answer.get("packet_id")
    if not isinstance(packet, str) or not re.fullmatch(r"[0-9a-f]{32}", packet):
        raise VideoEditError("คำตอบไม่มี packet_id ที่ถูกต้อง · ส่งออกคำสั่ง AI ใหม่")
    record = _json(folder / "packets" / (packet + ".json"))
    identity = record.get("identity", {}) if isinstance(record, dict) else None
    if not isinstance(identity, dict) or not isinstance(record.get("sources", {}), dict):
        raise VideoEditError("ข้อมูลอ้างอิงคำสั่ง AI ไม่ถูกต้อง · ส่งออกคำสั่ง AI ใหม่")
    active = _json(folder / ("active-manual.json" if identity.get("operation") == "manual" else "active.json"), {})
    if not isinstance(active, dict) or active.get("packet_id") != packet:
        raise VideoEditError("คำตอบนี้ไม่ตรงกับคำสั่ง AI ล่าสุดของงานนี้ · ส่งออกคำสั่ง AI ใหม่")
    if record.get("step") != step or record.get("target_id") != _path_id(target) or answer.get("step") != step:
        raise VideoEditError("คำตอบ AI เป็นของคนละงานหรือคนละขั้น · ส่งออกคำสั่ง AI ใหม่")
    if answer.get("revision") != identity.get("revision") or answer.get("source_id") != identity.get("source_id"):
        raise VideoEditError("ข้อมูลอ้างอิง revision/source_id ไม่ตรงกับคำสั่ง AI · ส่งออกคำสั่ง AI ใหม่")
    if answer.get("operation") != identity.get("operation"):
        raise VideoEditError("operation ไม่ตรงกับคำสั่ง AI ที่ส่งออก · ส่งออกคำสั่งใหม่")
    snapshot, _ = _snapshot(step, target, identity.get("preferences"), record.get("sources", {}), operation=identity.get("operation"))
    if _hash(snapshot) != identity.get("revision"):
        raise VideoEditError("ฟุต ซับ ความต้องการ หรือสไตล์เปลี่ยนหลังส่งคำสั่ง AI · ส่งออกคำสั่ง AI ใหม่")
    return identity
