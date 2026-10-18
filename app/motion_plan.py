"""Motion ownership and measured layouts shared by rendering and project preflight."""
import hashlib
import json
import math
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from video_edit import VideoEditError


ROLES = {"normal", "emphasis", "caption", "second", "title"}
_cut_preview = ContextVar("motion_cut_preview", default=None)


@contextmanager
def preview_cut(folder):
    review = {"folder": Path(folder).resolve(), "plans": []}
    token = _cut_preview.set(review)
    try:
        yield review["plans"]
    finally:
        _cut_preview.reset(token)


@contextmanager
def regenerate_cut(folder, draft, sidecars, pending, cuts, original_rows):
    """Prepare every affected recipe, render privately, then retain assets only if metadata commits."""
    if not pending:
        yield sidecars
        return
    import shutil
    import tempfile
    import uuid
    import atomic
    import cue_data
    import host_platform
    import insert_plan
    import kit_settings
    import video_editor
    import web_ai_identity as identity
    import web_ai_transaction as transaction

    review = _cut_preview.get()
    planning = review is not None and review["folder"] == folder.resolve()
    if not planning and host_platform.capcut_running():
        raise VideoEditError("ปิด CapCut ก่อนสร้าง motion ใหม่หลังตัด")
    original = transaction._files(folder)
    before = {name: transaction._hash(file) for name, file in original.items()}
    binding, source_cache = identity._snapshot("clip", folder, None, {})
    words = cue_data.load(folder, "clipkit_words.json", original_rows)
    items = sidecars["clipkit_overlays.json"]
    ids = {old.get("placement_id") for old, _ in pending}
    if (None in ids or any(not isinstance(value, str) or not value for value in ids)
            or len(ids) != len(pending) or any(sum(item.get("placement_id") == value for item in items) != 1 for value in ids)):
        raise VideoEditError("motion ไม่มี placement ID ที่ชัดเจน · ตรวจ recipe ก่อนตัด")
    for old, _ in pending:
        try:
            recipe = old["recipe"]
            if (old.get("template") != recipe.get("template") or old.get("params") != recipe.get("params")
                    or not isinstance(old.get("anchor"), dict) or not isinstance(old.get("text"), str)
                    or not isinstance(old.get("reason"), str)):
                raise VideoEditError("motion recipe ไม่ตรงกับรายการเดิม · ตรวจใหม่ก่อนตัด")
            source = Path(old["file"])
            source = source if source.is_absolute() else folder / source
            if recipe["version"] != 1 or insert_plan.file_digest(source) != recipe["media"]["sha256"]:
                raise VideoEditError("motion เดิมเปลี่ยนหรือไม่มี recipe ที่ตรวจแล้ว · ตรวจใหม่ก่อนตัด")
            verify_inputs(recipe)
        except (KeyError, TypeError, OSError) as exc:
            raise VideoEditError("motion recipe หรือไฟล์เดิมไม่ครบ · ตรวจใหม่ก่อนตัด") from exc
        # A timed word must survive intact; deleting spoken content needs a new source/subtitle decision.
        cue_ids = {target["cue_id"] for target in ownership(old)["targets"]}
        cue_ids.add(old.get("anchor", {}).get("cue_id"))
        if any(cut["start"] < row["start"] + b and row["start"] + a < cut["end"]
               for row in original_rows if row["id"] in cue_ids
               for a,b,_ in words.get(cue_data.key(row["id"]), []) for cut in cuts):
            raise VideoEditError("ช่วงตัดโดนคำพูดที่ motion อ้าง · แก้เนื้อหาและเวลาคำก่อนสร้างใหม่")

    assets = (kit_settings._motion_destination.get() or folder / "clipkit_motion") / uuid.uuid4().hex
    committed = False
    try:
        with tempfile.TemporaryDirectory(prefix="clipkit_motion_cut_", ignore_cleanup_errors=True) as temporary:
            work = Path(temporary) / folder.name
            work.mkdir()
            for name, source in original.items():
                transaction._copy(source, work / name)
                if transaction._hash(work / name) != before[name]:
                    raise VideoEditError("โปรเจกต์เปลี่ยนระหว่างเตรียม motion · ตรวจใหม่")
            atomic.write_text(work / "draft_content.json", json.dumps(draft, ensure_ascii=False))
            for name, data in sidecars.items():
                atomic.write_text(work / name, json.dumps(data, ensure_ascii=False))
            unchanged = [item for item in items if item.get("placement_id") not in ids]
            atomic.write_text(work / "clipkit_overlays.json", json.dumps(unchanged, ensure_ascii=False))
            plans, candidates = [], list(unchanged)
            for old, mapped in pending:
                params = None if old["template"] == "sentence-pair" else {**old["recipe"]["params"], "duration": mapped["duration"]}
                request = video_editor.OverlayRequest(path=str(work), template=old["template"],
                    start=mapped["start"], end=mapped["start"] + mapped["duration"], text=old["text"], params=params,
                    cue_id=old.get("cue_id"), anchor=mapped["anchor"], ownership=old["ownership"],
                    placement=old["recipe"]["placement"], reason=old["reason"])
                plan = video_editor.prepare_overlay(request, previous=candidates)
                plans.append((old, request, plan)); candidates.append(plan["candidate"])
            if planning:
                review["plans"].extend(video_editor.overlay_plan(request, plan) for _, request, plan in plans)
                replacements = {old["placement_id"]: {**mapped, "params": plan["prepared"]["source_params"]}
                                for (old, mapped), (_, request, plan) in zip(pending, plans)}
            else:
                with kit_settings.motion_destination(assets):
                    replacements = {old["placement_id"]: {**video_editor.render_overlay(request, plan),
                                    "placement_id": old["placement_id"]} for old, request, plan in plans}
            if (not planning and host_platform.capcut_running()) or {name: transaction._hash(file) for name, file in transaction._files(folder).items()} != before:
                raise VideoEditError("โปรเจกต์เปลี่ยนหรือ CapCut เปิดระหว่างสร้าง motion · ตรวจใหม่")
            current, _ = identity._snapshot("clip", folder, None, source_cache)
            if identity._hash(current) != identity._hash(binding):
                raise VideoEditError("ฟุตหรือข้อมูลอ้างอิงเปลี่ยนระหว่างสร้าง motion · ตรวจใหม่")
            for old, _ in pending:
                verify_inputs(old["recipe"])
                source = Path(old["file"])
                source = source if source.is_absolute() else folder / source
                if insert_plan.file_digest(source) != old["recipe"]["media"]["sha256"]:
                    raise VideoEditError("ไฟล์ motion เดิมเปลี่ยนระหว่างสร้าง · ตรวจใหม่")
            sidecars["clipkit_overlays.json"] = [replacements.get(item.get("placement_id"), item) for item in items]
            yield sidecars
            committed = True
    except BaseException:
        try:
            restored = {name: transaction._hash(file) for name, file in transaction._files(folder).items()} == before
        except (OSError, VideoEditError):
            restored = False  # Keep recovery assets when the metadata rollback cannot be proven.
        if not committed and restored and assets.is_dir():
            shutil.rmtree(assets)
        raise


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def template_layout(template, params, *, width=1080, height=1920, placement=None):
    """Measure existing template poses with an exact font; never relocate around a conflict."""
    from PIL import ImageFont
    import host_platform
    import insert_plan
    import kit_settings
    import render
    import video_editor

    if any(type(value) is not int or not 1 <= value <= 8192 for value in (width, height)):
        raise VideoEditError("motion canvas must contain integer width/height from 1 to 8192")
    if template not in {"hook-title", "stat-number"}:
        raise VideoEditError("this template needs its own verified layout")
    if not isinstance(params, dict):
        raise VideoEditError("motion params must be an object")
    spec = kit_settings.motion_templates().get(template)
    default_length = next((f.get("default") for f in (spec or {}).get("fields", [])
                           if f["key"] == (spec or {}).get("length", "duration")), None)
    source, duration = video_editor.motion_params(template, params, default_length)
    font_name = source.get("font")
    if font_name:
        font = Path(font_name)
        if not font.is_file():
            font = host_platform.find_font(font_name, kit_settings._read_config())
        if font is None or not font.is_file():
            raise VideoEditError("motion font is unavailable; choose an installed font file")
    else:
        font = render.DEFAULT_FONT
    family, font = render._font(str(font))
    from fontTools.ttLib import TTFont
    import unicodedata
    with TTFont(str(font), lazy=True) as face:
        cmap = face.getBestCmap()
        for key in ("key", "sub") if template == "hook-title" else ("number", "unit", "label"):
            source[key] = render.unbreak(source.get(key, ""))
            if any(not char.isspace() and unicodedata.category(char) != "Cf" and ord(char) not in cmap
                   for char in source[key]):
                raise VideoEditError("motion font lacks a requested character; choose a font that contains the text")
        digit_names = {cmap[ord(c)] for c in "0123456789" if ord(c) in cmap}
        if template == "stat-number":
            if len(digit_names) != 10:
                raise VideoEditError("motion font lacks numeric glyphs")
            if "GSUB" in face:
                table = face["GSUB"].table
                indices = {i for record in table.FeatureList.FeatureRecord if record.FeatureTag == "tnum"
                           for i in record.Feature.LookupListIndex}
                for i in indices:
                    for sub in table.LookupList.Lookup[i].SubTable:
                        sub = getattr(sub, "ExtSubTable", sub)
                        # shortcut: numeric single substitutions; extend before accepting other tnum lookup types.
                        if not hasattr(sub, "mapping") or any(not isinstance(v, str) for v in sub.mapping.values()):
                            raise VideoEditError("motion font has unsupported numeric substitutions; choose a verified font")
                        digit_names |= {sub.mapping[name] for name in list(digit_names) if name in sub.mapping}
            digit_advance = max(face["hmtx"].metrics[name][0] for name in digit_names) / face["head"].unitsPerEm
    measured = ImageFont.truetype(str(font), 100)
    # LINE_H fixes CSS line cells; the font cell additionally reserves tall Thai ink.
    cell = max(render.LINE_H, render.font_cell_metrics(font)["font_cell_scale"])
    def text_width(text, size, **kwargs):
        return measured.getlength(text, **kwargs) / 100 * size
    def fit(text, size, room):
        return min(size, size * room / max(text_width(text, size) * 1.02, 1))
    sizes, rectangles = {}, []
    if template == "hook-title":
        key, sub = source.get("key", ""), source.get("sub", "")
        if not key.strip() and not sub.strip():
            raise VideoEditError("hook-title needs actual title text")
        tilt = source.get("style") == "tilt"
        angle = math.radians(4.3 if tilt else 0)
        for role, text, size, top in (("key", key, 250 if tilt else 150, 1150 if tilt else 1265),
                                      ("sub", sub, 122 if tilt else 86, 1405 if tilt else 1150)):
            sizes[role] = size = fit(text, size, 940)
            if not text:
                continue
            w, h = text_width(text, size) * 1.02 + (11 if role == "sub" else 0), size * cell
            peak = 1.08 if tilt and role == "key" else 1.9 if role == "key" else 1
            bw = w * peak * math.cos(angle) + h * math.sin(angle)
            bh = h * math.cos(angle) + w * peak * math.sin(angle)
            # Three blur radii plus stroke/offset cover neon and smear entrance poses.
            pad = 140 if role == "key" else 32
            shift = -160 if role == "key" and not tilt else 0
            cy = top + size * render.LINE_H / 2
            rectangles.append([540 - bw / 2 + shift - pad, cy - bh / 2 - pad,
                               540 + bw / 2 + pad, cy + bh / 2 + pad])
    else:
        from decimal import Decimal
        number = source["number"].replace(",", "")
        if any(char not in "0123456789." for char in number):
            raise VideoEditError("stat-number requires ASCII digits for its numeric animation")
        amount = Decimal(number)
        if not amount.is_finite() or amount > 2 ** 53 - 1:
            raise VideoEditError("stat-number exceeds the exact numeric animation range")
        decimals = len(number.partition(".")[2])
        if decimals > 20:
            raise VideoEditError("stat-number supports at most 20 decimal places")
        final = format(amount, f",.{decimals}f")
        unit, label = source.get("unit", ""), source.get("label", "")
        numeric_width = sum(digit_advance * 260 if char.isdigit() else text_width(char, 260) for char in final)
        wide = numeric_width + 14 + text_width(unit, 130)
        k = min(1, 800 / max(wide * 1.02, 1))
        sizes.update(number=260 * k, unit=130 * k, label=fit(label, 74, 800))
        card_height = 48 + sizes["number"] * max(1.05, cell) + 14 + 26 + 30 + sizes["label"] * max(1.25, cell) + 56
        rectangles = [[90 - 245, 1080 - card_height * .05 - 240,
                       990 + 245, 1080 + card_height * 1.05 + 320]]
    rect = [min(r[0] for r in rectangles), min(r[1] for r in rectangles),
            max(r[2] for r in rectangles), max(r[3] for r in rectangles)]
    if placement is None:
        placement = {"x": .11, "y": .15 if template == "hook-title" else .2,
                     "w": .78, "h": .3 if template == "hook-title" else .4}
    if (not isinstance(placement, dict) or set(placement) != {"x", "y", "w", "h"}
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   for v in placement.values())
            or min(placement["x"], placement["y"]) < 0 or min(placement["w"], placement["h"]) <= 0
            or placement["x"] + placement["w"] > 1 or placement["y"] + placement["h"] > 1):
        raise VideoEditError("motion placement needs finite normalized x/y/w/h inside the canvas")
    x, y, w, h = (placement[key] * dimension for key, dimension in
                  (("x", width), ("y", height), ("w", width), ("h", height)))
    scale = min(w / (rect[2] - rect[0]), h / (rect[3] - rect[1]), min(width, height) / 1080)
    visible = [sizes[k] for k in sizes if (source.get(k) if k != "number" else True)]
    if any(size * scale < min(width, height) * .018 for size in visible):
        raise VideoEditError("motion text is too small in this placement; shorten text or choose a larger area")
    left, top = x + w / 2 - (rect[0] + rect[2]) * scale / 2, y + h / 2 - (rect[1] + rect[3]) * scale / 2
    bounds = [rect[0] * scale + left, rect[1] * scale + top, rect[2] * scale + left, rect[3] * scale + top]
    layout = {"width": width, "height": height, "left": left, "top": top, "scale": scale,
              "source_bounds": rect, "bounds": bounds, "font_sizes": sizes, "font_face": "ClipKitMotion"}
    if template == "stat-number":
        layout["number_text"] = final
    inputs = {"template": str(kit_settings.KIT / "motion" / template / "index.html"),
              "font": str(font.resolve()), "runtime": str(Path(__file__).with_name("motion_runtime.js"))}
    hashes = {key: insert_plan.file_digest(value) for key, value in inputs.items()}
    result = {"template": template, "source_params": source, "duration": duration,
              "canvas": {"width": width, "height": height}, "placement": dict(placement),
              "layout": layout, "bounds": bounds, "inputs": inputs, "hashes": hashes, "font_family": family}
    result["layout_sha256"] = _hash(result)
    return result


