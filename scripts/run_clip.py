"""One command from a raw video to finished short clips, without the ClipKit window.

    python scripts/run_clip.py "<video>"                 # pilot: story 1 only
    python scripts/run_clip.py "<video>" --all           # every story
    python scripts/run_clip.py "<video>" --insert        # verify the accepted insert plan
    python scripts/run_clip.py "<video>" --all --export  # MP4 for every story
    python scripts/run_clip.py "<video>" --all --no-ai --capcut  # no AI step: straight to CapCut projects
    python scripts/run_clip.py "<video>" --all --insert-sheet "<folder>"  # browser/local AI packet + contact sheets
    python scripts/run_clip.py "<video>" --all --insert-from "<folder>" --insert-plan "<answer.json>"

Steps: transcribe -> select stories (approximate --max seconds, or --natural) -> one project per story (crop on the face,
subtitles, silences trimmed, subtitle look, zoom cut) -> punch words and b-roll searches -> free Pixabay
b-roll -> cover -> HyperFrames page -> "<video> - ClipKit.html" with Pixabay search links -> MP4 on --export.

Two steps need judgement and are done by the AI agent (skill make-clip): splitting stories and picking punch
words. When one is missing the script prints `WAIT` with the command to run and exits 2; the agent does it
and runs the same command again. Only current bound selections and matching project manifests are reused.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote

def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="One command from a raw video to finished short clips.")
    ap.add_argument("video")
    ap.add_argument("--trial-id", help="benchmark session ID; reuse it for WAIT/retry invocations of the same trial")
    duration = ap.add_mutually_exclusive_group()
    duration.add_argument("--max", type=int, default=None, help="approximate target seconds; shorter or slightly longer for a complete thought; omitted keeps saved target")
    duration.add_argument("--natural", action="store_true", help="reset saved target to natural length; no forced duration split")
    ap.add_argument("--all", action="store_true", help="every story (default: story 1 only, as a pilot)")
    ap.add_argument("--shape", default="portrait", choices=["portrait", "landscape", "square", "source"])
    ap.add_argument("--look", default="pair", choices=["pair", "karaoke", "pop", "none"])
    mine = (Path(__file__).resolve().parents[1] / "presets" / "my-style.json").is_file()  # ClipKit - Autosave's
    ap.add_argument("--preset", default="my-style" if mine else "default",
                    help="subtitle preset in presets/ (normal + emphasis text); default: my-style when Autosave made one")
    ap.add_argument("--insert", action="store_true", help="verify and reuse the accepted insert placements; never infer matches from filenames")
    ap.add_argument("--export", action="store_true", help="render the MP4s")
    ap.add_argument("--capcut", action="store_true", help="also lay the finished clip out as a CapCut project to keep editing")
    ap.add_argument("--insert-from", metavar="FOLDER",
                    help="import --insert-plan for these assets; without a plan export the current packet/contact sheets and WAIT")
    ap.add_argument("--insert-sheet", metavar="FOLDER",
                    help="prompt + contact sheets of videos/images in FOLDER; match registered asset IDs to actual cue/word anchors")
    ap.add_argument("--insert-plan", metavar="ANSWER", help="with --insert-from: the web AI's JSON answer to --insert-sheet")
    ap.add_argument("--no-ai", action="store_true",
                    help="no AI agent: pause-based ranges for a target, whole footage for natural length (semantic selection unreviewed); "
                         "each project gets a file for a web AI to pick emphasis + title "
                         "(scripts/web_ai.py import clip, then run again); until then keep plain subtitles without invented emphasis")
    ap.add_argument("--client", help="with --capcut: check the CapCut project against this client's pace (presets/pace/<client>.json)")
    return ap


if __name__ == "__main__" and {"-h", "--help"} & set(sys.argv):
    _parser().parse_args()  # help works before config.json exists


def _self_update() -> None:
    """Team machines (a clone of ClipKit-Team) take the newest version before every run, so nobody has to type
    clipkit update. Offline, or local edits in the way: say so and run the version already here."""
    import os
    import subprocess
    root = Path(__file__).resolve().parents[1]
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, timeout=60)  # noqa: E731
    try:
        if "ClipKit-Team" not in git("remote", "get-url", "origin").stdout or os.environ.get("CLIPKIT_UPDATED"):
            return
        r = subprocess.run([sys.executable, str(root / "scripts" / "update.py")], capture_output=True, text=True)
        if r.returncode != 0:
            print(json.dumps({"update": "skipped: " + (r.stderr or r.stdout).strip()[-300:]}), flush=True)
            return
        if r.stdout.startswith("already the newest"):
            return
        print('{"update": "updated to the newest ClipKit - restarting"}', flush=True)
        os.environ["CLIPKIT_UPDATED"] = "1"
        sys.exit(subprocess.run([sys.executable, *sys.argv]).returncode)
    except (OSError, subprocess.SubprocessError) as exc:
        print('{"update": "skipped: ' + str(exc).replace('"', "'") + '"}', flush=True)


def _app_icon() -> None:
    """Team machines get the ClipKit desktop icon and the 12-hour update check on their first run."""
    if sys.platform != "win32":
        return
    import subprocess
    icon = Path(__file__).with_name("app_icon.ps1")
    desk = Path.home() / "Desktop" / "ClipKit.lnk"
    task = subprocess.run(["schtasks", "/Query", "/TN", "ClipKit update"], capture_output=True).returncode == 0
    if icon.is_file() and not (desk.exists() and task):
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(icon)],
                       capture_output=True, timeout=60)


if __name__ == "__main__":
    _self_update()
    _app_icon()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import capcut_edit  # noqa: E402
import atomic  # noqa: E402
import hf_build  # noqa: E402
import kit_settings  # noqa: E402
import render  # noqa: E402
import project_lock  # noqa: E402
import story_selection  # noqa: E402
import cutlog  # noqa: E402  (scripts/cutlog.py: the cut log)
import video_edit  # noqa: E402
import video_editor as ve  # noqa: E402
from fastapi import HTTPException  # noqa: E402

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
sys.stdout.reconfigure(encoding="utf-8")  # Thai titles when the output goes to a file or another program


def say(**kw) -> None:
    print(json.dumps(kw, ensure_ascii=False), flush=True)


TIMING = None  # active benchmark_timing.TimingLog, scoped to one invocation of main()


def timed(name: str, fn, *args, **kw):
    """Print step time and record it under the current trial and invocation."""
    t = time.perf_counter()
    try:
        return TIMING.timed(name, fn, *args, **kw) if TIMING else fn(*args, **kw)
    finally:
        say(took=name, s=round(time.perf_counter() - t, 1))


def detail(exc: Exception) -> str:
    return exc.detail if isinstance(exc, HTTPException) else str(exc)


@project_lock.edit
def stories(raw: str, max_s: int | None, no_ai: bool = False, *, natural: bool = False) -> list[dict] | None:
    import web_ai_json
    import web_ai_transaction
    web_ai_transaction.recover("stories", raw)
    if natural or max_s is not None:
        web_ai_json.save_story_target(raw, None if natural else max_s / 60)
    target = web_ai_json.story_target(raw)
    if not video_edit.transcript_file(raw).is_file():
        say(step="ถอดเสียงทั้งไฟล์")
    phrases = timed("ถอดเสียง", video_edit.transcribe_file, raw)
    _, selected = timed("อ่านผลเลือกเรื่อง", _selection_snapshot, raw, phrases)
    if selected is None and no_ai and phrases:
        # This is an explicit mechanical fallback, not a claim that the footage is one complete topic.
        if target is None:
            parts = [{"title": Path(raw).stem, "summary": "ทั้งฟุตต้นฉบับ · ยังไม่ได้คัดเรื่องตามความหมาย",
                      "start": phrases[0]["start"], "end": phrases[-1]["end"]}]
            say(fallback="whole footage; semantic story selection has not been reviewed")
        else:
            parts = timed("แบ่งช่วงจากช่วงพัก", video_edit._split_long, 0, len(phrases) - 1, phrases, Path(raw).stem,
                          "แบ่งจากช่วงพัก · ยังไม่ได้ตรวจความครบประเด็น", max_s=target * 60 * 1.25, aim_s=target * 60)
        import web_ai_identity
        # Issue only for a new requested edit; a resume/cache lookup never changes an active AI packet.
        web_ai_json.import_stories(raw, {"clipkit_web_ai": 2, **web_ai_identity.issue("stories", raw),
                                        "step": "stories", "video": Path(raw).name, "stories": parts})
        _, selected = _selection_snapshot(raw, phrases)
    return selected


def _selection_snapshot(raw: str, phrases: list[dict]) -> tuple[str, list[dict] | None]:
    revision = story_selection.source_revision(raw, include_stories=True)
    selected = story_selection.read_selection(raw, phrases)
    if story_selection.source_revision(raw, include_stories=True) != revision:
        raise video_edit.VideoEditError("source or selection changed while reading stories; run again")
    return revision, selected


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(path: str) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _resume_context(raw: str, *, shape: str, look: str, preset: str, selected: list[dict] | None = None) -> dict:
    chosen = render.pair_look({"preset": preset})
    font = Path(chosen["font"]) if chosen["font"] else render.DEFAULT_FONT
    try:
        phrases = json.loads(video_edit.transcript_file(raw).read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise video_edit.VideoEditError("source transcript is unreadable; restore or transcribe it before resuming") from exc
    revision, current = _selection_snapshot(raw, phrases)
    if current is None or selected is not None and current != selected:
        raise video_edit.VideoEditError("story selection changed or is unbound; import a current answer before resuming")
    return {"version": 1, "raw": _canonical(raw),
            "source_revision": revision,
            "options": {"shape": shape, "look": look, "preset": preset},
            "preset_sha256": _file_digest(render.PRESETS / f"{preset}.json"),
            "font_sha256": _file_digest(font), "default_font_sha256": _file_digest(render.DEFAULT_FONT),
            "skin": render.SKIN_DEFAULT}


def _manifest(context: dict, story: dict) -> dict:
    return copy.deepcopy({"context": context, "story": story})


def _load_resume(file: Path) -> dict:
    try:
        state = json.loads(file.read_text(encoding="utf-8-sig")) if file.is_file() else {}
    except (ValueError, OSError) as exc:
        raise video_edit.VideoEditError("CLI resume state is unreadable; retain it and restore a saved copy") from exc
    if not isinstance(state, dict):
        raise video_edit.VideoEditError("CLI resume state must be an object")
    if state.get("version") == 2:
        if not isinstance(state.get("projects"), dict):
            raise video_edit.VideoEditError("CLI resume projects must be an object")
        return state
    if any(not isinstance(path, str) for path in state.values()):
        raise video_edit.VideoEditError("unrecognized CLI resume state; retain it for recovery")
    return {"version": 2, "projects": {}, "legacy": state}


def _assert_current(context: dict) -> None:
    if _resume_context(context["raw"], **context["options"]) != context:
        raise video_edit.VideoEditError("source, story selection or style changed during this run; run again")


def _resume_project(state: dict, manifest: dict) -> str | None:
    context = manifest["context"]
    _assert_current(context)
    record = state["projects"].get(_digest(manifest))
    if record is None:
        return None
    if not isinstance(record, dict) or record.get("manifest") != manifest or not isinstance(record.get("path"), str):
        raise video_edit.VideoEditError("CLI project manifest is invalid; retain the state for recovery")
    path = record["path"]
    if not Path(path).exists():
        return None
    try:
        with project_lock.writer(path):
            draft = capcut_edit.read_draft(path)  # includes the existing outside-edit seal check
            meta = json.loads((Path(path) / "clipkit.json").read_text(encoding="utf-8"))
            style = json.loads((Path(path) / "clipkit_style.json").read_text(encoding="utf-8"))
            content = json.loads((Path(path) / "draft_content.json").read_text(encoding="utf-8"))
        story, options = manifest["story"], context["options"]
        canvas = content["canvas_config"]
        wanted = video_edit.CANVAS.get(options["shape"])
        if (not isinstance(meta, dict) or not isinstance(style, dict)
                or _canonical(meta["raw"]) != context["raw"] or _canonical(draft["source"]) != context["raw"]
                or meta.get("start") != story["start"] or meta.get("end") != story["end"]
                or style.get("anim") != options["look"] or style.get("preset") != options["preset"]
                or wanted and (canvas["width"], canvas["height"]) != wanted):
            raise video_edit.VideoEditError("CLI project no longer matches its source/story/style; retain it and rebuild")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise video_edit.VideoEditError("CLI project is incomplete or unreadable; retain it and rebuild") from exc
    return path


def _save_project(file: Path, state: dict, manifest: dict, path: str) -> None:
    _assert_current(manifest["context"])
    candidate = {"manifest": manifest, "path": path}
    updated = {**state, "projects": {**state["projects"], _digest(manifest): candidate}}
    if _resume_project(updated, manifest) is None:
        raise video_edit.VideoEditError("new CLI project is missing; resume state was not changed")
    atomic.write_text(file, json.dumps(updated, ensure_ascii=False, indent=1, allow_nan=False))
    state.update(updated)


def make_project(raw: str, n: int, st: dict, shape: str, look: str, preset: str = "default") -> str:
    f = timed("หากรอบคนพูด", video_edit.find_focus, raw, st["start"], st["end"])
    r = timed("สร้างร่าง", ve.capcut, ve.DraftRequest(file=raw, name=f"{Path(raw).stem} - {n} {st['title']}", start=st["start"], end=st["end"],
                                  via="clipkit", shape=shape, focus_x=f["x"], focus_y=f["y"]))
    path = r["draft_path"]
    with project_lock.writer(path):
        got = timed("ซับในร่าง", capcut_edit.transcribe_draft, path)
        timed("วางซับ", capcut_edit.set_subtitles, path, got["subtitles"])
        timed("ภาษาซับ", capcut_edit.subtitles_language, path, "th")  # spoken lines as heard, any language
        timed("ตัดช่วงพัก", capcut_edit.trim_pauses, path)
        timed("จัดบรรทัดซับ", capcut_edit.tidy_subtitles, path)
        (Path(path) / "clipkit_style.json").write_text(json.dumps({"anim": look, "preset": preset, "zoomcut": True, "skin": render.SKIN_DEFAULT}), encoding="utf-8")
        timed("ปรับสี", ve.auto_color, path)
    return path


def queries(path: str) -> list[str]:
    p = Path(path) / "clipkit_punch.json"
    if not p.is_file():  # --no-ai: no b-roll searches were picked
        return []
    seen = []
    for pairs in json.loads(p.read_text(encoding="utf-8")).values():
        for x in pairs:
            if len(x) > 2 and isinstance(x[2], str) and x[2] and x[2] not in seen:
                seen.append(x[2])
    return seen


def insert(path: str) -> int:
    """Reuse explicit, validated placements without matching names or changing music/SFX."""
    import insert_plan
    with project_lock.writer(path):
        placed = Path(path) / "clipkit_placed.json"
        if not placed.is_file():
            raise video_edit.VideoEditError("no accepted insert plan; export --insert-sheet <folder> and import --insert-plan <answer>")
        data = json.loads(placed.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("broll"), list):
            raise video_edit.VideoEditError("invalid insert placements; export a fresh --insert-sheet <folder>")
        return len(insert_plan.normalize(path, data["broll"]))


def _insert_projects(sts: list[dict], picked, state: dict, context: dict) -> list[str]:
    projects = []
    for n in picked:
        path = _resume_project(state, _manifest(context, sts[n - 1]))
        if path is None:
            raise video_edit.VideoEditError("no current project for these story/style options; run without insert flags first")
        if path in projects:
            raise video_edit.VideoEditError("insert batch contains the same project twice")
        projects.append(path)
    _assert_current(context)
    return projects


def _links(sts: list[dict], picked, state: dict, context: dict) -> list[tuple[str, int, str]]:
    """(project, its own link number, search) for every stock search link on the page, in page order."""
    out = []
    for path in _insert_projects(sts, picked, state, context):
        out += [(path, i, q) for i, q in enumerate(queries(path), 1)]
    return out


def _downloads(folder: str) -> list[Path]:
    import insert_plan
    src = Path(folder)
    if not src.is_dir():
        raise video_edit.VideoEditError(f"download folder not found: {folder}")
    return sorted((f for f in src.iterdir() if f.is_file() and f.suffix.lower() in insert_plan.EXTENSIONS),
                  key=lambda f: f.name.casefold())


def sheet(raw: str, sts: list[dict], picked, state: dict, folder: str, *, context: dict) -> tuple[Path, list[Path]]:
    """One shared catalog/planner per project, wrapped for browser/local AI batch replies."""
    import web_ai_json
    projects, files = _insert_projects(sts, picked, state, context), _downloads(folder)
    if not projects or not files:
        raise video_edit.VideoEditError("nothing to match: select current projects and a folder containing videos or images")
    _assert_current(context)
    base = Path(raw).with_name(Path(raw).stem + " - inserts")
    envelopes, pictures = [], []
    for n, path in enumerate(projects, 1):
        _assert_current(context)
        packet, picture = web_ai_json.export_inserts(path, files=files)
        blocks = re.findall(r"```json\s*(.*?)```", packet.read_text(encoding="utf-8"), re.S)
        if not blocks:
            raise video_edit.VideoEditError("insert export did not include its bound JSON data")
        data = web_ai_json.parse(blocks[-1])
        picture_copy = Path(str(base) + f"-{n}.jpg")
        shutil.copy2(picture, picture_copy)
        envelopes.append({**data, "contact_sheet": picture_copy.name})
        pictures.append(picture_copy)
    _assert_current(context)
    instruction = ("\n\n## CLI batch\nตอบ JSON ก้อนเดียวที่มี clipkit_web_ai: 2, step: clip, operation: inserts "
                   "และ projects เป็นรายการคำตอบแยกทุกโปรเจกต์ตามข้อมูลด้านล่าง "
                   "แต่ละคำตอบคง project, packet_id, revision, source_id และ operation: inserts ของตัวเอง "
                   "พร้อม place ตาม schema เดิม ถ้าไม่ควรแทรกให้ place: [] ห้ามละโปรเจกต์หรือจับคู่ด้วยชื่อไฟล์/ลำดับโหลด "
                   "แต่ละโปรเจกต์นำเข้าแยกกัน มี receipt ของตัวเอง ไม่ใช่ transaction เดียวทั้ง batch\n")
    md = web_ai_json._packet(web_ai_json._prompt("inserts.md") + instruction,
        {"clipkit_web_ai": 2, "step": "clip", "operation": "inserts", "projects": envelopes}, Path(str(base) + ".md"))
    atomic.write_text(md, md.read_text(encoding="utf-8").replace("แล้ว EditorKrab จะนำเข้าเอง",
        "แล้วรัน CLI เดิมเพิ่ม --insert-plan ตามชื่อไฟล์คำตอบนี้"), encoding="utf-8")
    return md, pictures


def place(raw: str, sts: list[dict], picked, state: dict, folder: str, plan: str | None = None, *, context: dict) -> list[dict]:
    """Import individually bound v2 clip insert answers; original assets are never moved."""
    import web_ai_identity
    import web_ai_json
    import web_ai_transaction
    projects = _insert_projects(sts, picked, state, context)
    if not plan:
        _downloads(folder)
        _assert_current(context)
        raise video_edit.VideoEditError("no explicit insert plan; export --insert-sheet <folder>, then --insert-plan <answer>")
    answer = web_ai_json.parse(Path(plan).read_text(encoding="utf-8-sig"))
    if (type(answer.get("clipkit_web_ai")) is not int or answer["clipkit_web_ai"] != 2
            or answer.get("step") != "clip" or answer.get("operation") != "inserts"):
        raise video_edit.VideoEditError("unbound/legacy insert plan; export a fresh --insert-sheet <folder> for v2 clip inserts")
    answers = answer.get("projects", [answer])
    if not isinstance(answers, list) or not answers or any(not isinstance(item, dict) for item in answers):
        raise video_edit.VideoEditError("projects must contain the individually bound insert answers")
    ready, used = [], set()
    for item in answers:
        packet = item.get("packet_id")
        if not isinstance(packet, str) or not re.fullmatch(r"[0-9a-f]{32}", packet) or item.get("operation") != "inserts":
            raise video_edit.VideoEditError("insert answer is missing its original packet identity/operation; export again")
        targets = [path for path in projects if (web_ai_identity.state_dir("clip", path) / "packets" / (packet + ".json")).is_file()]
        if len(targets) != 1 or targets[0] in used:
            raise video_edit.VideoEditError("insert answer has an unknown or duplicate project packet")
        path = targets[0]
        web_ai_json._head(item, "clip", "project", Path(path).name)
        with project_lock.writer(path):
            existing = web_ai_transaction.receipt("clip", path, item)
            if existing is None:
                web_ai_json.preview_inserts(path, json.dumps(item, ensure_ascii=False))
        ready.append((path, item))
        used.add(path)
    if used != set(projects):
        raise video_edit.VideoEditError("insert batch must include every selected project; use place: [] when no insert is appropriate")
    _assert_current(context)
    applied = []
    for path, item in ready:
        try:
            _assert_current(context)
            result = web_ai_json.import_clip(path, item)
            applied.append({"project": Path(path).name, "packet_id": item["packet_id"], "result": result})
        except (video_edit.VideoEditError, HTTPException, OSError) as exc:
            raise video_edit.VideoEditError(f"insert batch stopped; completed receipts: {json.dumps(applied, ensure_ascii=False)}; retry the same plan: {detail(exc)}") from exc
    return applied


def page(raw: str, rows: list[dict]) -> Path:
    out = Path(raw).with_name(Path(raw).stem + " - ClipKit.html")
    parts, k = [], 0
    for r in rows:
        links = "".join(
            f'<li><a href="https://pixabay.com/videos/search/{quote(q)}/" target="_blank">{k + i}. {html.escape(q)}</a>'
            f'{" · มีแล้ว (Pixabay)" if q in r["have"] else ""}</li>' for i, q in enumerate(r["queries"], 1))
        k += len(r["queries"])
        parts.append(f"""<section><h2>{r['n']}. {html.escape(r['title'])} <small>{r['length']} วิ</small></h2>
