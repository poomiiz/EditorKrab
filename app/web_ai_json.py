"""Web AI: ClipKit's two judgement steps (split into stories; subtitles, emphasis, title, motion per clip) done by
an AI in the browser (claude.ai, ChatGPT...) instead of an agent on this machine.

export_*() writes one text file to upload: the prompt plus the transcript and timings, never the video. The AI
answers with one JSON (schema: prompts/web_ai/schema.md); import_answer() checks it and writes the same files the
local agent writes (<video>.stories.json, clipkit_punch.json, clipkit_hook.json, clipkit_caption.json, motion), so
run_clip / the editor carry on as before and the CapCut project is built here from this machine's own media.
The same file works for an AI on this machine: it saves the JSON to answer_file() and the editor imports it.
"""
from __future__ import annotations

import atomic
import json
import cue_data
import math
import re
import time
from pathlib import Path
from typing import Any

import capcut_edit
import project_lock
import web_ai_identity as identity
import web_ai_transaction as transaction
import render
import video_edit
import story_selection
import subtitle_plan
from video_edit import VideoEditError

VERSION = identity.VERSION
PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
LOOKS = subtitle_plan.LOOKS


def _motion_menu() -> list[dict[str, Any]]:
    """Every motion/<name> template the AI may place: name, label, role and its fields (quick ones need none)."""
    import kit_settings
    import video_editor
    return [{"template": k, "label": v.get("label", k), "role": v.get("role", ""),
             **({} if k in video_editor.QUICK_MOTION else {"params": {
                 f["key"] + ("" if f.get("required") else "?"): f["type"] if f["type"] != "select" else [o[0] for o in f["options"]]
                 for f in v.get("fields", []) if f["type"] not in ("font",)}})}
            for k, v in kit_settings.motion_templates().items()]


def _prompt(name: str) -> str:
    bare = lambda f: re.sub(r"<!--.*?-->\s*", "", f.read_text(encoding="utf-8"), flags=re.S).strip()  # noqa: E731
    return bare(PROMPTS / "web_ai" / name).replace("{STORY_RULES}", bare(PROMPTS / "story_split.md"))


def packet_file(step: str, target: str) -> Path:
    return (Path(target).with_name(Path(target).name + ".web-ai-stories.md") if step == "stories"
            else Path(target) / "clipkit_web_ai.md")


def answer_file(step: str, target: str) -> Path:
    """Where an AI on this machine (Claude Code...) saves its answer instead of replying in chat."""
    return packet_file(step, target).with_suffix(".answer.json")


def _packet(prompt: str, data: dict[str, Any], out: Path) -> Path:
    ans = out.with_suffix(".answer.json")
    local_next = ("แล้วผู้ใช้ตรวจแผนก่อนกดใช้ภาพแทรก" if data.get("operation") == "inserts"
                  else "แล้ว EditorKrab จะนำเข้าเอง")
    atomic.write_text(out, "# คำสั่งสำหรับ AI: ทำงานจากไฟล์แนบนี้ได้ทันที\n\n"
                   "> ใช้ AI บนเว็บได้โดยไม่ต้องติดตั้ง agent: อ่านคำสั่งและข้อมูลทั้งหมดในไฟล์นี้ "
                   "ทำงานตามขั้นที่ระบุ แล้วตอบ JSON ตามรูปแบบด้านล่าง ผู้ใช้ไม่ต้องพิมพ์ prompt เพิ่ม "
                   "และจะนำคำตอบกลับไปวางใน EditorKrab\n\n"
                   f"> ทางเลือกสำหรับ AI ที่เขียนไฟล์ในเครื่องได้: บันทึก JSON ลง `{ans.name}` ในโฟลเดอร์เดียวกับไฟล์นี้ {local_next}\n\n"
                   + prompt.rstrip() + "\n\n## ข้อมูล (อย่าแก้ช่อง clipkit_web_ai, step, video, project)\n\n```json\n"
                   + json.dumps(data, ensure_ascii=False, indent=1) + "\n```\n", encoding="utf-8")
    if ans.is_file():
        ans.replace(ans.with_name(ans.name + f".before-export-{time.time_ns()}"))
    return out