def prepare_template(template, params, *, width=None, height=None, placement=None):
    import insert_plan
    import kit_settings
    if template != "sentence-pair":
        return template_layout(template, params, width=1080 if width is None else width,
                               height=1920 if height is None else height, placement=placement)
    if placement is not None:
        raise VideoEditError("sentence-pair positions come from the selected subtitle preset")
    source = kit_settings.sentence_pair_params(params)
    W, H = int(source["width"]), int(source["height"])
    if width is not None and width != W or height is not None and height != H:
        raise VideoEditError("sentence-pair canvas differs from its measured subtitle events")
    events = source["subtitle_events"]
    bounds = [min(e["bbox"][0] for e in events), min(e["bbox"][1] for e in events),
              max(e["bbox"][2] for e in events), max(e["bbox"][3] for e in events)]
    inputs = {"template": str(kit_settings.KIT / "motion" / template / "index.html")}
    for n, file in enumerate(dict.fromkeys(e["font_file"] for e in events)):
        inputs[f"font:{n}"] = str(Path(file).resolve())
    hashes = {key: insert_plan.file_digest(value) for key, value in inputs.items()}
    plan = {"template": template, "source_params": source, "duration": max(e["end"] for e in events),
            "canvas": {"width": W, "height": H}, "placement": None, "layout": None, "bounds": bounds,
            "inputs": inputs, "hashes": hashes}
    plan["layout_sha256"] = _hash(plan)
    return plan