<p><a href="{Path(r['hf']).as_uri()}">▶ ดูคลิป (HyperFrames)</a>{f' · <a href="{Path(r["mp4"]).as_uri()}">MP4</a>' if r.get('mp4') else ''}</p>
<p>โหลดคลิปทีละลิงก์ตามลำดับเลข ลงโฟลเดอร์ว่างโฟลเดอร์เดียว (ข้ามลิงก์ไหน ให้ตั้งชื่อไฟล์ขึ้นต้นด้วยเลขลิงก์ เช่น <code>7 coffee.mp4</code>)
แล้วสั่งคำสั่งเดิมเติม <code>--insert-from "&lt;โฟลเดอร์นั้น&gt;"</code></p><ol style="list-style:none;padding:0">{links}</ol></section>""")
    out.write_text(f"""<!doctype html><html lang="th"><head><meta charset="utf-8"><title>{html.escape(Path(raw).stem)} · ClipKit</title>
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;background:#111;color:#eee}}
a{{color:#8fb8ff}}section{{border:1px solid #333;border-radius:10px;padding:12px 16px;margin:12px 0}}small{{color:#999}}code{{color:#fc6}}</style>
</head><body><h1>{html.escape(Path(raw).name)}</h1>{''.join(parts)}</body></html>""", encoding="utf-8")
    return out


def main() -> int:
    import benchmark_timing
    a = _parser().parse_args()
    raw = str(Path(a.video).resolve())
    if not Path(raw).is_file():
        say(error="file not found", file=raw)
        return 1
    global TIMING
    previous = TIMING
    TIMING = benchmark_timing.TimingLog(cutlog.logs_dir() / "timings.jsonl", raw, a.trial_id)
    say(trial_id=TIMING.trial_id, run_id=TIMING.run_id)
    try:
        return TIMING.run(_pipeline, a, raw)
    finally:
        TIMING = previous


def _pipeline(a, raw: str) -> int:
    import web_ai_transaction
    with project_lock.writer(raw, recover=False):
        web_ai_transaction.recover("stories", raw)
        return _pipeline_locked(a, raw)


def _pipeline_locked(a, raw: str) -> int:
    render.pair_look({"preset": a.preset})  # unknown or broken preset: stop before any work
    say(preset=a.preset)
    if a.insert_plan and not a.insert_from:
        say(error="--insert-plan requires --insert-from <asset folder>")
        return 1
    state_f = Path(raw).with_name(Path(raw).name + ".clipkit_run.json")
    state = _load_resume(state_f)

    sts = timed("ถอดเสียง + เรื่อง", stories, raw, a.max, a.no_ai, natural=a.natural)
    if sts is None:
        import web_ai_json
        say(WAIT=video_edit.agent_command("stories", raw), web_ai=str(web_ai_json.export_stories(raw)),
            reason="story selection is absent, unbound or stale; import a current answer to continue",
            import_answer=f'python scripts/web_ai.py import stories "{raw}" <answer.json>', then=" ".join(sys.argv))
        return 2
    picked = range(1, len(sts) + 1) if a.all else [1]
    context = _resume_context(raw, shape=a.shape, look=a.look, preset=a.preset, selected=sts)
    if a.insert_sheet or a.insert_from:
        try:
            for n in picked:
                manifest = _manifest(context, sts[n - 1])
                if _resume_project(state, manifest) is None:
                    path = timed("ทำโปรเจกต์", make_project, raw, n, sts[n - 1], a.shape, a.look, a.preset)
                    _save_project(state_f, state, manifest, path)
        except (video_edit.VideoEditError, HTTPException) as exc:
            say(error=detail(exc))
            return 1
    if a.insert_sheet or a.insert_from and not a.insert_plan:
        folder = a.insert_sheet or a.insert_from
        try:
            md, pictures = timed("เตรียมจับคู่ภาพแทรก", sheet, raw, sts, picked, state, folder, context=context)
        except video_edit.VideoEditError as exc:
            say(error=str(exc))
            return 1
        say(WAIT="review the contact sheets and return the explicit asset/cue insert plan",
            upload=[str(md), *(str(picture) for picture in pictures)],
            then=f'save the answer, then run again with --insert-from "{folder}" --insert-plan <answer>')
        return 0 if a.insert_sheet else 2
    if a.insert_from:
        try:
            say(placed=timed("นำเข้าแผนภาพแทรก", place, raw, sts, picked, state, a.insert_from, a.insert_plan, context=context))
        except video_edit.VideoEditError as exc:
            say(error=str(exc))
            return 1
        a.insert = True
    rows, waits = [], []
    for n in picked:
        st = sts[n - 1]
        manifest = _manifest(context, st)
        try:
            path = _resume_project(state, manifest)
            if path is None:
                say(step="ทำโปรเจกต์", story=n, title=st["title"])
                path = timed("ทำโปรเจกต์", make_project, raw, n, st, a.shape, a.look, a.preset)
                _save_project(state_f, state, manifest, path)
            with project_lock.writer(path):
                _resume_project(state, manifest)
                need = [] if a.no_ai else ["clipkit_punch.json", "clipkit_hook.json"]
                style = json.loads((Path(path) / "clipkit_style.json").read_text(encoding="utf-8"))
                if style.get("anim") == "pair" and render.pair_look(style)["caption"] and not a.no_ai:
                    need.append("clipkit_caption.json")  # the preset has a small translated caption
                if not all((Path(path) / f).is_file() for f in need):
                    waits.append(ve.draft_punch_request(ve.DraftPath(path=path))["command"])
                    import web_ai_json
                    say(web_ai=str(web_ai_json.export_clip(path)),
                        import_answer=f'python scripts/web_ai.py import clip "{path}" <answer.json>', story=n)
                    continue
                if a.no_ai and not (Path(path) / "clipkit_punch.json").is_file():
                    import web_ai_json  # emphasis + title from an AI in the browser: upload this file, import its answer, run again
                    say(web_ai=str(web_ai_json.export_clip(path)), story=n)
                if a.insert:
                    say(step="ใส่คลิปที่โหลดมา", story=n, files=timed("ใส่ภาพแทรก", insert, path))
                note = ""
                try:
                    timed("b-roll Pixabay", ve.broll_fill, path)
                except video_edit.VideoEditError as exc:  # no Pixabay key / offline: the search links still cover it
                    note = str(exc)
                if not json.loads((Path(path) / "clipkit_style.json").read_text(encoding="utf-8")).get("cover"):
                    timed("ปก", ve.auto_cover, path)
                row = {"n": n, "title": st["title"], "path": path, "hf": str(timed("หน้า HyperFrames", hf_build.build, path)),
                       "queries": timed("คำค้นภาพประกอบ", queries, path),
                       "length": round(timed("ความยาวคลิป", ve.draft_timeline, path)["duration"], 1), "pixabay": note}
                bf = Path(path) / "clipkit_broll.json"
                row["have"] = list(json.loads(bf.read_text(encoding="utf-8"))) if bf.is_file() else []
                if a.capcut:
                    import capcut_build
                    say(step="ทำโปรเจกต์ CapCut", story=n)
                    cc, row["capcut_warnings"] = timed("โปรเจกต์ CapCut", capcut_build.build, path)
                    row["capcut"] = str(cc)
                    if a.client:
                        sys.path.insert(0, str(ROOT / "scripts" / "capcut"))
                        import pace
                        row["pace"] = timed("ตรวจจังหวะ", pace.check, cc, a.client)
                        say(pace=row["pace"]["score"], problems=row["pace"]["problems"], story=n)
                row["safe_zone"] = timed("ตรวจพื้นที่ปลอดภัย", render.safe_zone, row.get("capcut") or path)
                if row["safe_zone"]:
                    say(safe_zone=row["safe_zone"], story=n)
                if a.export:
                    say(step="ส่งออก MP4", story=n)
                    row["mp4"] = timed("ส่งออก MP4", ve.draft_hf_export, ve.ExportRequest(path=path))["file"]
                rows.append(row)
                import web_ai_json
                log_args = {k: v for k, v in vars(a).items() if k != "video"}
                log_args.update(max=None, max_minutes=web_ai_json.story_target(raw))
                row["check"] = timed("ตรวจคลิป + log", cutlog.log_run, raw, st, n, path, log_args, row["length"])
        except (video_edit.VideoEditError, HTTPException) as exc:
            say(error=detail(exc), story=n, title=st["title"])
            return 1
    if waits:
        say(WAIT=waits, then=" ".join(sys.argv))
        return 2
    say(done=str(timed("หน้าสรุป", page, raw, rows)), clips=[{k: r[k] for k in ("n", "title", "length", "path") if k in r} | {"mp4": r.get("mp4"), "capcut": r.get("capcut"), "capcut_warnings": r.get("capcut_warnings"), "safe_zone": r.get("safe_zone"), "check": r.get("check")}
                                         for r in rows], stories=len(sts), pilot=not a.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