def _issue_packet(step, target, prompt, data):
    active = identity.state_dir(step, target) / "active.json"
    previous = active.read_text(encoding="utf-8") if active.is_file() else None
    output = packet_file(step, target)
    old_packet = output.read_text(encoding="utf-8") if output.is_file() else None
    try:
        operation = {"operation": data["operation"]} if data.get("operation") else {}
        data.update(identity.issue(step, target, {"max_minutes": data["max_minutes"]}, **operation))
        return _packet(prompt, data, packet_file(step, target))
    except Exception:
        if previous is None:
            active.unlink(missing_ok=True)
        else:
            atomic.write_text(active, previous)
        if old_packet is None:
            output.unlink(missing_ok=True)
        else:
            atomic.write_text(output, old_packet)
        raise


def _caption_lang(path: str) -> str | None:
    """The language the preset's small caption needs from the AI; None = no caption (or Thai: the spoken line)."""
    f = Path(path) / "clipkit_style.json"
    style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    if style.get("anim") != "pair":
        return None
    look = render.pair_look(style)
    return look["caption_lang"] if look["caption"] and look["caption_lang"] != "th" else None


def story_target(video: str) -> float | None:
    path = Path(video + ".story-preferences.json")
    value = json.loads(path.read_text(encoding="utf-8")).get("max_minutes") if path.is_file() else None
    return _target_minutes(value)


def has_story_target(video: str) -> bool:
    return Path(video + ".story-preferences.json").is_file()


def _target_minutes(value: float | None) -> float | None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
        raise VideoEditError("ความยาวต้องเป็นจำนวนนาทีที่มากกว่า 0")
    return value


def save_story_target(video: str, value: float | None) -> bool:
    value = _target_minutes(value)
    changed = not has_story_target(video) or story_target(video) != value
    if changed:
        answer = answer_file("stories", video)
        if answer.is_file():
            answer.replace(answer.with_name(answer.name + f".before-target-{time.time_ns()}"))
        stamp = time.time_ns()
        for stories in (video_edit.stories_file(video), story_selection.selection_file(video)):
            if stories.is_file():
                stories.replace(stories.with_name(stories.name + f".before-target-{stamp}"))
        atomic.write_text(Path(video + ".story-preferences.json"), json.dumps({"max_minutes": value}), encoding="utf-8")
    return changed


@project_lock.edit
def export_stories(video: str, max_minutes: float | None = None) -> Path:
    transaction.recover("stories", video)
    if max_minutes is not None or not has_story_target(video):
        save_story_target(video, max_minutes)
    max_minutes = story_target(video)
    tf = video_edit.transcript_file(video)
    if not tf.is_file():
        raise VideoEditError(f"ยังไม่ได้ถอดเสียง: ไม่มี {tf.name} (กด ถอดเสียงและแบ่งเรื่อง ก่อน)")
    lines = story_selection.source_rows(json.loads(tf.read_text(encoding="utf-8")))
    data = {"clipkit_web_ai": VERSION, "step": "stories", "video": Path(video).name, "max_minutes": max_minutes, "lines": lines}
    prompt, brief = _prompt("stories.md"), video_edit.read_brief(video)
    if brief:  # the person's own words go first, so the AI picks stories for them
        prompt = (f"## ความต้องการของผู้ตัดต่อ\n\n{brief}\n\nเลือกและแบ่งเรื่องให้ตรงกับความต้องการนี้ "
                  f"โดยยังทำตามกฎทุกข้อด้านล่าง\n\n" + prompt)
    if max_minutes is not None:
        prompt = (f"## ความยาวเป้าหมาย\n\nเลือกเนื้อหาให้แต่ละคลิปยาวราว {max_minutes:g} นาที "
                  "เริ่มและจบให้ครบประเด็น สั้นกว่าได้หรือยาวกว่าเล็กน้อย ไม่ใช่เพดานตายตัว "
                  "อย่าตัดประโยคหรือเติมเนื้อหาเพื่อให้ครบเวลา\n\n" + prompt)
    else:
        prompt = "ความยาว: ตามเนื้อหา ผู้ใช้เลือกแล้ว ไม่ต้องถามเพิ่ม เลือกช่วงที่จบครบประเด็น\n\n" + prompt
    return _issue_packet("stories", video, prompt, data)


