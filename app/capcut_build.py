"""The finished clip as a CapCut project, laid out the way clips are built by hand, so a person can
open it in CapCut and keep editing: cut footage (zoom cuts as 108 % clips, skin effect), white and coloured
subtitle lines on their own tracks, the title, the small English caption, b-roll on an overlay track, a click
on every coloured word, and the music bed.

Built from the same timeline as the MP4 export (render.render_draft dry run) into a new revision of the project,
"<project> · CapCut <revision>", so existing projects stay as they are. Styles come from app/capcut_templates.json
(scripts/capcut/make_templates.py, pulled from hand-made CapCut projects). Colour correction is left to CapCut.
"""
from __future__ import annotations

import atomic
import host_platform
import copy
import json
import cue_data
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

import capcut_edit
import project_lock
import draft_quality
import render
import video_edit
from video_edit import US, VideoEditError

TEMPLATES = Path(__file__).with_name("capcut_templates.json")


def _machine_paths(text: str) -> str:
    """The templates name CapCut's fonts and effects by {LOCALAPPDATA} / {CAPCUT_APP}: point them at this
    machine's folders (the newest installed CapCut version)."""
    import os
    if host_platform.is_macos():
        return host_platform.mac_template_paths(text, capcut_edit.kitconfig._load())
    local = Path(os.environ.get("LOCALAPPDATA", "")).as_posix()
    apps = sorted((Path(local) / "CapCut" / "Apps").glob("*.*.*"), key=lambda p: [int(x) for x in p.name.split(".") if x.isdigit()])
    app = apps[-1].as_posix() if apps else f"{local}/CapCut/Apps/missing"
    return text.replace("{CAPCUT_APP}", app).replace("{LOCALAPPDATA}", local)


def _id() -> str:
    return str(uuid.uuid4()).upper()


def _us(t: float) -> int:
    return int(round(t * US))


def _clone(draft: dict, tpl: dict, material: dict, kind: str) -> dict:
    """A new segment (and its material and helper materials) from a template block; returns the segment."""
    mat = copy.deepcopy(material)
    mat["id"] = _id()
    draft["materials"].setdefault(kind, []).append(mat)
    seg = copy.deepcopy(tpl["seg"])
    seg["id"] = _id()
    seg["material_id"] = mat["id"]
    refs = []
    for k, helper in tpl["refs"]:
        h = copy.deepcopy(helper)
        h["id"] = _id()
        draft["materials"].setdefault(k, []).append(h)
        refs.append(h["id"])
    seg["extra_material_refs"] = refs
    return seg


def _text(draft: dict, role: str, words: str, a: float, b: float, y: float, size: float,
          fill: list | None = None, stroke: list | None = None) -> dict:
    tpl = _T[role]
    mat = copy.deepcopy(tpl["mat"])
    body = json.loads(mat["content"])
    st = copy.deepcopy(body["styles"][0])
    st["range"] = [0, len(words)]
    st["size"] = size
    if fill:
        st["fill"]["content"]["solid"]["color"] = list(fill)
    if stroke and st.get("strokes"):
        st["strokes"][0]["content"]["solid"]["color"] = list(stroke)
    body.update(text=words, styles=[st])
    mat.update(content=json.dumps(body, ensure_ascii=False), font_size=float(size), text_size=float(size), name=words[:20])
    seg = _clone(draft, tpl, mat, "texts")
    seg["target_timerange"] = {"start": _us(a), "duration": max(_us(b - a), 1)}
    seg["clip"]["transform"] = {"x": 0.0, "y": y}
    seg["clip"]["scale"] = {"x": 1.0, "y": 1.0}
    return seg