def verify_inputs(plan):
    import insert_plan
    inputs, hashes = plan.get("inputs"), plan.get("hashes")
    if (not isinstance(inputs, dict) or not inputs or not isinstance(hashes, dict) or set(inputs) != set(hashes)
            or any(not isinstance(key, str) or not isinstance(path, str) or not path for key, path in inputs.items())):
        raise VideoEditError("motion template/font fingerprints ไม่ครบ · สร้าง recipe ใหม่")
    if any(insert_plan.file_digest(path) != hashes[key] for key, path in inputs.items()):
        raise VideoEditError("motion template or font changed during rendering; prepare the layout again")


def verify_timeline(folder, timeline, *, strict=True):
    """Replan stored recipes against current cues; preview keeps stale items available for repair."""
    import insert_plan
    import video_editor
    from fastapi import HTTPException
    errors = []
    for item in timeline.get("motions", []):
        if "recipe" not in item:
            continue  # Legacy media stays explicitly unverified; no recipe is invented.
        try:
            recipe = item["recipe"]
            if not isinstance(recipe, dict) or recipe.get("version") != 1:
                raise VideoEditError("motion recipe ไม่ครบหรือเป็นรุ่นที่ไม่รองรับ")
            verify_inputs(recipe)
            media = recipe["media"]
            if (not isinstance(media, dict) or media.get("alpha_decode") is not True or
                    insert_plan.file_digest(item["file"]) != media["sha256"]):
                raise VideoEditError("ไฟล์ motion เปลี่ยนหรือยังไม่ผ่านการตรวจ alpha")
            request = video_editor.OverlayRequest(path=str(folder), template=item["template"],
                start=item["start"], end=item["start"] + item["duration"], text=item["text"],
                cue_id=item.get("cue_id"), placement_id=item["placement_id"],
                params=None if item["template"] == "sentence-pair" else item["params"],
                anchor=item["anchor"], ownership=item["ownership"], placement=recipe["placement"], reason=item["reason"])
            plan = video_editor.prepare_overlay(request, previous=timeline["motions"], timeline=timeline)
            current = plan["prepared"]
            item["subject_review_pending"] = subject_review_pending(timeline["style"].get("motion_subject_zones", []), item["start"], item["start"] + item["duration"])
            if (any(recipe[key] != current[key] for key in
                    ("template", "canvas", "placement", "bounds", "duration", "inputs", "hashes", "layout_sha256"))
                    or recipe["params"] != current["source_params"] or item["params"] != recipe["params"]
                    or item["canvas"] != current["canvas"] or item["bounds"] != current["bounds"]
                    or item["duration"] != current["duration"] or item["ownership"] != plan["ownership"]
                    or (media["width"], media["height"]) != (current["canvas"]["width"], current["canvas"]["height"])
                    or any(isinstance(media[key], bool) or not isinstance(media[key], (int, float)) or
                           not math.isfinite(media[key]) for key in ("duration", "fps"))
                    or media["fps"] <= 0 or media["duration"] <= 0
                    or abs(media["duration"] - current["duration"]) > 1 / media["fps"] + 1e-6):
                raise VideoEditError("motion ไม่ตรงข้อความ เวลา หรือขนาดภาพล่าสุด")
        except (VideoEditError, HTTPException, KeyError, TypeError, ValueError, OSError) as exc:
            message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
            errors.append({"placement_id": item.get("placement_id"), "error": message})
    if errors and strict:
        raise VideoEditError("motion เก่าใช้ส่งออกไม่ได้ · ตรวจและสร้างใหม่: " + errors[0]["error"])
    return errors


