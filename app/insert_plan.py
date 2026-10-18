"""One verified asset catalog and placement contract for manual, Browser and local AI inserts."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

import atomic
import capcut_edit
import cue_data
import project_lock
import subtitle_plan
import video_edit
from video_edit import VideoEditError

CATALOG = "clipkit_insert_assets.json"
CACHE = "clipkit_insert_assets"
EXTENSIONS = video_edit.IMAGE_EXT | {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


def _finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _stat(path):
    value = Path(path).stat()
    return [value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_ino]


def _digest(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise VideoEditError(f"ไม่พบไฟล์ภาพแทรกที่ตรวจแล้ว: {path.name}")
    before = _stat(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if _stat(path) != before:
        raise VideoEditError("ไฟล์ภาพแทรกกำลังเปลี่ยน · เลือกไฟล์ใหม่เมื่อบันทึกเสร็จ")
    return digest.hexdigest()


def file_digest(path):
    return _digest(path)


def _probe(path):
    """Preserve actual duration; the general sidebar probe rounds to hundredths."""
    if video_edit.is_image(str(path)):
        from PIL import Image, ImageOps
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                width, height = image.size
        except (OSError, ValueError) as exc:
            raise VideoEditError(f"อ่านภาพแทรกไม่ได้: {Path(path).name}") from exc
        return {"duration": None, "width": width, "height": height, "still": True}
    result = subprocess.run([video_edit.FFPROBE, "-v", "error", "-print_format", "json", "-show_format",
                             "-show_streams", str(path)], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", creationflags=video_edit.NO_WINDOW, timeout=60)
    if result.returncode:
        raise VideoEditError(f"อ่านภาพแทรกไม่ได้: {Path(path).name}")
    try:
        data = json.loads(result.stdout)
        stream = next(value for value in data["streams"] if value.get("codec_type") == "video")
        width, height = int(stream["width"]), int(stream["height"])
        rotation = next((abs(int(value["rotation"])) % 360 for value in stream.get("side_data_list", [])
                         if "rotation" in value), 0)
        if rotation in (90, 270):
            width, height = height, width
        try:
            duration = float(stream.get("duration"))
        except (ValueError, TypeError):
            try:
                duration = float(int(stream["duration_ts"]) * Fraction(stream["time_base"]))
            except (ValueError, TypeError, KeyError, ZeroDivisionError):
                # Containers can report the longer audio duration. Inspect only the chosen
                # video packets when its stream has no trustworthy end metadata.
                packets = subprocess.run([video_edit.FFPROBE, "-v", "error", "-select_streams", "v:0",
                    "-show_packets", "-show_entries", "packet=pts_time,duration_time", "-of", "json", str(path)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    creationflags=video_edit.NO_WINDOW, timeout=60)
                if packets.returncode:
                    raise ValueError("video packet end unavailable")
                entries = json.loads(packets.stdout).get("packets", [])
                spans = [(float(item["pts_time"]), float(item["duration_time"])) for item in entries]
                if not spans or any(not math.isfinite(a) or not math.isfinite(b) or b <= 0 for a, b in spans):
                    raise ValueError("video packet clock unavailable")
                duration = max(a + b for a, b in spans) - max(0, min(a for a, _ in spans))
        if not math.isfinite(duration) or duration <= 0 or min(width, height) <= 0:
            raise ValueError("invalid dimensions/duration")
    except (ValueError, KeyError, StopIteration, TypeError) as exc:
        raise VideoEditError(f"ภาพแทรกไม่มีภาพหรือความยาวที่ใช้ได้: {Path(path).name}") from exc
    return {"duration": duration, "width": width, "height": height, "still": False}


def _source_ref(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict):
        raise VideoEditError("ที่มาภาพแทรกต้องเป็นข้อมูลแหล่งที่มา")
    result = {key: value.get(key, "") for key in ("provider", "id", "page", "license_url")}
    result["license_status"] = value.get("license_status", "unverified")
    if any(not isinstance(item, str) or len(item) > 2000 for item in result.values()):
        raise VideoEditError("ข้อมูลแหล่งภาพแทรกไม่ถูกต้อง")
    if result["license_status"] not in ("unverified", "user-confirmed"):
        raise VideoEditError("สิทธิ์ภาพแทรกต้องเป็น unverified หรือ user-confirmed โดยผู้ใช้")
    if any(result[key] and not result[key].startswith("https://") for key in ("page", "license_url")):
        raise VideoEditError("ลิงก์แหล่งภาพแทรกต้องเป็น https")
    return result


def catalog(path):
    """Read and verify registered content without writing or trusting a filename/size alone."""
    folder = Path(path)
    file = folder / CATALOG
    if file.is_symlink() or (folder / CACHE).is_symlink():
        raise VideoEditError("ทะเบียนและ cache ภาพแทรกต้องอยู่ในโปรเจกต์")
    if not file.is_file():
        return {"version": 1, "assets": {}, "candidates": []}
    try:
        result = json.loads(file.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise VideoEditError("อ่านทะเบียนภาพแทรกไม่ได้ · เลือกไฟล์ใหม่") from exc
    if not isinstance(result, dict) or result.get("version") != 1 or not isinstance(result.get("assets"), dict):
        raise VideoEditError("ทะเบียนภาพแทรกไม่ถูกต้อง")
    candidates = result.get("candidates", [])
    if (not isinstance(candidates, list) or any(not isinstance(value, str) for value in candidates)
            or len(set(candidates)) != len(candidates) or any(value not in result["assets"] for value in candidates)):
        raise VideoEditError("รายการภาพแทรกที่เลือกไม่ถูกต้อง")
    root = (folder / CACHE).resolve()
    for asset_id, item in result["assets"].items():
        if not isinstance(item, dict) or not re.fullmatch(r"[0-9a-f]{64}", asset_id) or item.get("asset_id") != asset_id:
            raise VideoEditError("asset ID ไม่ถูกต้อง")
        if not isinstance(item.get("file"), str) or not isinstance(item.get("basename"), str) or Path(item["basename"]).name != item["basename"]:
            raise VideoEditError("พาธหรือชื่อภาพแทรกไม่ถูกต้อง")
        source = Path(item["file"])
        if (source.parent.resolve() != root or source.stem != asset_id or source.suffix not in EXTENSIONS
                or source.is_symlink() or item.get("sha256") != asset_id or _digest(source) != asset_id):
            raise VideoEditError("ภาพแทรกเปลี่ยนหรือออกนอก cache · เลือกไฟล์แล้วส่งออกคำสั่งใหม่")
        if (type(item.get("still")) is not bool or any(type(item.get(key)) is not int or item[key] <= 0 for key in ("width", "height"))
                or item["still"] and item.get("duration") is not None
                or (not item["still"] and (not _finite(item.get("duration")) or item["duration"] <= 0))):
            raise VideoEditError("ข้อมูลภาพ/ความยาวภาพแทรกไม่ถูกต้อง")
        _source_ref(item.get("source_ref"))
    return result


@project_lock.edit
def register_assets(path, files, provenance=None):
    """Copy selected files atomically; publish catalog only after every source verifies and probes."""
    folder = Path(path)
    if not (folder / "draft_content.json").is_file():
        raise VideoEditError("เลือกโปรเจกต์ก่อนเพิ่มภาพแทรก")
    if isinstance(files, (str, Path)):
        source = Path(files)
        files = sorted((file for file in source.iterdir() if file.is_file() and file.suffix.lower() in EXTENSIONS),
                       key=lambda file: file.name.casefold()) if source.is_dir() else [source]
    else:
        files = list(files)
    if not files:
        raise VideoEditError("ไม่มีไฟล์ภาพหรือวิดีโอที่เลือก")
    previous = catalog(path)
    result = {"version": 1, "assets": dict(previous["assets"]), "candidates": []}
    cache = folder / CACHE
    if cache.is_symlink():
        raise VideoEditError("cache ภาพแทรกต้องอยู่ในโปรเจกต์")
    cache.mkdir(exist_ok=True)
    created = []
    try:
        for source in map(Path, files):
            if not source.is_file() or source.suffix.lower() not in EXTENSIONS:
                raise VideoEditError(f"ไม่พบไฟล์ภาพแทรกที่รองรับ: {source.name}")
            before = _stat(source)
            descriptor, name = tempfile.mkstemp(prefix="register-", suffix=source.suffix.lower(), dir=cache)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as output, source.open("rb") as original:
                    shutil.copyfileobj(original, output)
                    output.flush()
                    os.fsync(output.fileno())
                asset_id = _digest(temporary)
                if _stat(source) != before or _digest(source) != asset_id:
                    raise VideoEditError("ต้นฉบับภาพแทรกเปลี่ยนระหว่างคัดลอก · เลือกใหม่")
                if asset_id not in result["assets"]:
                    info = _probe(temporary)
                    destination = cache / (asset_id + source.suffix.lower())
                    if destination.exists():
                        if _digest(destination) != asset_id:
                            raise VideoEditError("cache ภาพแทรกเดิมเสียหาย · เก็บไฟล์ไว้แล้วเลือกใหม่")
                    else:
                        os.replace(temporary, destination)
                        created.append(destination)
                    is_mapping = isinstance(provenance, dict) and any(isinstance(value, dict) for value in provenance.values())
                    reference = provenance.get(str(source), {}) if is_mapping else provenance
                    result["assets"][asset_id] = {"asset_id": asset_id, "file": str(destination.resolve()), "basename": source.name,
                                                   "sha256": asset_id, "stat": _stat(destination), **info,
                                                   "source_ref": _source_ref(reference)}
                if asset_id not in result["candidates"]:
                    result["candidates"].append(asset_id)
            finally:
                temporary.unlink(missing_ok=True)
        atomic.write_text(folder / CATALOG, json.dumps(result, ensure_ascii=False, indent=1, allow_nan=False))
    except BaseException:
        for file in created:
            file.unlink(missing_ok=True)
        raise
    return result


def public_assets(assets):
    return [{key: assets["assets"][asset_id][key] for key in
             ("asset_id", "basename", "duration", "width", "height", "still", "source_ref")}
            for asset_id in assets["candidates"]]


def _context(path):
    draft = capcut_edit._load(Path(path))
    rows = capcut_edit.read_draft(str(path))["subtitles"]
    duration = max((segment["target_timerange"]["start"] + segment["target_timerange"]["duration"]) / video_edit.US
                   for segment in capcut_edit._video_track(draft)["segments"])
    return draft, rows, duration


def opportunities(path):
    """Repeated queries remain separate opportunities with actual output-clock anchors."""
    import render
    _, rows, duration = _context(path)
    words = cue_data.load(path, "clipkit_words.json", rows)
    picks = cue_data.load(path, "clipkit_punch.json", rows)
    phrases = render._resolved_pair_cues(rows, words, picks, duration=duration)
    options = {}
    for row in rows:
        spoken = words.get(cue_data.key(row["id"]), [])
        language = "th" if re.search(r"[ก-๛]", "".join(word[2] for word in spoken)) else "en"
        starts = video_edit._word_starts(spoken, language)
        if language == "en":
            starts = [whole or n > 0 and spoken[n - 1][2][-1:].isspace() for n, whole in enumerate(starts)]
            if subtitle_plan.tight("".join(word[2] for word in spoken)) == subtitle_plan.tight(row["text"]):
                edges = {len(subtitle_plan.tight(row["text"][:match.start()])) for match in re.finditer(r"\S+", row["text"])}
                offset = 0
                for n, word in enumerate(spoken):
                    starts[n] = starts[n] or offset in edges
                    offset += len(subtitle_plan.tight(word[2]))
        indices = [n for n, whole in enumerate(starts) if whole and subtitle_plan.tight(spoken[n][2])]
        options[row["id"]] = [{"word": n + 1, "end_word": end, "heard": "".join(word[2] for word in spoken[n:end]),
                                "start": row["start"] + spoken[n][0], "end": row["start"] + spoken[end - 1][1]}
                               for n, end in zip(indices, indices[1:] + [len(spoken)])]
    return [{"opportunity_id": hashlib.sha256(json.dumps([p["cue_id"], p["k"]]).encode()).hexdigest()[:24],
             "cue_id": p["cue_id"], "phrase": p["k"], "text": p["line"], "lead": p["lead"], "punch": p["punch"],
             "query": p["query"], "start": p["start"], "hit": p["hit"], "end": p["end"],
             "anchor": {"cue_id": p["cue_id"], "kind": "keyword" if p["punch"] else "phrase", "phrase": p["k"]},
             "words": options[p["cue_id"]]} for p in phrases]


def resolve_anchor(path, anchor, *, points=None):
    """Resolve existing cue/whole-word indices once for inserts and motion; never estimate clocks."""
    if not isinstance(anchor, dict):
        raise VideoEditError("ต้องระบุ anchor ของคำพูดหรือเวลาที่เลือกเอง")
    kind = anchor.get("kind")
    allowed = ({"kind", "start"} if kind == "timeline" else
               {"kind", "cue_id", "word"} if kind == "word" else {"kind", "cue_id", "phrase"})
    if set(anchor) - allowed:
        raise VideoEditError("anchor มีช่องที่ไม่รองรับ")
    selected = None
    if kind == "timeline":
        start = anchor.get("start")
    else:
        points = opportunities(path) if points is None else points
        matches = [point for point in points if point["cue_id"] == anchor.get("cue_id")]
        if kind in ("phrase", "keyword"):
            selected = next((point for point in matches if point["phrase"] == anchor.get("phrase")), None)
            if type(anchor.get("phrase")) is not int or selected is None or kind == "keyword" and not selected["punch"]:
                raise VideoEditError("ไม่พบ phrase/keyword ที่อ้างอิง · โหลดซับล่าสุดแล้วเลือกใหม่")
            start = selected["hit" if kind == "keyword" else "start"]
        elif kind == "word":
            word = next((word for point in matches for word in point["words"] if word["word"] == anchor.get("word")), None)
            if type(anchor.get("word")) is not int or word is None:
                raise VideoEditError("anchor ต้องตรงต้นคำเต็มจาก words · ห้ามประมาณเวลาระหว่างคำ")
            start = word["start"]
        else:
            raise VideoEditError("ชนิด anchor ไม่ถูกต้อง")
    if not _finite(start) or start < 0:
        raise VideoEditError("เวลา anchor ต้องเป็นค่าจริงตั้งแต่ศูนย์")
    return start, selected


def normalize(path, items, *, assets=None, allow_legacy=False):
    """Resolve validated anchors and one measured geometry for every output; never alter files."""
    from insert_geometry import geometry, consumption
    draft, rows, duration = _context(path)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise VideoEditError("ภาพแทรกต้องเป็นรายการ")
    assets = catalog(path) if assets is None else assets
    style_file = Path(path) / "clipkit_style.json"
    style = json.loads(style_file.read_text(encoding="utf-8")) if style_file.is_file() else {}
    points = None
    result = []
    for item in items:
        asset_id = item.get("asset_id")
        legacy = asset_id is None and allow_legacy
        if legacy:
            file = Path(item.get("file", ""))
            asset_id = _digest(file)
            asset = {"file": str(file.resolve()), "sha256": asset_id, **_probe(file), "source_ref": _source_ref()}
        else:
            asset = assets["assets"].get(asset_id) if isinstance(asset_id, str) else None
            if not asset or _digest(asset["file"]) != asset["sha256"]:
                raise VideoEditError("asset ID ไม่ตรงกับภาพแทรกที่เลือก · เลือกไฟล์แล้วส่งคำสั่งใหม่")
        anchor = item.get("anchor")
        if anchor is None and legacy:
            anchor = {"kind": "timeline", "start": item.get("start")}
        if isinstance(anchor, dict) and anchor.get("kind") != "timeline":
            points = opportunities(path) if points is None else points
        start, selected = resolve_anchor(path, anchor, points=points)
        if (not _finite(start) or start < 0 or not _finite(item.get("dur")) or item["dur"] <= 0
                or start + item["dur"] > duration + 1e-6):
            raise VideoEditError("ภาพแทรกต้องอยู่ในความยาวคลิปจริง")
        if "start" in item and (not _finite(item["start"]) or abs(item["start"] - start) > 1e-6):
            raise VideoEditError("เวลาภาพแทรกไม่ตรง anchor ล่าสุด · เลือกจุดคำพูดใหม่")
        reason = item.get("reason", "" if legacy else None)
        if not isinstance(reason, str) or len(reason) > 500 or not legacy and not reason.strip():
            raise VideoEditError("ระบุเหตุผลที่ภาพนี้ช่วยเนื้อหา (ไม่เกิน 500 ตัวอักษร)")
        source_start = item.get("source_start", 0)
        ramp = bool(style.get("ramp")) and not asset["still"]
        consumed = consumption(item["dur"], ramp=ramp, still=asset["still"])
        if (not _finite(source_start) or source_start < 0 or asset["still"] and source_start != 0
                or not asset["still"] and source_start + consumed > asset["duration"] + 1e-6):
            raise VideoEditError("ช่วงภาพแทรกเกินไฟล์ต้นฉบับ · เลือกช่วงสั้นลงหรือเปลี่ยนไฟล์")
        focus_x, focus_y = item.get("focus_x", .5), item.get("focus_y", .5)
        measured = geometry(asset, W=draft["canvas_config"]["width"], H=draft["canvas_config"]["height"],
                            focus_x=focus_x, focus_y=focus_y)
        if item.get("opportunity_id") and (selected is None or item["opportunity_id"] != selected["opportunity_id"]):
            raise VideoEditError("opportunity ID ไม่ตรงจุดคำพูด")
        row = {"asset_id": asset_id, "anchor": dict(anchor), "cue_id": anchor.get("cue_id"), "reason": reason.strip(),
               "source_start": source_start, "dur": item["dur"], "focus_x": focus_x, "focus_y": focus_y,
               "start": start, "file": asset["file"], "source_sha256": asset["sha256"], "source_ref": asset["source_ref"],
               "source_duration": asset["duration"], "source_width": asset["width"], "source_height": asset["height"],
               "kind": "image" if asset["still"] else "video", "media_start": source_start, "source_consumed": consumed,
               "ramp": ramp, "review_required": legacy or item.get("review_required") is True,
               "query": selected["query"] if selected else item.get("query", ""), "thumb": "", **measured}
        placement_id = item.get("placement_id") or hashlib.sha256(json.dumps(
            [asset_id, anchor, source_start, item["dur"]], sort_keys=True, allow_nan=False).encode()).hexdigest()[:24]
        if not isinstance(placement_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", placement_id):
            raise VideoEditError("placement ID ไม่ถูกต้อง")
        row["placement_id"] = placement_id
        if selected:
            row["opportunity_id"] = selected["opportunity_id"]
        result.append(row)
    result.sort(key=lambda item: item["start"])
    if len({item["placement_id"] for item in result}) != len(result):
        raise VideoEditError("placement ID ซ้ำ")
    if any(first["start"] + first["dur"] > second["start"] + 1e-6 for first, second in zip(result, result[1:])):
        raise VideoEditError("ช่วงภาพแทรกทับกัน · เลือกจุดหรือระยะใหม่")
    return result


def contact_sheet(assets, out):
    """Existing numbered contact-sheet matcher, now using verified registered copies."""
    import cv2
    from PIL import Image, ImageDraw, ImageOps
    selected = public_assets(assets)
    if not selected:
        raise VideoEditError("ไม่มีภาพแทรกสำหรับ contact sheet")
    sheet = Image.new("RGB", (1280, 320 * math.ceil(len(selected) / 4)))
    for number, public in enumerate(selected, 1):
        asset = assets["assets"][public["asset_id"]]
        if asset["still"]:
            with Image.open(asset["file"]) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
        else:
            capture = cv2.VideoCapture(asset["file"])
            try:
                capture.set(cv2.CAP_PROP_POS_MSEC, min(1, asset["duration"] / 2) * 1000)
                ok, frame = capture.read()
            finally:
                capture.release()
            if not ok:
                raise VideoEditError(f"อ่านเฟรมตัวอย่างไม่ได้: {public['basename']}")
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        tile = ImageOps.pad(image, (320, 320))
        draw = ImageDraw.Draw(tile)
        draw.rectangle((0, 0, 68, 36), fill="black")
        draw.text((12, 8), str(number), fill="white")
        sheet.paste(tile, (((number - 1) % 4) * 320, ((number - 1) // 4) * 320))
    out = Path(out)
    import io
    payload = io.BytesIO()
    sheet.save(payload, format="JPEG", quality=90)
    atomic.write_bytes(out, payload.getvalue())
    return out