def _event_text(draft: dict, role: str, event: dict, W: int, H: int) -> dict:
    """Write measured pixels back to native units without a second fit, clamp or stack pass."""
    k = render.CAPCUT_PX * min(W, H) / 1080
    segment = _text(draft, role, event["text"], event["start"], event["end"],
                    1 - 2 * event["center_y_px"] / H, event["size_px"] / k, event["fill"], event["stroke"])
    segment["clip"].update(transform={"x": 2 * event["center_x_px"] / W - 1, "y": 1 - 2 * event["center_y_px"] / H},
                           rotation=event["rotation"])
    material = next(m for m in draft["materials"]["texts"] if m["id"] == segment["material_id"])
    body = json.loads(material["content"])
    style = body["styles"][0]
    style.setdefault("font", {})["path"] = event["font_file"]
    strokes = style.get("strokes") or [{"content": {"solid": {"color": event["stroke"]}}}]
    strokes[0].setdefault("content", {}).setdefault("solid", {})["color"] = event["stroke"]
    strokes[0]["width"] = max(0, (event["outline_px"] - event.get("outline_extra_px", 2)) / event["size_px"] / .6)
    style["strokes"] = strokes[:1]
    material.update(content=json.dumps(body, ensure_ascii=False), font_path=event["font_file"],
                    font_name=event["font_family"], font_title=event["font_family"], line_max_width=1.0,
                    has_shadow=bool(event["shadow"]), shadow_color="#000000")
    # Provider animations can move/scale the measured text outside this plan. Native keyframes
    # express the same seekable fade/pop as ASS and HyperFrames without a provider cache dependency.
    segment["extra_material_refs"] = []
    segment["keyframe_refs"], segment["common_keyframes"] = [], []
    channels = ({"KFTypeScaleX": (1.3, 1), "KFTypeScaleY": (1.3, 1)} if event["effect"] == "pop"
                else {"KFTypeAlpha": (0, 1)} if event["effect"] == "fade" else {})
    span = min(event["end"] - event["start"], .14 if event["effect"] == "pop" else .08)
    for property_name, values in channels.items():
        segment["common_keyframes"].append({"id": _id(), "material_id": "", "property_type": property_name,
            "keyframe_list": [{"id": _id(), "curveType": "Line", "time_offset": _us(offset),
                               "left_control": {"x": 0, "y": 0}, "right_control": {"x": 0, "y": 0},
                               "values": [value], "string_value": "", "graphID": ""}
                              for offset, value in ((0, values[0]), (span, values[1]))]})
    return segment


def _texts(draft: dict, role: str, words: str, a: float, b: float, y: float, size: float, fill: list | None,
           stroke: list | None, W: int, H: int, style: dict, x: float = 0, rot: float = 0) -> list[dict]:
    """One text per piece: one line only and never off screen, a long subtitle goes on as the next piece in time
    with the voice (render.one_line). Measured with the role's own font, inside the safe box and CapCut's own
    line width, so CapCut never wraps it either."""
    mat = _T[role]["mat"]
    _, font = render._font((json.loads(mat["content"])["styles"][0].get("font") or {}).get("path"))
    k = render.CAPCUT_PX * min(W, H) / 1080
    max_w = W * (min(render.safe_box(style)["w"], float(mat.get("line_max_width") or 1)) - abs(x)) - 24
    segs = [_text(draft, role, piece, pa, pb, y, int(px / k * 100) / 100, fill, stroke)
            for piece, pa, pb, px in render.one_line(words, font, size * k, max_w, a, b, one=True,
                fixed=render.pair_look(style, W, H)["fixed_size"])]
    for seg in segs:  # off the centre line and tilted (the second person's text)
        seg["clip"]["transform"]["x"] = x
        seg["clip"]["rotation"] = rot
    return segs


def _track(draft: dict, kind: str, segs: list[dict], render_index: int) -> None:
    if not segs:
        return
    for s in segs:
        s["render_index"] = render_index
    draft["tracks"].append({"type": kind, "attribute": 0, "flag": 0, "is_default_name": True, "id": _id(),
                            "segments": sorted(segs, key=lambda s: s["target_timerange"]["start"])})


def _text_tracks(draft, segs, render_index):
    lanes = []
    for segment in sorted(segs, key=lambda s:s["target_timerange"]["start"]):
        start = segment["target_timerange"]["start"]
        lane = next((row for row in lanes if row[-1]["target_timerange"]["start"] + row[-1]["target_timerange"]["duration"] <= start), None)
        if lane is None:
            lane = []
            lanes.append(lane)
        lane.append(segment)
    for lane in lanes:
        _track(draft,"text",lane,render_index)