def verify_media(file, plan):
    """Check the actual video clock/canvas and decode its alpha plane before publishing."""
    from fractions import Fraction
    import subprocess
    import video_edit
    import insert_plan
    result = subprocess.run([video_edit.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_streams",
                             "-of", "json", str(file)], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", creationflags=video_edit.NO_WINDOW, timeout=60)
    try:
        if result.returncode:
            raise ValueError("ffprobe failed")
        stream = json.loads(result.stdout)["streams"][0]
        W, H = int(stream["width"]), int(stream["height"])
        fps = float(Fraction(stream["r_frame_rate"]))
        duration = insert_plan._probe(file)["duration"]
        if (not math.isfinite(fps) or fps <= 0 or duration is None or
                (W, H) != (plan["canvas"]["width"], plan["canvas"]["height"]) or
                abs(duration - plan["duration"]) > 1 / fps + 1e-6 or
                any(value.get("rotation", 0) for value in stream.get("side_data_list", []))):
            raise ValueError("motion canvas, duration or rotation differs from the prepared layout")
    except (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError) as exc:
        raise VideoEditError("rendered motion does not match its measured canvas and duration") from exc
    decoded = subprocess.run([video_edit.FFMPEG, "-v", "error", "-xerror", "-i", str(file), "-map", "0:v:0",
                              "-vf", "alphaextract", "-f", "null", "-"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", creationflags=video_edit.NO_WINDOW, timeout=1200)
    if decoded.returncode:
        raise VideoEditError("rendered motion failed full alpha-channel decode")
    return {"duration": duration, "fps": fps, "width": W, "height": H,
            "sha256": insert_plan.file_digest(file), "alpha_decode": True}


def validate_subject_zones(zones, duration):
    finite = lambda v: not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)
    if not isinstance(zones, (list, tuple)):
        raise VideoEditError("subject zones must be an array")
    for zone in zones:
        keys = ("start", "end", "x", "y", "w", "h")
        if (not isinstance(zone, dict) or set(zone) != set(keys) or any(not finite(zone[k]) for k in keys)
                or not 0 <= zone["start"] < zone["end"] <= duration + 1e-6
                or min(zone["x"], zone["y"]) < 0 or min(zone["w"], zone["h"]) <= 0
                or zone["x"] + zone["w"] > 1 or zone["y"] + zone["h"] > 1):
            raise VideoEditError("subject zones need valid timed normalized x/y/w/h rectangles")
    return zones


def subject_review_pending(zones, start, end):
    cursor = start
    for zone in sorted(zones, key=lambda z: z["start"]):
        if zone["end"] <= cursor:
            continue
        if zone["start"] > cursor + 1e-6:
            break
        cursor = max(cursor, zone["end"])
    return cursor < end - 1e-6


def footprints(motion):
    start, length, bounds = motion.get("start"), motion.get("duration"), motion.get("bounds")
    finite = lambda v: not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)
    rectangle = lambda r: (isinstance(r, list) and len(r) == 4 and all(finite(v) for v in r)
                           and r[0] < r[2] and r[1] < r[3])
    if not finite(start) or not finite(length) or start < 0 or length <= 0 or not rectangle(bounds):
        raise VideoEditError("motion interval or measured bounds are invalid")
    params = motion.get("params", {})
    if not isinstance(params, dict) or not isinstance(params.get("subtitle_events", []), list):
        raise VideoEditError("motion replacement events must be an array")
    parts = params.get("subtitle_events", [])
    if not parts:
        return [(start, start + length, bounds)]
    if any(not isinstance(p, dict) or not rectangle(p.get("bbox")) or not finite(p.get("start"))
           or not finite(p.get("end")) or not 0 <= p["start"] < p["end"] <= length + 1e-6 for p in parts):
        raise VideoEditError("motion replacement events have invalid measured bounds or times")
    return [(start + p["start"], start + p["end"], p["bbox"]) for p in parts]


def check_overlap(first, second):
    """Compare active measured pixels and caption ownership, never the full transparent canvas."""
    if not (first["start"] < second["start"] + second["duration"] and second["start"] < first["start"] + first["duration"]):
        return
    policies = [ownership(m) for m in (first, second)]
    if any(p["mode"] == "legacy_full_card" for p in policies):
        raise VideoEditError("overlapping legacy motion needs a verified recipe")
    if any("file" in m and (not isinstance(m.get("recipe"), dict) or m["recipe"].get("version") != 1) for m in (first, second)):
        raise VideoEditError("overlapping stored motion needs a verified recipe")
    cues = {None} | {t["cue_id"] for p in policies for t in p["targets"]}
    if any(hides(first, role=role, cue_id=cue) and hides(second, role=role, cue_id=cue)
           for cue in cues for role in ROLES):
        raise VideoEditError("overlapping motions cannot both own the same subtitle role")
    if any(a < d and c < b and all(max(r[n], s[n]) < min(r[n+2], s[n+2]) - .05 for n in (0, 1))
           for a,b,r in footprints(first) for c,d,s in footprints(second)):
        raise VideoEditError("motion overlaps another motion; choose another interval or placement")


def check_placement(motion, events, *, width, height, duration, safe_box, subject_zones=(), others=()):
    """Read-only spatial/time gate before allocating a renderer; no position repair or hiding."""
    def finite(value):
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    def rectangle(value):
        return (isinstance(value, list) and len(value) == 4 and all(finite(v) for v in value)
                and value[0] < value[2] and value[1] < value[3])
    def intersects(first, second):
        return all(max(first[n], second[n]) < min(first[n+2], second[n+2]) - .05 for n in (0,1))
    start, length, bounds = motion.get("start"), motion.get("duration"), motion.get("bounds")
    if not finite(start) or not finite(length) or not 0 <= start < start + length <= duration + 1e-6 or not rectangle(bounds):
        raise VideoEditError("motion interval or measured bounds are invalid")
    end = start + length
    if (bounds[0] < width * (1 - safe_box["w"]) / 2 - .05 or bounds[2] > width * (1 + safe_box["w"]) / 2 + .05
            or bounds[1] < height * safe_box["top"] - .05 or bounds[3] > height * safe_box["bottom"] + .05):
        raise VideoEditError(f"motion {start:g}s exceeds the selected safe area; choose another placement")
    policy = ownership(motion)
    if policy["mode"] == "legacy_full_card":
        raise VideoEditError("new motion needs an explicit ownership policy")
    targets = {(target["cue_id"], role) for target in policy["targets"] for role in target["roles"]}
    if targets - {(event.get("cue_id"), event["role"]) for event in events if event["start"] < end and start < event["end"]}:
        raise VideoEditError("motion ownership refers to an unavailable cue or role")
    reproduced = motion.get("params", {}).get("subtitle_events", [])
    occupied = footprints(motion)
    for other in others:
        check_overlap(motion, other)
    import render
    for event in events:
        if not (event["start"] < end and start < event["end"]):
            continue
        owned = hides(motion, role=event["role"], cue_id=event.get("cue_id"))
        if owned:
            needs_copy = policy["mode"] == "replace" or event["role"] == "caption" and policy["caption_policy"] == "replace"
            if needs_copy:
                if event["start"] < start - 1e-6 or event["end"] > end + 1e-6:
                    raise VideoEditError("motion cuts an owned held subtitle; select its full interval")
                if not any(part.get("role") == event["role"] and part.get("cue_id") == event.get("cue_id")
                           and part.get("text") == event["text"] and abs(part["start"] + start - event["start"]) <= 1e-6
                           and abs(part["end"] + start - event["end"]) <= 1e-6 for part in reproduced):
                    raise VideoEditError("motion does not reproduce the cue/role it claims to replace")
        elif any(a < hi and lo < b and intersects(rect, event["bbox"])
                 for lo,hi in render.visible_spans(event["start"], event["end"], others, role=event["role"], cue_id=event.get("cue_id"))
                 for a,b,rect in occupied):
            raise VideoEditError(f"motion {start:g}s overlaps cue {event.get('cue_id')} {event['role']}; choose another placement")
    for zone in validate_subject_zones(subject_zones, duration):
        rect = [zone["x"] * width, zone["y"] * height,
                (zone["x"] + zone["w"]) * width, (zone["y"] + zone["h"]) * height]
        if any(a < zone["end"] and zone["start"] < b and intersects(footprint, rect) for a,b,footprint in occupied):
            raise VideoEditError(f"motion {start:g}s overlaps the confirmed subject zone; choose another placement")


def ownership(motion: dict) -> dict:
    """Read old media conservatively; explicit recipes must declare what they replace."""
    if "ownership" not in motion:
        return {"mode": "legacy_full_card", "targets": [], "caption_policy": "hide"}
    value = motion["ownership"]
    if not isinstance(value, dict) or set(value) != {"mode", "targets", "caption_policy"}:
        raise VideoEditError("motion ownership requires mode, targets and caption_policy")
    mode, targets, policy = value["mode"], value["targets"], value["caption_policy"]
    if mode not in ("decorate", "replace", "full_card") or policy not in ("preserve", "replace", "hide"):
        raise VideoEditError("motion ownership mode or caption policy is invalid")
    if not isinstance(targets, list):
        raise VideoEditError("motion ownership targets must be an array")
    seen = set()
    caption = False
    for target in targets:
        if (not isinstance(target, dict) or set(target) != {"cue_id", "roles"}
                or not isinstance(target["cue_id"], str) or not target["cue_id"].strip()
                or not isinstance(target["roles"], list) or not target["roles"]
                or any(not isinstance(role, str) or role not in ROLES for role in target["roles"])):
            raise VideoEditError("motion target requires an actual cue_id and subtitle roles")
        for role in target["roles"]:
            key = target["cue_id"], role
            if key in seen:
                raise VideoEditError("motion ownership target is duplicated")
            seen.add(key)
            caption |= role == "caption"
    if mode == "decorate" and (targets or policy != "preserve"):
        raise VideoEditError("decoration must preserve all subtitles and captions")
    if mode == "replace" and (not targets or policy == "hide" or caption != (policy == "replace")):
        raise VideoEditError("replacement must name its roles and declare caption replacement explicitly")
    if mode == "full_card" and targets:
        raise VideoEditError("full_card owns its interval; use replace for individual cue roles")
    grouped = {}
    for target in targets:
        grouped.setdefault(target["cue_id"], set()).update(target["roles"])
    return {"mode": mode, "targets": [{"cue_id": cue, "roles": sorted(roles)} for cue, roles in sorted(grouped.items())],
            "caption_policy": policy}


def hides(motion: dict, *, role: str | None, cue_id: str | None) -> bool:
    policy = ownership(motion)
    if policy["mode"] == "legacy_full_card":
        return True
    if role not in ROLES:
        raise VideoEditError("explicit motion ownership needs the subtitle role")
    if policy["mode"] == "decorate":
        return False
    if role == "caption" and policy["caption_policy"] == "preserve":
        return False
    if policy["mode"] == "full_card":
        return True
    return any(target["cue_id"] == cue_id and role in target["roles"] for target in policy["targets"])