@project_lock.edit
def export_clip(path: str, max_minutes: float | None = None) -> Path:
    """max_minutes: about how long the person wants the clip; a rough aim for the cut, not a hard limit."""
    if max_minutes is None:
        meta = Path(path) / "clipkit.json"
        raw = json.loads(meta.read_text(encoding="utf-8")).get("raw") if meta.is_file() else None
        max_minutes = story_target(raw) if raw else None
    max_minutes = _target_minutes(max_minutes)
    subs = capcut_edit.read_draft(path)["subtitles"]
    if not subs:
        raise VideoEditError("ยังไม่มีซับ — ถอดเสียงก่อน")
    lines = [s["text"] for s in subs]
    words_file = Path(path) / "clipkit_words.json"
    word_times = cue_data.load(path, "clipkit_words.json", subs)
    atomic.write_text((Path(path) / "clipkit_lines.json"), json.dumps(lines, ensure_ascii=False, indent=1), encoding="utf-8")
    data = {"clipkit_web_ai": VERSION, "step": "clip", "project": Path(path).name, "max_minutes": max_minutes, "caption_language": _caption_lang(path),
            "lines": [{"n": n, "cue_id": s["id"], "start": s["start"], "end": s["end"], "text": s["text"],
                       "words": [{"word": index, "start": s["start"] + a, "end": s["start"] + b, "text": w}
                                 for index, (a, b, w) in enumerate(word_times.get(cue_data.key(s["id"]), []), 1)]} for n, s in enumerate(subs, 1)],
            "motion_templates": _motion_menu()}
    style = render._json(Path(path) / "clipkit_style.json", {})
    draft = capcut_edit._load(Path(path))
    canvas = draft["canvas_config"]
    look = render.pair_look(style, canvas["width"], canvas["height"])
    family, font = render._font(look.get("font"))
    data["subtitle_layout"] = {"fixed_size": look["fixed_size"], "normal_size": look["lead"][0],
                               "emphasis_size": look["punch"][0], "normal_y": look["lead"][1],
                               "emphasis_y": look["punch"][1], "canvas": {key: canvas[key] for key in ("width", "height")},
                               "font_family": family, "font_file": font.name, "safe_box": render.safe_box(style)}
    if style.get("anim", "none") in ("none", "pop"):
        events = validate_subtitles(path)
        data["subtitle_layout"] = {"mode":style.get("anim", "none"),"canvas":dict(canvas),"safe_box":render.safe_box(style),
            "events":[{**{key:event[key] for key in ("role","cue_id","text","start","end","size_px","center_x_px","center_y_px","rotation","bbox")},
                       "font_file":Path(event["font_file"]).name} for event in events]}
    import motion_plan
    data["motion_subject_zones"] = motion_plan.validate_subject_zones(style.get("motion_subject_zones", []), draft["duration"] / capcut_edit.US)
    prompt = _prompt("clip.md")
    if max_minutes:
        prompt = prompt.replace("## 1. Subtitle", f"ผู้ตัดต่ออยากได้คลิปยาวราว {max_minutes:g} นาที (คลิปตอนนี้ยาว "
                                f"{subs[-1]['end'] / 60:.1f} นาที): ตัดพูดซ้ำ พูดผิด ออกนอกเรื่องก่อน ถ้ายังยาวกว่ามาก ให้ตัดบรรทัดที่เป็น"
                                "รายละเอียดรองที่ตัดแล้วยังเล่าต่อกันรู้เรื่อง คุมคร่าวๆ เกินได้บ้าง อย่าตัดใจความจนเรื่องขาด\n\n## 1. Subtitle", 1)
    return _issue_packet("clip", path, prompt, data)