_T: dict[str, Any] = {}
_BUILD_LOCK = threading.Lock()  # _T is shared by the native text writers.


def build(path: str) -> tuple[Path, list[str]]:
    """Returns the new project folder and what this machine lacks (shown to the person, never hidden)."""
    with _BUILD_LOCK, project_lock.writer(path):
        if host_platform.capcut_running():
            raise VideoEditError("Close CapCut before building a native project")
        src = Path(path).resolve()
        capcut_edit._load(src)  # A changed source seal must not be lost when copying sidecars are excluded.
        import web_ai_identity
        check_export = web_ai_identity.export_check(src)
        final = src.with_name(src.name + " · CapCut " + uuid.uuid4().hex)
        with tempfile.TemporaryDirectory(prefix=".capcut-build-", dir=src.parent) as staging:
            dst = Path(staging) / "draft"
            warnings = _build(str(src), dst, final)
            draft_quality.check(str(dst))
            audio_files = {}
            for material in capcut_edit._load(dst)["materials"].get("audios", []):
                file = Path(material["path"])
                file = file.resolve() if file.is_absolute() else (dst / file).resolve()
                published = final / file.relative_to(dst) if file.is_relative_to(dst) else file
                audio_files[str(published.resolve())] = file
            check_export.bind_media(audio_files)
            if host_platform.capcut_running():
                raise VideoEditError("Close CapCut before publishing the native project")
            if final.exists() or final.is_symlink():
                raise VideoEditError(f"native project already exists: {final}")
            import delivery_manifest
            delivery_manifest.prepare(path, dst, final, "capcut", check_export(),
                options={"backend": "capcut", "warnings": warnings}, checks={"structure": "passed", "native_playback": "pending"})
            check_export()
            dst.rename(final)
        return final, warnings


def _build(path: str, dst: Path, final: Path) -> list[str]:
    if not TEMPLATES.is_file():
        raise VideoEditError("app/capcut_templates.json is missing - run scripts/capcut/make_templates.py")
    t = render.render_draft(path, "", dry=True, final=True)
    render.motion_plan.verify_timeline(path, t)
    probe = json.loads(_machine_paths(TEMPLATES.read_text(encoding="utf-8")))
    warnings: list[str] = []
    fonts, bodies = [], []
    for role in ("white", "orange", "caption", "title"):
        mat = probe[role]["mat"]
        body = json.loads(mat["content"])
        bodies.append((mat, body))
        fonts.append((mat, "font_path"))
        fonts.extend((style.setdefault("font", {}), "path") for style in body.get("styles", []))
    paths = [obj.get(key, "") for obj, key in fonts]
    if any(not isinstance(path, str) for path in paths):
        raise VideoEditError("template font path must be a string")
    missing = {path for path in paths if not path or not Path(path).is_file()}
    if missing:
        if not render.DEFAULT_FONT.is_file():
            raise VideoEditError(f"font file missing: {render.DEFAULT_FONT}")
        kanit = render.DEFAULT_FONT.as_posix()
        for font_path in sorted(missing):
            name = Path(font_path).name if font_path else "(empty path)"
            warnings.append(f"font {name} not on this machine: using Kanit (change it in CapCut if you like)")
        # Mutate only font fields: nested JSON escaping and ordinary text stay intact.
        for obj, key in fonts:
            if obj.get(key, "") in missing:
                obj[key] = kanit
        for mat, body in bodies:
            mat["content"] = json.dumps(body, ensure_ascii=False)
    for mat, body in bodies:
        preset_font = render.pair_look(render._json(Path(path) / "clipkit_style.json", {})).get("font")
        if preset_font:
            _, font_path = render._font(preset_font)
            for font_style in body.get("styles", []):
                font_style.setdefault("font", {})["path"] = str(font_path)
            mat["font_path"] = str(font_path)
            mat["content"] = json.dumps(body, ensure_ascii=False)
        family, _ = render._font(body["styles"][0]["font"]["path"])
        mat.update(font_name=family, font_title=family)
    _T.clear()
    _T.update(probe)
    t["broll"] = render.insert_playback(path, t["broll"], materialize=True)
    st = t["style"] or {}
    src = Path(path)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("clipkit*", ".clipkit-web-ai", "*.bak*", "draft_content.before_*"))
    meta_f = dst / "draft_meta_info.json"
    if meta_f.is_file():
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
        for key in ("draft_json_file", "draft_cover"):
            value = meta.get(key)
            if isinstance(value, str) and Path(value).is_absolute():
                try:
                    relative = Path(value).resolve().relative_to(src.resolve())
                except ValueError:
                    continue
                meta[key] = (final / relative).as_posix()
        meta.update(draft_name=final.name, draft_fold_path=final.as_posix(),
                    draft_root_path=final.parent.as_posix(), draft_id=_id())
        atomic.write_text(meta_f, json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    capcut_edit.fresh_ids(dst)  # its own timeline id: never the same as the project it was copied from
    draft = capcut_edit._load(dst)
    draft["tracks"] = [x for x in draft["tracks"] if x["type"] != "text"]

    # footage: zoom cuts as 108 % clips, skin effect on every clip
    main = capcut_edit._video_track(draft)
    skin = float(st.get("skin", 0))
    skin_id = None
    if skin > 0 and not Path(_T["skin"].get("path", "")).exists():
        warnings.append("CapCut skin effect not downloaded on this machine: add skin smoothing once by hand in CapCut")
        skin = 0
    if skin > 0:
        fx = copy.deepcopy(_T["skin"])
        fx["id"] = skin_id = _id()
        for f in fx.get("face_adjust_params", []):
            for p in f["adjust_params"]:
                if p["name"] == "face_adjust_skin_Intensity":
                    p["value"] = skin
        draft["materials"].setdefault("effects", []).append(fx)
    for seg in main["segments"]:
        a = seg["target_timerange"]["start"] / US
        if any(z0 - 0.01 <= a < z1 for z0, z1 in t["zoom"]):
            sc = seg["clip"]["scale"]
            seg["clip"]["scale"] = {"x": sc["x"] * render.ZOOM, "y": sc["y"] * render.ZOOM}
        if skin_id:
            seg["extra_material_refs"].append(skin_id)

    # subtitles: the same choices as the MP4 (render._shown), each role on its own track like a hand-made project
    W, H = t["width"], t["height"]
    look = t["look"]
    P = render.pair_look(st, W, H) if look == "pair" else None
    whites, colours, captions, titles, seconds = [], [], [], [], []
    if look == "pair" or "plain_events" in t:
        roles = {"normal": ("white", whites), "emphasis": ("orange", colours),
                 "caption": ("caption", captions), "second": ("white", seconds)}
        for event in render.pair_events_from_timeline(t):
            role, target = roles[event["role"]]
            target.append(_event_text(draft, role, event, W, H))
    else:
        for phrase in t["phrases"]:
            if not t["hook"] or phrase["start"] >= render.HOOK_DUR:
                for a, b in render.visible_spans(phrase["start"], phrase["end"], t.get("motions", []),
                                                 role="normal", cue_id=phrase.get("cue_id")):
                    whites.append(_text(draft, "white", phrase["lead"], a, b, -.6, 16))
    for h in t["hook"]:
        y = h["y"] + {"up": 0.04, "down": -0.05}.get(h["grow"], 0)  # CapCut places a text by its centre
        for a, b in render.visible_spans(h["start"], h["end"], t.get("motions", []), role="title"):
            titles.append(_text(draft, "title", h["text"], a, b, y, h["size"], h["fill"], h["stroke"]))
    _text_tracks(draft, whites, 14001)
    _text_tracks(draft, colours, 14002)
    _text_tracks(draft, captions, 14003)
    _text_tracks(draft, titles, 14004)
    _text_tracks(draft, seconds, 14005)

    # b-roll on an overlay track, filling the frame
    rolls = []
    for it in t["broll"]:
        mat = copy.deepcopy(_T["broll"]["mat"])
        still = it["kind"] == "image"
        # CapCut keeps a picture as type "photo" with a 3-hour length; as a "video" it would last one frame
        mat.update(path=Path(it["play"]).as_posix(), material_name=Path(it["play"]).name, name=Path(it["play"]).name,
                   duration=_us(10800) if still else _us(it["dur"] if it.get("derived_recipe") else it["source_duration"]),
                   width=it["source_width"], height=it["source_height"],
                   type="photo" if still else "video")
        seg = _clone(draft, _T["broll"], mat, "videos")
        seg["source_timerange"] = {"start": _us(it["media_start"]), "duration": _us(it["dur"])}
        seg["target_timerange"] = {"start": _us(it["start"]), "duration": _us(it["dur"])}
        placement = it["clip"]
        seg["clip"]["scale"] = {"x": placement["scale_x"], "y": placement["scale_y"]}
        seg["clip"]["transform"] = {"x": placement["x"], "y": placement["y"]}
        seg["volume"] = 0.0
        rolls.append(seg)
    _track(draft, "video", rolls, 1)

    for motion in t.get("motions", []):
        info = video_edit.probe(motion["file"])
        mat = copy.deepcopy(_T["broll"]["mat"])
        mat.update(path=Path(motion["file"]).as_posix(), material_name=Path(motion["file"]).name,
                   name=Path(motion["file"]).name, duration=_us(info["duration"]), width=info["width"], height=info["height"], type="video")
        seg = _clone(draft, _T["broll"], mat, "videos")
        seg["source_timerange"] = {"start": 0, "duration": _us(motion["duration"])}
        seg["target_timerange"] = {"start": _us(motion["start"]), "duration": _us(motion["duration"])}
        seg["clip"]["scale"] = {"x": 1.0, "y": 1.0}
        seg["clip"]["transform"] = {"x": 0.0, "y": 0.0}
        seg["volume"] = 0.0
        _track(draft, "video", [seg], 2)

    # a click on every coloured word (one short click, ~30 % volume)
    fx = t["sfx"]
    clicks = []
    if fx.get("on"):
        own = render._own_sfx("pop")  # the pop in this machine's sfx folder wins over CapCut's click
        click_file = str(own) if own else _T["click"]["mat"]["path"]
        if not Path(click_file).is_file():  # this machine's CapCut never downloaded that click: use ours
            click_file = str(render._sfx("pop"))
        own_len = _us(render.sfx_len("pop")) if own else 0
        for tp in t["pops"]:
            mat = copy.deepcopy(_T["click"]["mat"])
            mat["path"] = Path(click_file).as_posix()
            if own:
                mat["duration"] = own_len
            seg = _clone(draft, _T["click"], mat, "audios")
            if own:
                seg["source_timerange"] = {"start": 0, "duration": own_len}
            seg["target_timerange"] = {"start": _us(tp), "duration": seg["source_timerange"]["duration"]}
            seg["volume"] = 0.3 * float(fx.get("volume", 1))
            clicks.append(seg)
        # clicks closer than their own length would overlap on one track
        keep, last = [], -1
        for s in sorted(clicks, key=lambda s: s["target_timerange"]["start"]):
            if s["target_timerange"]["start"] >= last:
                keep.append(s)
                last = s["target_timerange"]["start"] + s["target_timerange"]["duration"]
        clicks = keep
    _track(draft, "audio", clicks, 0)
    video_edit._drop_unused_materials(draft)
    capcut_edit._save(dst, draft, "capcut_build")

    mu = t["music"]
    if mu.get("file"):
        capcut_edit.add_music(str(dst), mu["file"], float(mu.get("volume", 0.12)))
    # the AI version as handed over, so editdata.py can compare it with what the person approves
    # (approved.json: "ai_version": "clipkit_ai_version.json")
    shutil.copyfile(dst / "draft_content.json", dst / "clipkit_ai_version.json")
    return warnings