@project_lock.edit
def export_inserts(path: str, files=None, *, folder=None, provenance=None) -> tuple[Path, Path]:
    """Reuse the contact-sheet matcher with verified copies and the existing v2 clip transaction."""
    import insert_plan
    transaction.recover("clip", path)
    if files is not None and folder is not None:
        raise VideoEditError("เลือก files หรือ folder อย่างเดียว")
    chosen = files if files is not None else folder
    assets = insert_plan.register_assets(path, chosen, provenance) if chosen is not None else insert_plan.catalog(path)
    if not assets["candidates"]:
        raise VideoEditError("เลือกไฟล์ภาพแทรกก่อนส่งออกคำสั่งจับคู่")
    sheet = insert_plan.contact_sheet(assets, Path(path) / "clipkit_insert_sheet.jpg")
    from insert_geometry import RAMP_IN, RAMP_FROM
    draft = capcut_edit._load(Path(path))
    style = render._json(Path(path) / "clipkit_style.json", {})
    data = {"clipkit_web_ai": VERSION, "step": "clip", "operation": "inserts", "project": Path(path).name,
            "max_minutes": None, "contact_sheet": sheet.name, "assets": insert_plan.public_assets(assets),
            "opportunities": insert_plan.opportunities(path),
            "insert_settings": {"canvas": {key: draft["canvas_config"][key] for key in ("width", "height")},
                                "ramp": bool(style.get("ramp")), "ramp_in": RAMP_IN, "ramp_from": RAMP_FROM}}
    return _issue_packet("clip", path, _prompt("inserts.md"), data), sheet


def _insert_rows(path, ans, *, assets=None):
    import insert_plan
    _head(ans, "clip", "project", Path(path).name)
    if ans.get("operation") != "inserts":
        raise VideoEditError("คำตอบนี้ไม่ใช่งานจับคู่ภาพแทรก")
    if any(key in ans for key in ("lines", "cut", "motion", "hook", "caption")):
        raise VideoEditError("งานจับคู่ภาพแทรกห้ามแก้ซับหรือตัดเนื้อหา · ส่งคำตอบภาพแทรกแยก")
    assets = insert_plan.catalog(path) if assets is None else assets
    items = ans.get("place")
    if not isinstance(items, list) or any(not isinstance(item, dict) or item.get("asset_id") not in assets["candidates"] for item in items):
        raise VideoEditError("place ต้องเลือก asset_id จากไฟล์ที่ส่งออกครั้งนี้เท่านั้น")
    return insert_plan.normalize(path, items, assets=assets)


def preview_inserts(path: str, text: str) -> dict[str, Any]:
    """Review the same strict plan before explicit apply; no file or active-packet mutation."""
    with project_lock.writer(path, recover=False):
        answer = parse(text)
        identity.verify("clip", path, answer)
        return {"operation": "inserts", "project": Path(path).name, "broll": _insert_rows(path, answer)}


def preview_clip(path: str, text: str) -> dict[str, Any]:
    with project_lock.writer(path, recover=False):
        if not text.strip():
            saved = answer_file("clip", path)
            if not saved.is_file():
                return {"waiting": True}
            text = saved.read_text(encoding="utf-8-sig")
            local_answer = text
        else:
            local_answer = None
        answer = parse(text)
        _head(answer, "clip", "project", Path(path).name)
        if answer.get("operation") is not None:
            raise VideoEditError("เลือกช่องตรวจภาพแทรกสำหรับคำตอบภาพแทรก")
        import video_editor
        import motion_plan
        def prepare(work):
            with motion_plan.preview_cut(work) as regenerated:
                result = _prepare_clip(str(work), answer, preview=True)
            result["motion_after"] = (len(video_editor._overlays(str(work))) if "motion" not in answer else result["motion"])
            if regenerated:
                result.update(motion_action="regenerate", motion_plan=regenerated)
            return result
        result = transaction.preview_clip(path, answer, prepare)
        result["motion_before"] = len(video_editor._overlays(path))
        if local_answer is not None:
            result["answer"] = local_answer
        return result


def parse(text: str) -> dict[str, Any]:
    """The AI's answer as pasted: bare JSON, or JSON inside a ```json block with words around it."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    body = m.group(1) if m else text.strip()
    try:
        out = json.loads(body)
    except ValueError as exc:
        raise VideoEditError(f"ไม่พบคำตอบที่ใช้ได้ คัดลอกคำตอบของ AI ทั้งหมด (รวมกล่องโค้ด) มาวางใหม่ ({exc})") from exc
    if not isinstance(out, dict):
        raise VideoEditError("คำตอบต้องเป็น JSON object { ... }")
    return out


def _head(ans: dict[str, Any], step: str, key: str, name: str) -> None:
    version = 1 if step == "inserts" else VERSION
    if ans.get("clipkit_web_ai") != version:
        raise VideoEditError(f"clipkit_web_ai ต้องเป็น {version} (ได้ {ans.get('clipkit_web_ai')!r}): ใช้ไฟล์ที่ส่งออกจาก EditorKrab รุ่นนี้")
    if ans.get("step") != step:
        raise VideoEditError(f"คำตอบนี้เป็นขั้น {ans.get('step')!r} แต่กำลังนำเข้าขั้น {step!r}")
    if ans.get(key) != name:
        raise VideoEditError(f"คำตอบนี้เป็นของ {ans.get(key)!r} ไม่ใช่ {name!r}: นำเข้าผิดไฟล์")


def _num(x: Any, where: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise VideoEditError(f"{where} ต้องเป็นตัวเลขวินาที (ได้ {x!r})")
    return float(x)


def _story_selection(video: str, ans: dict[str, Any]) -> list[dict[str, Any]]:
    _head(ans, "stories", "video", Path(video).name)
    phrases = json.loads(video_edit.transcript_file(video).read_text(encoding="utf-8"))
    out = video_edit.validate_stories(ans.get("stories"), phrases, max_minutes=story_target(video))
    return out


def _item(it: Any, where: str) -> list:
    text = it[0] + it[1] if isinstance(it, list) and len(it) >= 2 and all(isinstance(x, str) for x in it[:2]) else "invalid"
    return subtitle_plan.validate_items(text, [it], cue_id=where)[0]


def validate_subtitles(path: str, *, picks: dict | None = None, captions: dict | None = None,
                       style: dict | None = None, rows: list[dict] | None = None, layout_errors: list | None = None) -> list[dict]:
    """Read-only shared preflight against real project clocks, canvas, preset and font."""
    folder = Path(path)
    draft = capcut_edit._load(folder)
    original_rows = capcut_edit.read_draft(path)["subtitles"]
    rows = original_rows if rows is None else rows
    canvas = draft["canvas_config"]
    duration = max((segment["target_timerange"]["start"] + segment["target_timerange"]["duration"]) / video_edit.US
                   for segment in capcut_edit._video_track(draft)["segments"])
    active_style = render._json(folder / "clipkit_style.json", {}) if style is None else style
    words = cue_data.load(folder, "clipkit_words.json", original_rows)
    if active_style.get("preset"):
        render.pair_look(active_style, canvas["width"], canvas["height"])
    if active_style.get("anim", "none") in ("none", "pop"):
        render._resolved_pair_cues(rows, words, cue_data.load(folder, "clipkit_punch.json", original_rows) if picks is None else picks, duration=duration)
        return render.plain_events(draft, rows, words, style=active_style, duration=duration)
    return render.validate_pair_cues(rows, words,
                                    cue_data.load(folder, "clipkit_punch.json", original_rows) if picks is None else picks,
                                    style=active_style,
                                    W=canvas["width"], H=canvas["height"],
                                    captions=cue_data.load(folder, "clipkit_caption.json", original_rows) if captions is None else captions,
                                    duration=duration, layout_errors=layout_errors)


def _prepare_clip(path: str, ans: dict[str, Any], *, insert_assets=None, preview=False) -> dict[str, Any]:
    """Run existing editing operations only inside the transaction staging folder."""
    _head(ans, "clip", "project", Path(path).name)
    if ans.get("operation") == "inserts":
        items = _insert_rows(path, ans, assets=insert_assets)
        placed = Path(path) / "clipkit_placed.json"
        previous = json.loads(placed.read_text(encoding="utf-8")) if placed.is_file() else {}
        if not isinstance(previous, dict):
            raise VideoEditError("รายการภาพแทรก/เพลงเดิมไม่ถูกต้อง")
        previous["broll"] = items
        atomic.write_text(placed, json.dumps(previous, ensure_ascii=False, indent=1, allow_nan=False))
        return {"written": ["clipkit_placed.json"], "inserts": len(items), "broll": items}
    if ans.get("operation") is not None:
        raise VideoEditError("operation ไม่ถูกต้อง")
    subs = capcut_edit.read_draft(path)["subtitles"]
    lines = ans.get("lines")
    if (not isinstance(lines, list) or any(not isinstance(x, dict) or type(x.get("n")) is not int for x in lines)
            or [x["n"] for x in lines] != list(range(1, len(subs) + 1))):
        raise VideoEditError(f"lines ต้องมีครบ {len(subs)} บรรทัด เรียง n = 1..{len(subs)} ตามไฟล์ที่ส่งไป")
    if any(x.get("cue_id") != s["id"] for x, s in zip(lines, subs)):
        raise VideoEditError("cue ID ไม่ตรงกับซับล่าสุด · ส่งออกคำสั่ง AI ใหม่")
    cut = [] if ans.get("cut") is None else ans["cut"]
    if not isinstance(cut, list) or any(type(n) is not int or n not in range(1, len(subs) + 1) for n in cut) or len(set(cut)) >= len(subs):
        raise VideoEditError(f"cut ต้องเป็นรายการเลขบรรทัด 1..{len(subs)} ที่จะตัดทิ้ง และต้องเหลืออย่างน้อย 1 บรรทัด")
    groups, continuation = [], set()
    for x in lines:
        if x["n"] in continuation:
            if set(x) - {"n", "cue_id"}:
                raise VideoEditError("บรรทัดต่อของกลุ่มต้องมีแค่ n และ cue_id")
            continue
        last = x.get("through", x["n"])
        if isinstance(last, bool) or not isinstance(last, int) or not x["n"] <= last <= len(subs):
            raise VideoEditError("through ต้องเป็นเลขบรรทัดสุดท้ายของกลุ่มต่อเนื่อง")
        if last > x["n"]:
            if any(n in cut for n in range(x["n"], last + 1)):
                raise VideoEditError("กลุ่มซับห้ามทับบรรทัดที่ตัด")
            groups.append((x["n"], last))
            continuation.update(range(x["n"] + 1, last + 1))
    merged = capcut_edit.merge_subtitles(path, groups, dry=True) if groups else [
        {**s, "n": n} for n, s in enumerate(subs, 1)]
    punch = {}
    for s in merged:
        x = lines[s["n"] - 1]
        if x["n"] in cut:
            continue
        items = x.get("items")
        if not isinstance(items, list) or not items:
            raise VideoEditError(f"บรรทัด {x['n']}: items ต้องมีอย่างน้อย 1 ท่อน")
        items = subtitle_plan.validate_items(s["text"], items, cue_id=s["id"])
        if x.get("cue_id") is not None and x["cue_id"] != s["id"]:
            raise VideoEditError("cue ID ไม่ตรงกับซับล่าสุด · ส่งออกคำสั่ง AI ใหม่")
        punch[cue_data.key(s["id"])] = items
    hook = ans.get("hook", [])
    if not isinstance(hook, list) or len(hook) > 2 or any(
            not isinstance(h, dict) or not isinstance(h.get("text"), str) or not h["text"].strip()
            or h.get("color") not in render.HOOK_COLORS for h in hook):
        raise VideoEditError('hook ต้องเป็น [] หรือ 1-2 บรรทัด [{"text": "...", "color": "white|orange|red"}]')
    lang = _caption_lang(path)
    caption = None
    if lang:
        cap = ans.get("caption")
        if not isinstance(cap, dict) or any(not isinstance(cap.get(str(n)), str) or not cap[str(n)].strip()
                                            for n in range(1, len(subs) + 1) if n not in cut and n not in continuation):
            raise VideoEditError(f'preset นี้มีซับแปล ({lang}): caption ต้องเป็น {{"1": "...", ...}} ครบทุกบรรทัด')
        caption = {cue_data.key(s["id"]): cap[str(s["n"])].strip() for s in merged if s["n"] not in cut}
    motion = ans.get("motion", [])
    import video_editor
    quick = video_editor.QUICK_MOTION
    if not isinstance(motion, list) or any(not isinstance(m, dict) or type(m.get("n")) is not int or m.get("n") not in range(1, len(subs) + 1)
                                           or m["n"] in cut or m["n"] in continuation for m in motion):
        raise VideoEditError('motion ต้องเป็น [{"n": เลขบรรทัด, "template": "...", "params": {...}}]')
    for m in motion:  # checked before anything is written; params only for templates not made from the line
        if set(m) - {"n", "template", "params", "anchor", "placement", "ownership", "reason"}:
            raise VideoEditError("motion มีช่องที่ไม่รองรับ")
        if not isinstance(m.get("template"), str):
            raise VideoEditError("motion template ต้องเป็นชื่อเทมเพลต")
        if any(m.get(key) is not None and not isinstance(m[key], dict) for key in ("placement", "ownership")):
            raise VideoEditError("motion placement และ ownership ต้องเป็น object")
        if "anchor" in m and not isinstance(m["anchor"], dict):
            raise VideoEditError("motion anchor ต้องเป็น object")
        if "reason" in m and (not isinstance(m["reason"], str) or not m["reason"].strip() or len(m["reason"]) > 500):
            raise VideoEditError("motion reason ต้องมีข้อความไม่เกิน 500 ตัวอักษร")
        if m.get("params") is not None:
            video_editor.motion_params(m.get("template"), m["params"], 3.0)
        elif m.get("template") not in quick:
            raise VideoEditError(f'motion บรรทัด {m["n"]}: "{m.get("template")}" ต้องมี params (ช่องตาม motion_templates)')

    folder = Path(path)
    if "motion" in ans:
        # The transaction owns this staging folder; explicit lists replace, omission preserves.
        atomic.write_text(folder / video_editor.OVERLAYS, "[]", encoding="utf-8")
    if groups:
        if cut:
            ends = [max(s["end"], nxt["start"]) for s, nxt in zip(subs, subs[1:])] + [subs[-1]["end"]]
            capcut_edit.trim_pauses(path, ranges=[(subs[n - 1]["start"], ends[n - 1]) for n in sorted(set(cut))])
        shifted = lambda n: n - sum(k < n for k in set(cut))
        grouped = capcut_edit.merge_subtitles(path, [(shifted(a), shifted(b)) for a, b in groups])
        current = dict(zip([s["n"] for s in merged if s["n"] not in cut], grouped))
    written = ["clipkit_punch.json", "clipkit_hook.json"]
    if groups:
        written.append("merge_subs")
    cue_data.save(folder, "clipkit_punch.json", punch)
    atomic.write_text((folder / "clipkit_hook.json"), json.dumps([{"text": h["text"].strip(), "color": h["color"]} for h in hook],
                                                         ensure_ascii=False, indent=1), encoding="utf-8")
    if caption is not None:
        cue_data.save(folder, "clipkit_caption.json", caption)
        written.append("clipkit_caption.json")
    if cut and not groups:  # the footage of each dropped line (up to the next line) goes, its subtitle with it
        ends = [max(s["end"], nxt["start"]) for s, nxt in zip(subs, subs[1:])] + [subs[-1]["end"]]
        capcut_edit.trim_pauses(path, ranges=[(subs[n - 1]["start"], ends[n - 1]) for n in sorted(set(cut))])
        written.append(f"ตัด {len(set(cut))} บรรทัด")
        kept = {s["id"]: s for s in capcut_edit.read_draft(path)["subtitles"]}
        subs = [kept.get(s["id"], s) for s in subs]
    subtitle_events = validate_subtitles(path)
    reviewed = []
    if motion:
        import insert_plan
        plans, candidates = [], []
        for m in motion:
            s = current[m["n"]] if groups else subs[m["n"] - 1]
            anchor = m.get("anchor", {"kind": "timeline", "start": s["start"]})
            if anchor.get("kind") != "timeline" and anchor.get("cue_id") != s["id"]:
                raise VideoEditError("motion anchor ต้องอ้าง cue ของบรรทัดที่เลือก")
            start, _ = insert_plan.resolve_anchor(path, anchor)
            end = s["end"]
            if m["template"] == "sentence-pair":
                end = max([end] + [event["end"] for event in subtitle_events if event.get("cue_id") == s["id"]])
            request = video_editor.OverlayRequest(path=path, template=m["template"], params=m.get("params"),
                start=start, end=end, text=s["text"], cue_id=s["id"], anchor=anchor,
                **{key: m[key] for key in ("placement", "ownership", "reason") if key in m})
            plan = video_editor.prepare_overlay(request, previous=candidates)
            plans.append((request, plan))
            candidates.append(plan["candidate"])
        reviewed = [video_editor.overlay_plan(request, plan) for request, plan in plans]
        if not preview:
            # Every requested item must pass before the first renderer allocates an asset.
            rendered = [video_editor.render_overlay(request, plan) for request, plan in plans]
            atomic.write_text(folder / video_editor.OVERLAYS,
                              json.dumps(sorted(rendered, key=lambda item: item["start"]), ensure_ascii=False,
                                         indent=1, allow_nan=False), encoding="utf-8")
    if "motion" in ans:
        written.append(video_editor.OVERLAYS)
    out = {"written": written, "lines": len(punch), "cut": len(set(cut)), "motion": len(motion)}
    if preview:
        out.update(project=Path(path).name, motion_plan=reviewed,
                   motion_action="preserve" if "motion" not in ans else "replace" if motion else "clear",
                   hook=hook, subtitle_events=[{key: event[key] for key in ("role", "text", "start", "end")}
                                               for event in subtitle_events])
    return out


def _import(step: str, target: str, ans: dict[str, Any]) -> dict[str, Any]:
    _head(ans, step, "video" if step == "stories" else "project", Path(target).name)
    with project_lock.writer(target, recover=False):
        transaction.recover(step, target)
        previous = transaction.receipt(step, target, ans)
        if previous is not None:
            return previous
        identity.verify(step, target, ans)
        insert_assets = None
        if ans.get("operation") == "inserts":
            import insert_plan
            insert_assets = insert_plan.catalog(target)

        def prepare(work, assets):
            if step == "stories":
                stories = _story_selection(target, ans)
                atomic.write_text(work / "stories.json", json.dumps(stories, ensure_ascii=False, indent=1))
                binding = story_selection.selection_binding(target, work / "stories.json")
                atomic.write_text(work / "selection.json", json.dumps(binding, ensure_ascii=False, indent=1))
                return {"stories": stories, "input_revision": binding["input_revision"]}
            import host_platform
            import kit_settings
            if host_platform.capcut_running():
                raise VideoEditError("ปิด CapCut ก่อนนำเข้าคำตอบ AI เพื่อรักษางานที่เปิดอยู่")
            with kit_settings.motion_destination(assets):
                return (_prepare_clip(str(work), ans, insert_assets=insert_assets) if insert_assets is not None
                        else _prepare_clip(str(work), ans))

        return transaction.apply(step, target, ans, prepare)


def import_clip(path: str, ans: dict[str, Any]) -> dict[str, Any]:
    return _import("clip", path, ans)


def import_stories(video: str, ans: dict[str, Any]) -> list[dict[str, Any]]:
    return _import("stories", video, ans)["stories"]


def import_answer(step: str, target: str, text: str) -> dict[str, Any]:
    """Paste, local polling and CLI share one revision check, transaction and durable receipt."""
    if step not in ("stories", "clip"):
        raise VideoEditError(f"unknown step: {step}")
    with project_lock.writer(target, recover=False):
        transaction.recover(step, target)
        saved = answer_file(step, target) if not text.strip() else None
        if saved and not saved.is_file():
            return {"waiting": True}
        payload = saved.read_text(encoding="utf-8-sig") if saved else text
        ans = parse(payload)
        if saved and ans.get("operation") == "inserts" and transaction.receipt(step, target, ans) is None:
            identity.verify(step, target, ans)
            _insert_rows(target, ans)
            return {"waiting": True, "review_required": True, "operation": "inserts", "answer": payload}
        out = _import(step, target, ans)
        if saved:
            # Keep local agent evidence; a response/unlink failure cannot replay a committed cut.
            if saved.read_text(encoding="utf-8-sig") == payload:
                saved.replace(saved.with_name(saved.name + ".imported-" + ans["packet_id"]))
        return out
