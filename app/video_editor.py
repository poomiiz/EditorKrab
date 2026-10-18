"""Router: video_editor — scan a folder of footage, transcribe it, find the
pauses worth trimming, and hand the result to CapCut as a ready draft."""
from __future__ import annotations

import atomic
import jobstore
import project_lock
import subprocess
import sys
import json
import cue_data
import re
from threading import RLock, Thread
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

import capcut_edit
import host_platform
import video_edit
from video_edit import VideoEditError


router = APIRouter(prefix="/api/video", tags=["video-editor"])
_transcribe_jobs: dict[str, subprocess.Popen[str]] = {}
_APP_DIR = Path(__file__).resolve().parent


class ScanRequest(BaseModel):
    path: str


class AnalyzeRequest(BaseModel):
    file: str
    start: float = 0.0
    end: float | None = None
    threshold_db: int = Field(default=-33, ge=-60, le=-10)
    min_pause: float = Field(default=0.30, ge=0.1, le=3.0)
    keep: float = Field(default=0.25, ge=0.0, le=2.0)
    min_gain: float = Field(default=0.30, ge=0.05, le=3.0)


class TranscribeRequest(BaseModel):
    file: str
    start: float = 0.0
    end: float | None = None
    language: str | None = None  # None = config.json "speech_language"
    model: str | None = None


class SubtitleStyle(BaseModel):
    size: float | None = None
    y: float | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    stroke: float | None = None


class SubtitleIn(BaseModel):
    id: str | None = None
    words: list[list[Any]] | None = None
    start: float
    end: float
    text: str
    style: SubtitleStyle | None = None


class CutIn(BaseModel):
    start: float
    end: float


class DraftRequest(BaseModel):
    file: str
    name: str
    start: float = 0.0
    end: float | None = None
    cuts: list[CutIn] = Field(default_factory=list)
    subs: list[SubtitleIn] = Field(default_factory=list)
    sub_size: int = Field(default=18, ge=6, le=60)
    sub_y: float = Field(default=-0.60, ge=-1.0, le=1.0)
    sub_color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    sub_stroke: float = Field(default=0.05, ge=0.0, le=0.3)
    via: str = Field(default="capcut", pattern="^(clipkit|capcut)$")
    shape: str = Field(default="source", pattern="^(source|portrait|landscape|square)$")
    focus_x: float = Field(default=0.5, ge=0, le=1)  # where to keep in frame when cropping (0 left, 1 right)
    focus_y: float = Field(default=0.5, ge=0, le=1)  # which button made it: finish in ClipKit or in CapCut
    template: str | None = None  # project template (project_templates/<name>.json) to start from


class BriefRequest(BaseModel):
    path: str


class BrowseRequest(BaseModel):
    path: str | None = None


class DraftPath(BaseModel):
    path: str


class DraftSubsRequest(BaseModel):
    path: str
    subs: list[SubtitleIn]
    size: float | None = None
    y: float | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    stroke: float | None = None
    replace: bool = True


class FillGapsRequest(BaseModel):
    path: str
    min_gap: float = Field(default=1.0, ge=0.3, le=10.0)
    preview: bool = False


class DraftStyleRequest(BaseModel):
    path: str
    size: float | None = None
    y: float | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    stroke: float | None = None
    line_h: float | None = Field(default=None, ge=0.8, le=2.5)


class DraftTrimRequest(BaseModel):
    path: str
    keep: float = Field(default=0.25, ge=0.0, le=2.0)
    min_gain: float = Field(default=0.30, ge=0.05, le=3.0)
    apply: bool = True


class GradeRequest(BaseModel):
    source: str
    target: str


class AnimationRequest(BaseModel):
    path: str
    name: str
    target: str = Field(default="text", pattern="^(text|video)$")
    duration: float | None = Field(default=None, ge=0.1, le=5.0)


class AnimationClearRequest(BaseModel):
    path: str
    target: str = Field(default="text", pattern="^(text|video)$")


class MusicRequest(BaseModel):
    path: str
    music: str
    volume: float = Field(default=0.10, ge=0.0, le=1.0)
    fade_out: float = Field(default=5.0, ge=0.0, le=10.0)
    start: float = Field(default=0.0, ge=0.0)
    fade_in: float | None = Field(default=None, ge=0.0, le=10.0)


class SoundRequest(BaseModel):
    path: str
    sound: str = "sfx_pop"
    volume: float = Field(default=0.35, ge=0.0, le=2.0)
    every_line: bool = True
    offset: float = Field(default=0.0, ge=-1.0, le=1.0)


class TranslateRequest(BaseModel):
    lines: list[str]
    target: str = "en"


class EmphasisRequest(BaseModel):
    lines: list[str]
    ratio: float = Field(default=0.25, ge=0.05, le=0.8)


class TermsRequest(BaseModel):
    lines: list[str]
    per_line: int = Field(default=2, ge=1, le=4)


def _clip_end(file: str, end: float | None) -> float:
    return float(end) if end is not None else video_edit.probe(file)["duration"]


@router.post("/scan")
def scan(req: ScanRequest) -> dict[str, Any]:
    try:
        files = video_edit.scan_folder(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": req.path, "count": len(files), "files": files}


@router.post("/projects/plan")
def project_plan(req: ScanRequest) -> dict[str, Any]:
    try:
        plans = video_edit.suggest_projects(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(plans), "plans": plans}


@router.post("/projects/transcribe")
def start_project_transcription(req: ScanRequest) -> dict[str, Any]:
    folder = Path(req.path).resolve()
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="folder not found")
    key = str(folder).lower()
    active = _transcribe_jobs.get(key)
    if active and active.poll() is None:
        raise HTTPException(status_code=409, detail="bot is already transcribing this project")
    output = folder / "transcripts"
    output.mkdir(parents=True, exist_ok=True)
    log = (output / "bot.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(_APP_DIR / "transcribe_folder.py"), str(folder)],
        cwd=str(_APP_DIR), stdout=log, stderr=subprocess.STDOUT, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _transcribe_jobs[key] = process
    return {"status": "running", "takes": len(video_edit.scan_folder(str(folder)))}


@router.get("/projects/transcribe/status")
def project_transcription_status(path: str = Query(...)) -> dict[str, Any]:
    folder = Path(path).resolve()
    key = str(folder).lower()
    process = _transcribe_jobs.get(key)
    output = folder / "transcripts"
    completed = len([file for file in output.glob("*.json") if file.name != "status.json"]) if output.is_dir() else 0
    return {"status": "running" if process and process.poll() is None else "idle",
            "completed": completed, "takes": len(video_edit.scan_folder(str(folder)))}


@router.get("/projects/transcribe/results")
def project_transcription_results(path: str = Query(...)) -> dict[str, Any]:
    output = Path(path).resolve() / "transcripts"
    results = []
    for file in sorted(output.glob("*.json")) if output.is_dir() else []:
        if file.name == "status.json":
            continue
        data = json.loads(file.read_text(encoding="utf-8"))
        results.append({"file": file.stem, "source": data.get("source"),
                        "duration": data.get("duration"), "review": data.get("review", {}),
                        "markdown": file.with_suffix(".md").read_text(encoding="utf-8") if file.with_suffix(".md").is_file() else ""})
    return {"results": results}


@router.post("/analyze")
def analyze(req: AnalyzeRequest) -> dict[str, Any]:
    try:
        end = _clip_end(req.file, req.end)
        pauses = video_edit.detect_pauses(req.file, req.start, end,
                                          req.threshold_db, req.min_pause)
        cuts = video_edit.suggest_cuts(pauses, req.keep, req.min_gain)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    removed = round(sum(c["gain"] for c in cuts), 2)
    length = round(end - req.start, 2)
    return {
        "length": length,
        "pauses": pauses,
        "cuts": cuts,
        "removed": removed,
        "result_length": round(length - removed, 2),
        "cuts_per_10s": round(10 * len(cuts) / max(length - removed, 0.1), 2),
    }


@router.post("/transcribe")
def transcribe(req: TranscribeRequest) -> dict[str, Any]:
    try:
        end = _clip_end(req.file, req.end)
        phrases = video_edit.transcribe(req.file, req.start, end, req.language, req.model)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # model load / runtime problems must surface, not hide
        raise HTTPException(status_code=500, detail=f"transcribe failed: {exc}") from exc
    return {"count": len(phrases), "phrases": phrases}


class StoryPlanRequest(BaseModel):
    file: str
    brief: str | None = Field(default=None, max_length=2000)  # None: keep the saved one; "": remove it
    max_minutes: float | None = Field(default=None, gt=0, allow_inf_nan=False)


_story_jobs = jobstore.JobStore("story-plan", failed="error")
_story_guard = RLock()


def _story_file(file: str) -> str:
    import web_ai_identity
    return str(web_ai_identity._canonical(file))


def _story_result(file: str, job: dict, phrases: list[dict]) -> None:
    import story_selection
    import web_ai_json
    revision = story_selection.source_revision(file, include_stories=True)
    stories = story_selection.read_selection(file, phrases)
    result = dict(status="done" if stories else "waiting", step="เลือกเรื่อง" if stories else "รอเลือกเรื่อง",
               transcript=str(video_edit.transcript_file(file)), max_minutes=web_ai_json.story_target(file),
               input_revision=story_selection.source_revision(file),
               plan_revision=revision,
               source_rows=story_selection.source_rows(phrases),
               command=video_edit.agent_command("stories", file),
               duration=round(max((p["end"] for p in phrases), default=0), 1),
               speech=round(sum(p["end"] - p["start"] for p in phrases), 1),
               unverified_selection=video_edit.stories_file(file).is_file() and stories is None)
    if stories:
        result["stories"] = stories
    if story_selection.source_revision(file, include_stories=True) != revision:
        raise HTTPException(409, "รายการเรื่องเปลี่ยนระหว่างอ่าน · โหลดข้อมูลล่าสุดอีกครั้ง")
    job.pop("error", None)
    job.pop("stories", None)
    job.update(result)


def _run_story_plan(file: str, job: dict | None = None) -> None:
    """One source writer owns ASR; obsolete workers cannot publish into a replacement job."""
    import web_ai_identity
    import web_ai_transaction
    job = job if job is not None else _story_jobs[file]
    try:
        with project_lock.writer(file, recover=False):
            with _story_guard:
                if _story_jobs.get(file) is not job:
                    return
            web_ai_transaction.recover("stories", file)
            before = web_ai_identity._stat(Path(file))
            phrases = video_edit.transcribe_file(file)
            if web_ai_identity._stat(Path(file)) != before:
                raise VideoEditError("ฟุตเปลี่ยนระหว่างถอดเสียง · เริ่มแบ่งเรื่องใหม่")
            with _story_guard:
                if _story_jobs.get(file) is job:
                    _story_result(file, job, phrases)
                    _story_jobs.save()
    except Exception as exc:
        with _story_guard:
            if _story_jobs.get(file) is job:
                job.update(status="error", error=str(exc))
                _story_jobs.save()


def _new_story_job(file: str, revision: str) -> dict:
    job = {"status": "running", "step": "ถอดเสียง", "input_revision": revision}
    _story_jobs[file] = job
    _story_jobs.save()
    return job


def _story_http(exc: VideoEditError) -> HTTPException:
    import hf_build
    return HTTPException(409 if isinstance(exc, hf_build.BuildBusy) else 422, str(exc))


@router.post("/story-plan")
def story_plan(req: StoryPlanRequest) -> dict[str, Any]:
    """Transcribe a whole file and split it into stories before choosing CapCut or HyperFrames."""
    file = _story_file(req.file)
    if not Path(file).is_file():
        raise HTTPException(status_code=400, detail="file not found")
    import story_selection
    import web_ai_json
    import web_ai_transaction
    start = False
    try:
        with project_lock.writer(file, recover=False), _story_guard:
            web_ai_transaction.recover("stories", file)
            if "max_minutes" in req.model_fields_set:
                web_ai_json.save_story_target(file, req.max_minutes)
            if req.brief is not None:
                if req.brief.strip():
                    atomic.write_text(video_edit.brief_file(file), req.brief.strip(), encoding="utf-8")
                else:
                    video_edit.brief_file(file).unlink(missing_ok=True)
            revision = story_selection.source_revision(file, require_transcript=False)
            job = _story_jobs.get(file)
            if not job or job["status"] == "error" or (job["status"] != "running" and job.get("input_revision") != revision):
                job, start = _new_story_job(file, revision), True
            elif job["status"] != "running":
                phrases = json.loads(video_edit.transcript_file(file).read_text(encoding="utf-8"))
                _story_result(file, job, phrases)
                _story_jobs.save()
    except VideoEditError as exc:
        raise _story_http(exc) from exc
    if start:
        Thread(target=_run_story_plan, args=(file, job), daemon=True).start()
    return dict(job)


@router.get("/brief")
def brief(file: str = Query(...)) -> dict[str, Any]:
    import web_ai_json
    return {"brief": video_edit.read_brief(file), "max_minutes": web_ai_json.story_target(file)}


@router.get("/story-plan")
def story_plan_status(file: str = Query(...)) -> dict[str, Any]:
    import story_selection
    import web_ai_transaction
    file = _story_file(file)
    job = _story_jobs.get(file)
    if job and job["status"] in ("running", "error"):
        return dict(job)
    if not job and not video_edit.transcript_file(file).is_file():
        raise HTTPException(status_code=404, detail="no plan started for this file")
    start = False
    try:
        with project_lock.writer(file, recover=False), _story_guard:
            web_ai_transaction.recover("stories", file)
            job = _story_jobs.get(file)
            if job and job["status"] == "running":
                return dict(job)
            revision = story_selection.source_revision(file, require_transcript=False)
            if job and job.get("input_revision") == revision:
                phrases = json.loads(video_edit.transcript_file(file).read_text(encoding="utf-8"))
                _story_result(file, job, phrases)
                _story_jobs.save()
            else:
                job, start = _new_story_job(file, revision), True
    except VideoEditError as exc:
        raise _story_http(exc) from exc
    if start:
        Thread(target=_run_story_plan, args=(file, job), daemon=True).start()
    return dict(job)


class StorySaveRequest(BaseModel):
    file: str
    plan_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    stories: list[dict[str, Any]]


@router.post("/story-plan/save")
def story_plan_save(req: StorySaveRequest) -> dict[str, Any]:
    import story_selection
    import web_ai_identity
    import web_ai_json
    import web_ai_transaction
    file = _story_file(req.file)
    try:
        with project_lock.writer(file, recover=False), _story_guard:
            web_ai_transaction.recover("stories", file)
            if story_selection.source_revision(file, include_stories=True) != req.plan_revision:
                raise HTTPException(409, "ข้อมูลแบ่งเรื่องเปลี่ยนแล้ว · โหลดข้อมูลล่าสุดก่อนบันทึก")
            expected_input = story_selection.source_revision(file)
            phrases = json.loads(video_edit.transcript_file(file).read_text(encoding="utf-8"))
            stories = story_selection.validate(req.stories, phrases, max_minutes=web_ai_json.story_target(file))
            if story_selection.source_revision(file, include_stories=True) != req.plan_revision:
                raise HTTPException(409, "ข้อมูลแบ่งเรื่องเปลี่ยนระหว่างตรวจ · โหลดข้อมูลล่าสุดก่อนบันทึก")
            # Manual acceptance supersedes a pending AI answer through the same durable transaction.
            answer = {"clipkit_web_ai": 2, **web_ai_identity.issue("stories", file),
                      "step": "stories", "video": Path(file).name, "stories": stories}
            if (answer["revision"] != expected_input or
                    story_selection.source_revision(file, include_stories=True) != req.plan_revision):
                raise HTTPException(409, "ข้อมูลแบ่งเรื่องเปลี่ยนระหว่างบันทึก · โหลดข้อมูลล่าสุดก่อนบันทึก")
            web_ai_json.import_stories(file, answer)
            job = _story_jobs.setdefault(file, {})
            _story_result(file, job, phrases)
            _story_jobs.save()
            return dict(job)
    except VideoEditError as exc:
        raise _story_http(exc) from exc


@router.post("/capcut")
def capcut(req: DraftRequest) -> dict[str, Any]:
    try:
        end = _clip_end(req.file, req.end)
        result = video_edit.create_capcut_draft(
            video_path=req.file,
            project_name=req.name,
            clip_in=req.start,
            clip_out=end,
            cuts=[c.model_dump() for c in req.cuts],
            subs=[s.model_dump() for s in req.subs],
            sub_size=req.sub_size,
            sub_y=req.sub_y,
            sub_color=req.sub_color,
            sub_stroke=req.sub_stroke,
            shape=req.shape,
            focus=(req.focus_x, req.focus_y),
        )
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # which raw file this project came from: the sidebar groups projects under it
    atomic.write_text((Path(result["draft_path"]) / "clipkit.json"), 
        json.dumps({"raw": req.file, "start": req.start, "end": end, "via": req.via}, ensure_ascii=False), encoding="utf-8")
    if req.template:
        apply_template(result["draft_path"], req.template)
    return result


# ── project templates: the whole look of a clip, saved once and given to every new project ──
TEMPLATES_DIR = _APP_DIR.parent / "project_templates"   # this machine's own (git-ignored)
TEMPLATE_KEYS = ("anim", "preset", "zoomcut", "progress", "emoji", "ramp", "skin", "music", "music_volume", "sfx", "sfx_volume",
                 "auto_color", "trim", "hook_colors")
TEMPLATE_DEFAULT = {"anim": "pair", "preset": "default", "zoomcut": True, "skin": 0.6, "auto_color": True, "trim": True,
                    "sfx": True, "sfx_volume": 1.0, "music_volume": 0.12, "hook_colors": ["white", "orange"]}


def _template(name: str) -> dict[str, Any]:
    f = TEMPLATES_DIR / f"{name}.json"
    if not f.is_file():
        raise HTTPException(400, f"ไม่พบเทมเพลต: {name}")
    return {**TEMPLATE_DEFAULT, **json.loads(f.read_text(encoding="utf-8"))}


@project_lock.edit
def apply_template(path: str, name: str) -> dict[str, Any]:
    """Put a template's settings into a project (clipkit_style.json); the steps they switch on run in prepare."""
    t = _template(name)
    f = Path(path) / "clipkit_style.json"
    style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    style.update({k: v for k, v in t.items() if k in TEMPLATE_KEYS})
    style["template"] = name
    atomic.write_text(f, json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return style


@router.get("/templates")
def templates() -> dict[str, Any]:
    names = sorted(p.stem for p in TEMPLATES_DIR.glob("*.json")) if TEMPLATES_DIR.is_dir() else []
    return {"templates": names}


class TemplateSave(BaseModel):
    path: str
    name: str = Field(min_length=1, max_length=60, pattern=r"^[^\\/:*?\"<>|]+$")


@router.post("/template/save")
def template_save(req: TemplateSave) -> dict[str, Any]:
    """This project's look as a template: subtitle look and preset, zoom, skin, colour, music, clicks, title colours."""
    f = Path(req.path) / "clipkit_style.json"
    style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    t = {k: style[k] for k in TEMPLATE_KEYS if k in style}
    t["auto_color"] = bool(style.get("color"))
    t["trim"] = style.get("trim", True)
    placed = Path(req.path) / "clipkit_placed.json"
    if placed.is_file():  # the music and clicks as reviewed in the editor
        pl = json.loads(placed.read_text(encoding="utf-8"))
        if pl.get("music") is not None:
            t["music"], t["music_volume"] = pl["music"].get("file", ""), pl["music"].get("volume", 0.12)
        if pl.get("sfx") is not None:
            t["sfx"], t["sfx_volume"] = pl["sfx"].get("on", True), pl["sfx"].get("volume", 1.0)
    hook = Path(req.path) / "clipkit_hook.json"
    if hook.is_file():
        t["hook_colors"] = [x.get("color", "white") for x in json.loads(hook.read_text(encoding="utf-8"))][:2]
    TEMPLATES_DIR.mkdir(exist_ok=True)
    atomic.write_text((TEMPLATES_DIR / f"{req.name.strip()}.json"), json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"template": req.name.strip(), "settings": t}


class Prepare(BaseModel):
    path: str
    template: str | None = None
    capcut: bool = True


@router.post("/draft/prepare")
@project_lock.edit
def draft_prepare(req: Prepare) -> dict[str, Any]:
    """Everything the template switches on, then (capcut) the finished layout as a CapCut project to keep editing:
    subtitles, breaths cut, colour, then capcut_build. No MP4 preview here (that is the ClipKit route)."""
    import capcut_build
    steps = []
    try:
        style = apply_template(req.path, req.template) if req.template else {}
        t = {**TEMPLATE_DEFAULT, **style}
        if not capcut_edit.read_draft(req.path)["subtitles"]:
            got = capcut_edit.transcribe_draft(req.path)
            capcut_edit.set_subtitles(req.path, got["subtitles"])
            capcut_edit.subtitles_language(req.path, "th")
            steps.append(f"ถอดเสียงใส่ซับ {got['count']} บรรทัด")
        if t.get("trim") and not (Path(req.path) / "draft_content.json.bak_trim").exists():
            capcut_edit.trim_pauses(req.path)
            steps.append("ตัดช่วงหายใจ")
        if t.get("auto_color") and not style.get("color"):
            auto_color(req.path)
            steps.append("ปรับสีอัตโนมัติ")
        out = None
        if req.capcut:
            out, warnings = capcut_build.build(req.path)
            steps.append("ทำโปรเจกต์ CapCut แล้ว")
            steps += warnings
    except VideoEditError as exc:
        raise HTTPException(400, "; ".join(steps + [str(exc)])) from exc
    return {"steps": steps, "capcut": str(out) if out else None}


class FocusRequest(BaseModel):
    file: str
    start: float = 0.0
    end: float | None = None


@router.post("/focus")
def focus(req: FocusRequest) -> dict[str, Any]:
    """Where the speaker's face is, to place the crop."""
    try:
        return video_edit.find_focus(req.file, req.start, req.end)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/browse")
def browse(req: BrowseRequest) -> dict[str, Any]:
    """Folder picker data: drives, subfolders, and video counts."""
    try:
        return video_edit.browse(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/subtitle-presets")
def subtitle_presets() -> dict[str, Any]:
    """Subtitle presets (normal + emphasis text) in presets/, made by each user."""
    import render
    return {"presets": render.preset_names()}


@router.get("/presets")
def presets() -> dict[str, Any]:
    """Colour grade and subtitle styling taken from the projects already cut."""
    try:
        return video_edit.extract_presets()
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/briefs")
def briefs(req: BriefRequest) -> dict[str, Any]:
    """Per-clip notes of a 'For Cutting' style folder (hook line, footage)."""
    try:
        items = video_edit.read_briefs(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(items), "briefs": items}


@router.get("/frame")
def frame(path: str = Query(...), t: float = Query(1.0), width: int = Query(360)) -> Response:
    """A still from the clip, used as the backdrop of the subtitle preview."""
    try:
        data = video_edit.grab_frame(path, t, width)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "max-age=300"})


@router.get("/drafts")
def drafts() -> dict[str, Any]:
    """CapCut projects already on this machine."""
    try:
        items = capcut_edit.list_drafts()
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for d in items:  # made by the ClipKit button, or a CapCut project (any made before the buttons split)
        meta = Path(d["path"]) / "clipkit.json"
        d["via"] = json.loads(meta.read_text(encoding="utf-8")).get("via", "capcut") if meta.is_file() else "capcut"
    return {"count": len(items), "drafts": items}


@router.post("/draft")
def draft(req: DraftPath) -> dict[str, Any]:
    try:
        return capcut_edit.read_draft(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/transcribe")
def draft_transcribe(req: DraftPath) -> dict[str, Any]:
    """Subtitles for an existing project, timed to its own timeline."""
    try:
        return capcut_edit.transcribe_draft(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"transcribe failed: {exc}") from exc


class ExportRequest(BaseModel):
    path: str
    preview: bool = False  # quick half-size file for review before the real export


@router.post("/draft/export")
@project_lock.edit
def draft_export(req: ExportRequest) -> dict[str, Any]:
    """MP4 straight from ClipKit (no CapCut): <output_dir>/exports/<project>.mp4"""
    import kit_settings
    import render
    cfg = kit_settings._read_config()
    out = cfg.get("output_dir") or cfg.get("work_root")  # same place covers and motion files go
    if not out:
        raise HTTPException(400, "ยังไม่ได้ตั้งที่เก็บไฟล์ส่งออก — ไปที่ ตั้งค่า > โฟลเดอร์")
    try:
        return render.render_draft(req.path, str(Path(out) / "exports"), preview=req.preview)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/subs")
@project_lock.edit
def draft_subs(req: DraftSubsRequest) -> dict[str, Any]:
    try:
        return capcut_edit.set_subtitles(
            req.path, [s.model_dump() for s in req.subs], size=req.size, y=req.y,
            color=req.color, stroke=req.stroke, replace=req.replace)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class MergeSubtitlesRequest(BaseModel):
    path: str
    first: int = Field(ge=1, strict=True)
    last: int = Field(ge=2, strict=True)


@router.post("/draft/subs/merge")
@project_lock.edit
def draft_merge_subtitles(req: MergeSubtitlesRequest) -> dict[str, Any]:
    try:
        rows = capcut_edit.merge_subtitles(req.path, [(req.first, req.last)])
        return {"subtitles": len(rows)}
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class TidyRequest(BaseModel):
    path: str
    mode: str = Field(default="lines", pattern="^(lines|split|shrink)$")


@router.get("/draft/text-check")
def draft_text_check(path: str = Query(...)) -> dict[str, Any]:
    """What the text settings page shows before an export: the subtitle look and the lines outside the safe zone."""
    import render
    try:
        subs = capcut_edit.read_draft(path)["subtitles"]
        f = Path(path) / "clipkit_style.json"
        style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        return {"look": subs[0] if subs else None, "count": len(subs), "problems": render.safe_zone(path),
                "subs": [{k: s[k] for k in ("start", "end", "text")} for s in subs],
                "box": render.safe_box(style), "zone": style.get("safe_zone") or "general",
                "zones": {k: z["label"] for k, z in render.SAFE_ZONES.items()},
                "line_h": float(style.get("line_h", render.LINE_H)), "px_per_size": render.CAPCUT_PX}
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/tidy")
@project_lock.edit
def draft_tidy(req: TidyRequest) -> dict[str, Any]:
    try:
        return capcut_edit.tidy_subtitles(req.path, req.mode)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/subs/fill")
@project_lock.edit
def draft_subs_fill(req: FillGapsRequest) -> dict[str, Any]:
    """Subtitle only the stretches that have none, for footage added later."""
    try:
        return capcut_edit.fill_subtitle_gaps(req.path, min_gap=req.min_gap,
                                              preview=req.preview)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class SubsLangRequest(BaseModel):
    path: str
    lang: str = Field(pattern="^(th|en)$")


@router.post("/draft/subs-lang")
@project_lock.edit
def draft_subs_lang(req: SubsLangRequest) -> dict[str, Any]:
    try:
        return capcut_edit.subtitles_language(req.path, req.lang)
    except capcut_edit.NeedsAgent as exc:
        raise HTTPException(status_code=409, detail={"command": str(exc)}) from exc
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class TextPlace(BaseModel):
    id: str
    x: float = Field(ge=-1.5, le=1.5)
    y: float = Field(ge=-1.5, le=1.5)
    rotation: float = Field(default=0.0, ge=-180, le=180)
    size: float | None = Field(default=None, ge=2, le=60)


class TextLayoutRequest(BaseModel):
    path: str
    items: list[TextPlace]


@router.post("/draft/layout")
@project_lock.edit
def draft_layout(req: TextLayoutRequest) -> dict[str, Any]:
    try:
        return capcut_edit.set_text_layout(req.path, [i.model_dump() for i in req.items])
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/style")
@project_lock.edit
def draft_style(req: DraftStyleRequest) -> dict[str, Any]:
    try:
        return capcut_edit.restyle_subtitles(req.path, size=req.size, y=req.y,
                                             color=req.color, stroke=req.stroke, line_h=req.line_h)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/trim")
@project_lock.edit
def draft_trim(req: DraftTrimRequest) -> dict[str, Any]:
    try:
        return capcut_edit.trim_pauses(req.path, keep=req.keep,
                                       min_gain=req.min_gain, apply=req.apply)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/grade")
@project_lock.edit("req.target")
def draft_grade(req: GradeRequest) -> dict[str, Any]:
    try:
        return capcut_edit.copy_grade(req.source, req.target)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/emphasis")
def emphasis(req: EmphasisRequest) -> dict[str, Any]:
    """First guess at which lines should use the emphasised style."""
    return {"lines": video_edit.suggest_emphasis(req.lines, req.ratio)}


@router.post("/terms")
def terms(req: TermsRequest) -> dict[str, Any]:
    """English stock-footage search terms for Thai subtitle lines."""
    try:
        return {"terms": video_edit.search_terms(req.lines, req.per_line)}
    except VideoEditError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/animations")
def animations() -> dict[str, Any]:
    """Animations already downloaded on this machine, read from real projects."""
    try:
        items = capcut_edit.list_animations()
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(items), "animations": items}


@router.post("/draft/animation")
@project_lock.edit
def draft_animation(req: AnimationRequest) -> dict[str, Any]:
    try:
        return capcut_edit.apply_animation(req.path, req.name, req.target, req.duration)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/animation/clear")
@project_lock.edit
def draft_animation_clear(req: AnimationClearRequest) -> dict[str, Any]:
    try:
        return capcut_edit.clear_animations(req.path, req.target)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/file")
def file(path: str = Query(...)) -> FileResponse:
    """Serve one music file so it can be auditioned in the page."""
    from pathlib import Path as _Path
    target = _Path(path).resolve()
    if not target.is_file() or target.suffix.lower() not in capcut_edit.AUDIO_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"not an audio file: {path}")
    known = any(target.is_relative_to(_Path(d).resolve()) for d in capcut_edit.music_dirs())
    if not known:
        raise HTTPException(status_code=403, detail="file is outside the music folders")
    return FileResponse(str(target))


@router.get("/music")
def music() -> dict[str, Any]:
    """Background music already owned, from the known folders."""
    try:
        items = capcut_edit.list_music()
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(items), "music": items}


@router.post("/draft/music")
@project_lock.edit
def draft_music(req: MusicRequest) -> dict[str, Any]:
    try:
        return capcut_edit.add_music(req.path, req.music, req.volume,
                                     req.fade_out, req.start, req.fade_in)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sounds")
def sounds() -> dict[str, Any]:
    """Sound effects available locally, learned from existing projects."""
    try:
        items = capcut_edit.list_sounds()
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(items), "sounds": items}


@router.post("/draft/sound")
@project_lock.edit
def draft_sound(req: SoundRequest) -> dict[str, Any]:
    try:
        return capcut_edit.add_sound_on_subtitles(req.path, req.sound, req.volume,
                                                  req.every_line, req.offset)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft/sound/clear")
@project_lock.edit
def draft_sound_clear(req: DraftPath) -> dict[str, Any]:
    try:
        return capcut_edit.clear_sounds(req.path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/config")
def config() -> dict[str, Any]:
    return {
        "drafts_root": video_edit.kitconfig.CFG.get("capcut_drafts", ""),
        "template_draft": video_edit.CAPCUT_TEMPLATE_DRAFT or "(newest draft in folder)",
        "whisper_model": video_edit.whisper_model(),
        "whisper_cpu_fallback": video_edit.WHISPER_CPU_FALLBACK,
        "gpu": video_edit._has_cuda(),
        "local_llm": video_edit.LOCAL_LLM_URL,
        "simple": video_edit.kitconfig.simple(),
        "team": video_edit.kitconfig.is_team(),
    }


@router.post("/update")
def update() -> dict[str, Any]:
    """The Update button: take the newest ClipKit release from GitHub (team clones), then restart the app."""
    root = Path(__file__).resolve().parents[1]
    import sys
    r = subprocess.run([sys.executable, str(root / "scripts" / "update.py")], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise HTTPException(400, "อัปเดตไม่ได้: " + (r.stderr or r.stdout).strip()[-300:])
    if r.stdout.startswith("already the newest"):
        return {"updated": False, "note": "เป็นตัวล่าสุดแล้ว"}
    import threading
    def restart():
        import os
        import time
        time.sleep(1)
        subprocess.Popen([sys.executable, *sys.argv], cwd=str(Path(__file__).parent))
        os._exit(0)
    threading.Thread(target=restart, daemon=True).start()
    return {"updated": True, "note": "อัปเดตแล้ว กำลังเปิดใหม่"}


# --- motion on top of the clip: pick a subtitle line, pick a template, it renders and sits at that time ---
OVERLAYS = "clipkit_overlays.json"
# made from the line's own words, so the editor offers them as one-click buttons on every line; any other
# motion/<name> template (numbers, infographics) needs its fields filled in (params), by the web AI or an agent
QUICK_MOTION = {"hook-title": "หัวคลิป", "sentence-pair": "คำเน้น"}


def motion_labels() -> dict[str, str]:
    import kit_settings
    return {k: QUICK_MOTION.get(k, v.get("label", k)) for k, v in kit_settings.motion_templates().items()}


def motion_params(template: str, params: dict[str, Any], line_len: float) -> tuple[dict[str, Any], float]:
    """params checked against the template's clipkit-motion fields, defaults filled in, a missing "duration" set to
    line_len (as long as the line, like the one-click buttons); returns them and the length in seconds. Raises on an unknown template, an unknown key, a bad value or a missing length."""
    import kit_settings
    import math
    spec = kit_settings.motion_templates().get(template)
    if spec is None:
        raise VideoEditError(f"unknown motion template: {template}")
    if not isinstance(params, dict):
        raise VideoEditError(f"{template}: params ต้องเป็น object {{...}}")
    fields = {f["key"]: f for f in spec.get("fields", [])}
    if extra := sorted(set(params) - set(fields)):
        raise VideoEditError(f"{template}: ไม่มีช่อง {', '.join(extra)} (มี {', '.join(fields)})")
    if missing := [k for k, f in fields.items() if f.get("required") and k not in params]:
        raise VideoEditError(f"{template}: ต้องมีช่อง {', '.join(missing)}")
    # a text the AI left out stays empty: the defaults are the motion page's demo words, not this clip's
    out = {k: "" if f["type"] == "text" else f["default"] for k, f in fields.items()
           if f["type"] not in ("pairs", "font") and f.get("default") is not None}
    for k, v in params.items():
        f = fields[k]
        if f["type"] == "number" and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0):
            raise VideoEditError(f"{template}.{k} ต้องเป็นตัวเลขมากกว่า 0 (ได้ {v!r})")
        if f["type"] in ("text", "font") and not isinstance(v, str):
            raise VideoEditError(f"{template}.{k} ต้องเป็นข้อความ (ได้ {v!r})")
        if f["type"] in ("text", "font") and len(v) > f.get("max_length", 500):
            raise VideoEditError(f"{template}.{k} ข้อความยาวเกินขอบเขตของ template")
        if f.get("pattern") and not re.fullmatch(f["pattern"], v):
            raise VideoEditError(f"{template}.{k} รูปแบบไม่ถูก (ได้ {v!r})")
        if f["type"] == "select" and v not in [o[0] for o in f["options"]]:
            raise VideoEditError(f"{template}.{k} ต้องเป็นหนึ่งใน {[o[0] for o in f['options']]} (ได้ {v!r})")
        if f["type"] == "pairs" and (not isinstance(v, list) or not v or any(
                not isinstance(x, list) or len(x) != 5 for x in v)):
            raise VideoEditError(f"{template}.{k} ต้องเป็น [[start, end, ขาว, เน้น, delay], ...] (ได้ {v!r})")
        out[k] = v
    if "duration" in fields and "duration" not in params:
        out["duration"] = line_len
    how = spec.get("length", "duration")
    try:
        length = max(float(x[1]) for x in out["pairs"]) if how == "pairs" else float(out[how])
    except (KeyError, TypeError, ValueError) as exc:
        raise VideoEditError(f"{template}: ไม่มีความยาว ({how})") from exc
    if not math.isfinite(length) or not 0 < length <= 120:
        raise VideoEditError(f"{template}: ความยาวต้องอยู่ในช่วงมากกว่า 0 ถึง 120 วินาที")
    return out, length


def _overlays(path: str) -> list[dict[str, Any]]:
    f = Path(path) / OVERLAYS
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []


@router.get("/draft/overlays")
def draft_overlays(path: str = Query(...)) -> dict[str, Any]:
    try:
        d = capcut_edit.read_draft(path)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"subs": [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in d["subtitles"]],
            "overlays": _overlays(path), "templates": motion_labels(), "quick": QUICK_MOTION}


class OverlayRequest(BaseModel):
    path: str
    template: str
    start: float = Field(ge=0)
    end: float
    text: str
    params: dict[str, Any] | None = None   # the template's fields; None = made from text (QUICK_MOTION only)
    cue_id: str | None = None
    placement_id: str | None = None
    placement: dict[str, Any] | None = None
    ownership: dict[str, Any] | None = None
    anchor: dict[str, Any] | None = None
    reason: str = Field(default="ผู้ใช้เลือก motion จากเนื้อหา", min_length=1, max_length=500)
    review_revision: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")


def prepare_overlay(req: OverlayRequest, *, previous=None, timeline=None) -> dict[str, Any]:
    """Read-only layout preflight shared by single edits and whole AI plans."""
    import kit_settings
    import math
    import render
    import motion_plan
    dur = req.end - req.start
    ownership = None
    try:
        if not math.isfinite(dur) or dur <= 0:
            raise VideoEditError("motion ต้องมีเวลาเริ่มก่อนเวลาจบ")
        if timeline is None:
            timeline = render.render_draft(req.path, "", dry=True)
        base = {k: v for k, v in timeline.items() if k != "subtitle_events"}
        base["motions"] = []
        if timeline["look"] != "pair" and "plain_events" not in timeline and timeline["phrases"]:
            raise VideoEditError("เลือกพรีเซ็ตซับแบบวัดตำแหน่งก่อนวาง motion ร่วมกับข้อความ")
        underlying = render.pair_events_from_timeline(base)
        anchor = req.anchor or {"kind": "timeline", "start": req.start}
        import insert_plan
        resolved, _ = insert_plan.resolve_anchor(req.path, anchor)
        if abs(resolved - req.start) > 1e-6:
            raise VideoEditError("เวลา motion ไม่ตรง anchor ล่าสุด · เลือกจุดคำพูดใหม่")
        if req.template == "sentence-pair":
            tight = lambda text: "".join(render.unbreak(text).split())
            phrases = [p for p in timeline["phrases"] if p["end"] > req.start and p["start"] < req.end
                       and (req.cue_id is None or p.get("cue_id") == req.cue_id)
                       and (tight(p["line"]) == tight(req.text) or tight(p["lead"] + p["punch"]) == tight(req.text))]
            if not phrases:
                raise VideoEditError("หาประโยคและเวลาคำเน้นไม่พบ · เลือกบรรทัดจาก Timeline อีกครั้ง")
            cues = {p.get("cue_id") for p in phrases}
            if len(cues) != 1 or not next(iter(cues)):
                raise VideoEditError("motion ต้องอ้าง cue เดียวที่ชัดเจน · เลือกบรรทัดจาก Timeline อีกครั้ง")
            if req.params:
                expected = [[p["start"] - req.start, p["end"] - req.start, p["lead"], p["punch"],
                             p["hit"] - p["start"]] for p in phrases]
                if set(req.params) != {"pairs"} or req.params["pairs"] != expected:
                    raise VideoEditError("sentence-pair ใช้ข้อความและเวลาจริงจาก Timeline · เอา params ที่กำหนดเองออก")
            # Replacing an existing overlay needs the underlying roles before that overlay hides them.
            events = [e for e in underlying
                      if e.get("cue_id") in cues and e["end"] > req.start and e["start"] < req.end]
            if req.ownership is not None:
                ownership = motion_plan.ownership({"ownership": req.ownership})
                if ownership["mode"] != "replace" or any(target["cue_id"] not in cues for target in ownership["targets"]):
                    raise VideoEditError("sentence-pair ต้องแทนบทบาทของ cue ที่เลือกเท่านั้น")
                events = [e for e in events if motion_plan.hides({"ownership": ownership}, role=e["role"], cue_id=e.get("cue_id"))]
            if any(e["start"] < req.start - .001 or e["end"] > req.end + .001 for e in events):
                raise VideoEditError("motion ตัดช่วงข้อความค้างหรือ caption · ขยายช่วงให้ครบก่อนสร้าง")
            events = [{**e, "start": max(0, e["start"] - req.start), "end": e["end"] - req.start,
                       "effect_start": e.get("effect_start", e["start"]) - req.start} for e in events]
            params = kit_settings.sentence_pair_params({"subtitle_events": events,
                "width": timeline["width"], "height": timeline["height"]})
            dur = max(event["end"] for event in params["subtitle_events"])
            roles = sorted({e["role"] for e in events})
            derived = motion_plan.ownership({"ownership": {"mode": "replace",
                "targets": [{"cue_id": next(iter(cues)), "roles": roles}],
                "caption_policy": "replace" if "caption" in roles else "preserve"}})
            if ownership is not None and ownership != derived:
                raise VideoEditError("motion ต้องแทนบทบาทที่มีอยู่จริงใน cue")
            ownership = derived
        elif req.params is not None:
            params, dur = motion_params(req.template, req.params, dur)
        elif req.template == "hook-title":
            params = {"key": req.text, "sub": "", "style": "straight", "duration": dur}
        else:
            raise VideoEditError(f"{req.template} ต้องกรอกช่องของมันเอง (params)")
        if ownership is None:
            ownership = motion_plan.ownership({"ownership": req.ownership or
                {"mode": "decorate", "targets": [], "caption_policy": "preserve"}})
        prepared = motion_plan.prepare_template(req.template, params, width=timeline["width"],
                                                height=timeline["height"], placement=req.placement)
        dur = prepared["duration"]
        if not .5 <= dur <= 120:
            raise VideoEditError("motion ต้องยาว 0.5–120 วินาที")
        candidate = {"start": req.start, "duration": dur, "bounds": prepared["bounds"],
                     "ownership": ownership, "params": prepared["source_params"]}
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc
    previous = _overlays(req.path) if previous is None else previous
    if req.placement_id is not None and sum(o.get("placement_id") == req.placement_id for o in previous) != 1:
        raise HTTPException(409, "ไม่พบ motion ที่จะแก้ · โหลด Timeline ใหม่")
    items = [o for o in previous if req.placement_id is None or o.get("placement_id") != req.placement_id]
    for item in items:
        a, length = item.get("start"), item.get("duration")
        if (any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (a, length))
                or a < 0 or length <= 0):
            raise HTTPException(400, "เวลา motion เดิมไม่ถูกต้อง · ตรวจรายการก่อนเพิ่ม")
        try:
            if a < req.start + dur and req.start < a + length and any(
                    error.get("placement_id") == item.get("placement_id") for error in timeline.get("motion_recipe_errors", [])):
                raise VideoEditError("stored motion is stale; repair its recipe before concurrent placement")
            motion_plan.check_overlap(candidate, item)
        except VideoEditError as exc:
            raise HTTPException(409, str(exc)) from exc
    try:
        motion_plan.check_placement(candidate, underlying, width=timeline["width"], height=timeline["height"],
                                    duration=timeline["duration"], safe_box=render.safe_box(timeline["style"]),
                                    subject_zones=timeline["style"].get("motion_subject_zones", []), others=items)
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"params": params, "prepared": prepared, "ownership": ownership, "anchor": anchor,
            "timeline": timeline, "items": items, "candidate": candidate,
            "cue_id": next(iter(cues)) if req.template == "sentence-pair" else None}


def overlay_plan(req: OverlayRequest, plan: dict) -> dict[str, Any]:
    """Review content, clocks and geometry without exposing local input paths."""
    import motion_plan
    prepared = plan["prepared"]
    return {"template": req.template, "text": req.text, "start": req.start, "duration": prepared["duration"],
            "motion_before": len(plan["items"]) + (1 if req.placement_id else 0),
            "anchor": plan["anchor"], "reason": req.reason, "ownership": plan["ownership"],
            "canvas": prepared["canvas"], "bounds": prepared["bounds"],
            "params": {key: value for key, value in prepared["source_params"].items()
                       if key not in {"font", "subtitle_events"}},
            "events": [{key: event[key] for key in ("role", "text", "start", "end")}
                       for event in prepared["source_params"].get("subtitle_events", [])],
            "subject_review_pending": motion_plan.subject_review_pending(plan["timeline"]["style"].get("motion_subject_zones", []), req.start, req.start + prepared["duration"])}


@router.post("/draft/overlay-preview")
def draft_overlay_preview(req: OverlayRequest) -> dict[str, Any]:
    try:
        with project_lock.writer(req.path, recover=False):
            revision = _overlay_revision(req)
            result = overlay_plan(req, prepare_overlay(req))
            if _overlay_revision(req) != revision:
                raise VideoEditError("โปรเจกต์เปลี่ยนระหว่างตรวจ motion · ตรวจใหม่")
            return {**result, "review_revision": revision}
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc


def _overlay_revision(req: OverlayRequest) -> str:
    import web_ai_identity as identity
    snapshot, _ = identity._snapshot("clip", req.path, None, {})
    return identity._hash({"snapshot": snapshot, "request": req.model_dump(exclude={"review_revision"})})


def render_overlay(req: OverlayRequest, plan: dict) -> dict[str, Any]:
    """Materialize one preflighted recipe without publishing project metadata."""
    import kit_settings
    import motion_plan
    prepared, timeline = plan["prepared"], plan["timeline"]
    name = f"{Path(req.path).name[:40]} {req.template} {int(req.start * 1000)}ms"
    rendered = kit_settings.make_motion(kit_settings.MotionRequest(template=req.template, name=name, params=plan["params"],
        width=timeline["width"], height=timeline["height"], placement=req.placement))
    if rendered["recipe"]["layout_sha256"] != prepared["layout_sha256"]:
        raise HTTPException(409, "motion เปลี่ยนหลังตรวจตำแหน่ง · ตรวจแผนใหม่ก่อนใช้")
    item = {"file": rendered["file"], "start": req.start, "duration": prepared["duration"], "template": req.template, "text": req.text,
            "placement_id": req.placement_id or __import__("uuid").uuid4().hex, "params": prepared["source_params"],
            "ownership": plan["ownership"], "anchor": plan["anchor"], "reason": req.reason, "recipe": rendered["recipe"],
            "canvas": prepared["canvas"], "bounds": prepared["bounds"],
            "subject_review_pending": motion_plan.subject_review_pending(timeline["style"].get("motion_subject_zones", []), req.start, req.start + prepared["duration"])}
    if req.template == "sentence-pair":
        item["cue_id"] = plan["cue_id"]
    return item


@router.post("/draft/overlay")
@project_lock.edit
def draft_overlay_add(req: OverlayRequest) -> dict[str, Any]:
    prepare_overlay(req)
    import kit_settings
    import web_ai_identity as identity
    import web_ai_transaction as transaction
    try:
        revision = _overlay_revision(req)
        if req.review_revision is not None and revision != req.review_revision:
            raise VideoEditError("โปรเจกต์หรือคำสั่ง motion เปลี่ยน · ตรวจแผนใหม่")
        answer = {"clipkit_web_ai": 2, **identity.issue("clip", req.path, operation="manual"), "step": "clip",
                  "manual": "motion", "request": req.model_dump(exclude={"review_revision"})}
        if _overlay_revision(req) != revision:
            raise VideoEditError("โปรเจกต์เปลี่ยนระหว่างเตรียม motion · ตรวจแผนใหม่")
        def prepare(work, assets):
            staged = req.model_copy(update={"path": str(work)})
            plan = prepare_overlay(staged)
            with kit_settings.motion_destination(assets):
                items = plan["items"] + [render_overlay(staged, plan)]
            items.sort(key=lambda item: item["start"])
            atomic.write_text(work / OVERLAYS, json.dumps(items, ensure_ascii=False, indent=1))
            return {"overlays": items}
        return transaction.apply("clip", req.path, answer, prepare)
    except VideoEditError as exc:
        raise HTTPException(409, str(exc)) from exc


class OverlayDelete(BaseModel):
    path: str
    start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    placement_id: str | None = None


@router.post("/draft/overlay-remove")
@project_lock.edit
def draft_overlay_remove(req: OverlayDelete) -> dict[str, Any]:
    previous = _overlays(req.path)
    selected = [o for o in previous if (o.get("placement_id") == req.placement_id if req.placement_id is not None
                                       else req.start is not None and abs(o["start"] - req.start) <= 1e-6)]
    if len(selected) != 1:
        raise HTTPException(409, "ไม่พบ motion เดียวที่ตรงกัน · โหลด Timeline ใหม่")
    items = [o for o in previous if o is not selected[0]]
    atomic.write_text((Path(req.path) / OVERLAYS), json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"overlays": items}


class SubAnimRequest(BaseModel):
    path: str
    anim: str = Field(pattern="^(none|pop|karaoke|pair)$")


@router.post("/draft/sub-anim")
@project_lock.edit
def draft_sub_anim(req: SubAnimRequest) -> dict[str, Any]:
    """How subtitles move in the MP4 export: still, pop in, or the spoken word lit up."""
    f = Path(req.path) / "clipkit_style.json"
    style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    style["anim"] = req.anim
    atomic.write_text(f, json.dumps(style, ensure_ascii=False), encoding="utf-8")
    words = Path(req.path) / "clipkit_words.json"
    return {"anim": req.anim, "zoomcut": bool(style.get("zoomcut")), "has_word_times": words.is_file()}


@router.get("/draft/sub-anim")
def draft_sub_anim_get(path: str = Query(...)) -> dict[str, Any]:
    f = Path(path) / "clipkit_style.json"
    style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    return {"anim": style.get("anim", "none"), "zoomcut": bool(style.get("zoomcut")),
            "has_word_times": (Path(path) / "clipkit_words.json").is_file()}


class ZoomCut(BaseModel):
    path: str
    on: bool


@router.post("/draft/zoomcut")
@project_lock.edit
def draft_zoomcut(req: ZoomCut) -> dict[str, Any]:
    """Every other subtitle line punched in (an editing effect for the MP4 export)."""
    f = Path(req.path) / "clipkit_style.json"
    style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    style["zoomcut"] = req.on
    atomic.write_text(f, json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return {"zoomcut": req.on}


@router.post("/draft/spelling-request")
@project_lock.edit
def draft_spelling_request(req: DraftPath) -> dict[str, Any]:
    """Lines for the agent to check for misheard words; the editor pastes the returned command into chat."""
    lines = [s["text"] for s in capcut_edit.read_draft(req.path)["subtitles"]]
    if not lines:
        raise HTTPException(400, "ยังไม่มีซับ — ถอดเสียงก่อน")
    atomic.write_text((Path(req.path) / "clipkit_lines.json"), json.dumps(lines, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"command": video_edit.agent_command("spelling", req.path), "lines": len(lines)}


@router.post("/draft/punch-request")
@project_lock.edit
def draft_punch_request(req: DraftPath) -> dict[str, Any]:
    """Lines for the agent to pick punch words from; the editor pastes the returned command into chat."""
    lines = [s["text"] for s in capcut_edit.read_draft(req.path)["subtitles"]]
    if not lines:
        raise HTTPException(400, "ยังไม่มีซับ — ถอดเสียงก่อน")
    atomic.write_text((Path(req.path) / "clipkit_lines.json"), json.dumps(lines, ensure_ascii=False, indent=1), encoding="utf-8")
    done = Path(req.path) / "clipkit_punch.json"
    picked = json.loads(done.read_text(encoding="utf-8")) if done.is_file() else {}
    return {"command": video_edit.agent_command("punch", req.path), "lines": len(lines),
            "picked": sum(1 for t in lines if t in picked)}


class WebAiRequest(BaseModel):
    step: str = Field(pattern="^(stories|clip)$")
    target: str  # the video (stories) or the project folder (clip)
    answer: str = ""
    max_minutes: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    operation: str = Field(default="clip", pattern="^(clip|inserts)$")
    folder: str | None = None


@router.post("/web-ai/export")
def web_ai_export(req: WebAiRequest) -> dict[str, Any]:
    """The file to upload to an AI in the browser instead of pasting the command to the agent here."""
    import web_ai_json as web_ai
    try:
        if req.operation == "inserts":
            if req.step != "clip" or not req.folder:
                raise VideoEditError("เลือกโฟลเดอร์ภาพแทรกก่อนส่งออก")
            out, sheet = web_ai.export_inserts(req.target, folder=req.folder)
            return {"file": str(out), "name": out.name, "text": out.read_text(encoding="utf-8"),
                    "sheet": {"file": str(sheet), "name": sheet.name}}
        out = (web_ai.export_stories(req.target, req.max_minutes) if req.step == "stories"
               else web_ai.export_clip(req.target, req.max_minutes))
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"file": str(out), "name": out.name, "text": out.read_text(encoding="utf-8")}


@router.post("/web-ai/preview")
def web_ai_preview(req: WebAiRequest) -> dict[str, Any]:
    import web_ai_json as web_ai
    try:
        if req.step != "clip":
            raise VideoEditError("เลือกโปรเจกต์คลิปก่อนตรวจแผน")
        return (web_ai.preview_inserts(req.target, req.answer) if req.operation == "inserts"
                else web_ai.preview_clip(req.target, req.answer))
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/web-ai/import")
def web_ai_import(req: WebAiRequest) -> dict[str, Any]:
    """The web AI's JSON answer, checked, into the same files the agent writes."""
    import web_ai_json as web_ai
    try:
        if req.answer.strip():
            operation = web_ai.parse(req.answer).get("operation")
            if operation != ("inserts" if req.operation == "inserts" else None):
                raise VideoEditError("คำตอบภาพแทรกต้องตรวจแผนในช่องคัดภาพแทรกด้วย AI ก่อนใช้")
        return web_ai.import_answer(req.step, req.target, req.answer)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class FreeSearch(BaseModel):
    query: str
    count: int = Field(default=6, ge=1, le=20)
    portrait: bool = True


@router.post("/stock/free-search")
def stock_free_search(req: FreeSearch) -> dict[str, Any]:
    """Pixabay hits (free for commercial use)."""
    import free_stock
    import kit_settings
    try:
        return free_stock.search(kit_settings._read_config(), req.query, req.count, req.portrait)
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class FreeDownload(BaseModel):
    source: str
    id: str
    file: str


@router.post("/stock/free-download")
def stock_free_download(req: FreeDownload) -> dict[str, Any]:
    import free_stock
    import kit_settings
    cfg = kit_settings._read_config()
    folder = cfg.get("stock_video") or cfg.get("output_dir") or cfg.get("work_root")
    if not folder:
        raise HTTPException(400, "ยังไม่ได้ตั้งโฟลเดอร์สต็อกวิดีโอ — ไปที่ ตั้งค่า > โฟลเดอร์")
    try:
        return {"path": free_stock.download(folder, req.source, req.id, req.file)}
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@project_lock.edit
def broll_fill(path: str) -> dict[str, Any]:
    """Search suggestions only; relevance and rights require an explicit asset choice."""
    import free_stock
    import kit_settings
    import insert_plan
    folder = Path(path)
    opportunities = [item for item in insert_plan.opportunities(path) if item.get("query")]
    out_file = folder / "clipkit_broll.json"
    chosen = json.loads(out_file.read_text(encoding="utf-8")) if out_file.is_file() else {}
    if not opportunities:
        return {"broll": chosen, "not_found": [], "queries": 0, "suggestions": [], "needs_review": False}
    cfg = kit_settings._read_config()
    searches, suggestions = {}, []
    for item in opportunities:
        q = item["query"]
        if q not in searches:
            searches[q] = free_stock.search(cfg, q, 3)["items"]
        suggestions.append({**item, "items": searches[q]})
    return {"broll": chosen, "not_found": [q for q, hits in searches.items() if not hits],
            "queries": len(opportunities), "suggestions": suggestions, "needs_review": True}


@router.post("/draft/broll-auto")
@project_lock.edit
def draft_broll_auto(req: DraftPath) -> dict[str, Any]:
    try:
        return broll_fill(req.path)
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc


class BrollRemove(BaseModel):
    path: str
    query: str


@router.post("/draft/broll-remove")
@project_lock.edit
def draft_broll_remove(req: BrollRemove) -> dict[str, Any]:
    f = Path(req.path) / "clipkit_broll.json"
    chosen = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    chosen.pop(req.query, None)
    atomic.write_text(f, json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"broll": chosen}


class AutoRequest(BaseModel):
    path: str
    look: str = Field(default="pair", pattern="^(pair|karaoke|pop|none)$")


@router.post("/draft/auto")
@project_lock.edit
def draft_auto(req: AutoRequest) -> dict[str, Any]:
    """Every step in one go, ending in a quick preview for a person to review before the real export:
    subtitles (if none yet) -> silence trimmed -> subtitle look + zoom cut -> b-roll (where the agent asked)
    -> music + sound effects -> half-size preview MP4."""
    import kit_settings
    import render
    steps = []
    try:
        if not capcut_edit.read_draft(req.path)["subtitles"]:
            got = capcut_edit.transcribe_draft(req.path)
            capcut_edit.set_subtitles(req.path, got["subtitles"])
            capcut_edit.subtitles_language(req.path, "th")
            steps.append(f"ถอดเสียงใส่ซับ {got['count']} บรรทัด")
        t = capcut_edit.trim_pauses(req.path)
        steps.append("ตัดช่วงเงียบแล้ว")
        f = Path(req.path) / "clipkit_style.json"
        style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        style.setdefault("anim", req.look)
        style.setdefault("zoomcut", True)
        style.setdefault("skin", render.SKIN_DEFAULT)
        atomic.write_text(f, json.dumps(style, ensure_ascii=False), encoding="utf-8")
        if not video_edit.kitconfig.simple():
            b = broll_fill(req.path)
            steps.append(f"รอเลือกภาพประกอบ {b['queries']} จุด" if b["queries"] else "ยังไม่มีคำค้นภาพประกอบ")
        cfg = kit_settings._read_config()
        out = cfg.get("output_dir") or cfg.get("work_root")
        if not out:
            raise HTTPException(400, "ยังไม่ได้ตั้งที่เก็บไฟล์ส่งออก — ไปที่ ตั้งค่า > โฟลเดอร์")
        r = render.render_draft(req.path, str(Path(out) / "exports"), preview=True)
        style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        if not style.get("cover") or not Path(style["cover"]).is_file():
            steps.append("ทำปกจากชื่อเรื่องแล้ว" if auto_cover(req.path) else "")
        placed = Path(req.path) / "clipkit_placed.json"
        if not placed.is_file():  # what the tool chose becomes the list the editor reviews and changes
            atomic.write_text(placed, json.dumps({"broll": r["broll_items"],
                                          "music": {"file": r["music_file"], "volume": r["music_volume"]},
                                          "sfx": {"on": r["sfx_on"], "volume": r["sfx_volume"]}},
                                         ensure_ascii=False, indent=1), encoding="utf-8")
    except VideoEditError as exc:
        raise HTTPException(status_code=400, detail="; ".join(steps + [str(exc)])) from exc
    return {"steps": steps, "preview": r, "trim": t, "agent_command": video_edit.agent_command("punch", req.path),
            "punch_picked": (Path(req.path) / "clipkit_punch.json").is_file()}



@router.get("/draft/placed")
def draft_placed(path: str = Query(...)) -> dict[str, Any]:
    """The b-roll / music / effects list the editor reviews, plus the subtitle lines to add a b-roll at."""
    f = Path(path) / "clipkit_placed.json"
    import insert_plan
    subs = [{"id": s["id"], "start": s["start"], "end": s["end"], "text": s["text"]} for s in capcut_edit.read_draft(path)["subtitles"]]
    try:
        return {"placed": json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None, "subs": subs,
                "opportunities": insert_plan.opportunities(path), "assets": insert_plan.public_assets(insert_plan.catalog(path))}
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc


class BrollItem(BaseModel):
    start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    dur: float = Field(gt=0, allow_inf_nan=False)
    file: str = ""
    query: str = ""
    thumb: str = ""
    asset_id: str | None = None
    placement_id: str | None = None
    opportunity_id: str | None = None
    anchor: dict[str, Any] | None = None
    reason: str = Field(default="ผู้ใช้เลือกภาพและตำแหน่ง", max_length=500)
    source_start: float = Field(default=0, ge=0, allow_inf_nan=False)
    focus_x: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    focus_y: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    source_ref: dict[str, Any] | None = None
    review_required: bool = False


class MusicPick(BaseModel):
    file: str = ""  # "" = no music
    volume: float = Field(default=0.12, ge=0, le=1, allow_inf_nan=False)


class SfxPick(BaseModel):
    on: bool = True
    volume: float = Field(default=1.0, ge=0, le=2, allow_inf_nan=False)


class PlacedRequest(BaseModel):
    path: str
    broll: list[BrollItem]
    music: MusicPick
    sfx: SfxPick
    reset: bool = False  # True = forget the edits, let the tool choose again


@router.post("/draft/placed")
@project_lock.edit
def draft_placed_save(req: PlacedRequest) -> dict[str, Any]:
    import insert_plan
    f = Path(req.path) / "clipkit_placed.json"
    if req.reset:
        f.unlink(missing_ok=True)
        return {"placed": None}
    if req.music.file and not Path(req.music.file).is_file():
        raise HTTPException(400, f"ไม่พบไฟล์เพลง: {req.music.file}")
    catalog_file = Path(req.path) / "clipkit_insert_assets.json"
    before = catalog_file.read_bytes() if catalog_file.is_file() else None
    try:
        items = [b.model_dump(exclude_none=True) for b in req.broll]
        for item in items:
            if not item.get("anchor"):
                if item.get("start") is None:
                    raise VideoEditError("เลือกคำหรือเวลาวางภาพแทรก")
                item["anchor"] = {"kind": "timeline", "start": item["start"]}
        current = insert_plan.catalog(req.path)
        for item in items:
            if item.get("asset_id") not in current["assets"] and item.get("review_required"):
                item.pop("asset_id", None)
        files = [item["file"] for item in items if not item.get("asset_id")]
        provenance = {item["file"]: item["source_ref"] for item in items if item.get("source_ref") and item["file"] in files}
        assets = insert_plan.register_assets(req.path, files, provenance=provenance) if files else insert_plan.catalog(req.path)
        for item in items:
            if not item.get("asset_id"):
                digest = insert_plan.file_digest(item["file"])
                item["asset_id"] = next((key for key, asset in assets["assets"].items() if asset["sha256"] == digest), "")
            item["review_required"] = False  # This explicit manual Save confirms the chosen placement.
        data = {"broll": insert_plan.normalize(req.path, items, assets=assets),
                "music": req.music.model_dump(), "sfx": req.sfx.model_dump()}
        atomic.write_text(f, json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        if before is None:
            catalog_file.unlink(missing_ok=True)
        elif not catalog_file.is_file() or catalog_file.read_bytes() != before:
            atomic.write_bytes(catalog_file, before)
        if isinstance(exc, VideoEditError):
            raise HTTPException(400, str(exc)) from exc
        raise
    return {"placed": data}


@router.get("/draft/timeline")
def draft_timeline(path: str = Query(...)) -> dict[str, Any]:
    """Everything the export will put on screen and in the sound, as tracks for the timeline editor."""
    import render
    try:
        t = render.render_draft(path, "", dry=True, repair=True)
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc
    d = capcut_edit.read_draft(path)
    t["subs"] = [{"id": s["id"], "start": s["start"], "end": s["end"], "text": s["text"]} for s in d["subtitles"]]
    t["name"], t["source"] = d["name"], d["source"]
    import kit_settings
    cfg = kit_settings._read_config()
    out = cfg.get("output_dir") or cfg.get("work_root") or ""
    pv = Path(out) / "exports" / f"{d['name']} - ตัวอย่าง.mp4"
    t["preview_file"] = str(pv) if out and pv.is_file() else ""
    meta = Path(path) / "clipkit.json"
    t["via"] = json.loads(meta.read_text(encoding="utf-8")).get("via", "capcut") if meta.is_file() else "capcut"
    t["agent_command"] = video_edit.agent_command("punch", path)
    t["punch_picked"] = (Path(path) / "clipkit_punch.json").is_file()
    base = {key: value for key, value in t.items() if key != "subtitle_events"}
    base["motions"] = []
    events = render.pair_events_from_timeline(base, layout_errors=[]) if t["look"] == "pair" or "plain_events" in t else []
    t["motion_cues"] = [{"cue_id": sub["id"], "text": sub["text"],
                         "roles": {role: {"start": min(e["start"] for e in events if e.get("cue_id") == sub["id"] and e["role"] == role),
                                          "end": max(e["end"] for e in events if e.get("cue_id") == sub["id"] and e["role"] == role)}
                                   for role in sorted({e["role"] for e in events if e.get("cue_id") == sub["id"]})},
                         "start": min(p["start"] for p in t["phrases"] if p.get("cue_id") == sub["id"]),
                         "end": max(event["end"] for event in events if event.get("cue_id") == sub["id"])}
                        for sub in t["subs"] if any(p.get("cue_id") == sub["id"] for p in t["phrases"])
                        and any(event.get("cue_id") == sub["id"] for event in events)]
    return t


class PhraseEdit(BaseModel):
    path: str
    line: str                  # the subtitle line this phrase belongs to (current text)
    cue_id: str | None = None
    pairs: list[list[str | dict[str, Any]]]  # every phrase of that line: [lead, punch, query?, options?]
    source_text: str | None = None  # explicit manual spelling change; never inferred from pair coverage
    caption: str | None = None  # None keeps this cue's caption; empty text explicitly clears it


@router.post("/draft/phrases")
@project_lock.edit
def draft_phrases(req: PhraseEdit) -> dict[str, Any]:
    """Validate source coverage, clocks and actual layout before saving one cue's choices."""
    import subtitle_plan
    import web_ai_json
    try:
        folder = Path(req.path)
        rows = capcut_edit.read_draft(req.path)["subtitles"]
        selected = cue_data.select(rows, req.line, req.cue_id)
        line = req.line if req.source_text is None else req.source_text.strip()
        items = subtitle_plan.validate_items(line, req.pairs, cue_id=selected["id"])
        data = cue_data.load(folder, "clipkit_punch.json", rows)
        data[cue_data.key(selected["id"])] = items
        captions = None
        if req.caption is not None:
            captions = cue_data.load(folder, "clipkit_caption.json", rows)
            if req.caption.strip():
                captions[cue_data.key(selected["id"])] = req.caption.strip()
            else:
                captions.pop(cue_data.key(selected["id"]), None)
        candidate = [{**row, "text": line} if row["id"] == selected["id"] else row for row in rows]
        baseline = []
        web_ai_json.validate_subtitles(req.path, layout_errors=baseline)

        def validate_repair(path, **changes):
            issues = []
            web_ai_json.validate_subtitles(path, layout_errors=issues, **changes)
            unchanged = {json.dumps(issue, sort_keys=True) for issue in baseline}
            for issue in issues:
                if (selected["id"] in (issue["cue_id"], issue.get("other_cue_id"))
                        or json.dumps(issue, sort_keys=True) not in unchanged):
                    raise VideoEditError(issue["message"])

        validate_repair(req.path, picks=data, captions=captions, rows=candidate)
        if line != req.line or captions is not None:
            import web_ai_identity
            import web_ai_transaction
            answer = {"clipkit_web_ai": 2, **web_ai_identity.issue("clip", req.path, operation="manual"),
                      "step": "clip", "project": folder.name, "manual": "phrases", "cue_id": selected["id"],
                      "line": req.line, "source_text": line, "pairs": items, "caption": req.caption}

            def prepare(work, assets):
                if captions is not None:
                    cue_data.save(work, "clipkit_caption.json", captions)
                if line != req.line:
                    capcut_edit.set_line_text(str(work), req.line, line, selected["id"], picks_override=items)
                cue_data.save(work, "clipkit_punch.json", data)
                validate_repair(str(work))
                return {"line": line, "cue_id": selected["id"]}

            return web_ai_transaction.apply("clip", req.path, answer, prepare)
        cue_data.save(folder, "clipkit_punch.json", data)
        return {"line": line, "cue_id": selected["id"]}
    except VideoEditError as exc:
        raise HTTPException(422, str(exc)) from exc


class SubjectZonesRequest(BaseModel):
    path: str
    previous: list[dict[str, Any]]
    zones: list[dict[str, Any]]


@router.post("/draft/subject-zones")
@project_lock.edit
def draft_subject_zones(req: SubjectZonesRequest) -> dict[str, Any]:
    import motion_plan
    import render
    import web_ai_transaction as transaction
    try:
        duration = capcut_edit.read_draft(req.path)["duration"]
        motion_plan.validate_subject_zones(req.zones, duration)
        style_file = Path(req.path) / "clipkit_style.json"
        style = json.loads(style_file.read_text(encoding="utf-8")) if style_file.is_file() else {}
        if style.get("motion_subject_zones", []) != req.previous:
            raise HTTPException(409, "พื้นที่เปลี่ยนแล้ว · โหลด Timeline ใหม่ก่อนแก้")
        def prepare(work):
            file = work / "clipkit_style.json"
            current = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}
            if current.get("motion_subject_zones", []) != req.previous:
                raise VideoEditError("พื้นที่เปลี่ยนแล้ว · โหลด Timeline ใหม่ก่อนแก้")
            current["motion_subject_zones"] = req.zones
            atomic.write_text(file, json.dumps(current, ensure_ascii=False, allow_nan=False))
            timeline = render.render_draft(str(work), "", dry=True)
            return {"style":current,"motion_recipe_errors":timeline["motion_recipe_errors"],"motions":timeline["motions"]}
        return transaction.manual(req.path, prepare)
    except VideoEditError as exc:
        raise HTTPException(422, str(exc)) from exc


class LookRequest(BaseModel):
    path: str
    style: dict[str, Any]   # merged into clipkit_style.json (anim, zoomcut, color, ...)


@router.post("/draft/look")
@project_lock.edit
def draft_look(req: LookRequest) -> dict[str, Any]:
    import web_ai_json
    try:
        f = Path(req.path) / "clipkit_style.json"
        style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        style.update(req.style)
        if "motion_subject_zones" in req.style:
            __import__("motion_plan").validate_subject_zones(req.style["motion_subject_zones"], capcut_edit.read_draft(req.path)["duration"])
        web_ai_json.validate_subtitles(req.path, style=style)
        atomic.write_text(f, json.dumps(style, ensure_ascii=False), encoding="utf-8")
        return {"style": style}
    except VideoEditError as exc:
        raise HTTPException(422, str(exc)) from exc


@project_lock.edit
def auto_color(path: str) -> dict[str, Any]:
    """A first colour pick from the footage itself, over 6 frames of the clip: white balance (grey-world, 60 %
    of the way, so warm indoor light stays a little warm), brightness towards a mid level, contrast up when the
    picture is flat, a little more colour. The temperature slider (warmth) stays the person's."""
    import subprocess as sp
    import numpy as np
    d = capcut_edit.read_draft(path)
    src, info = d["source"], video_edit.probe(d["source"])
    segs = capcut_edit._video_track(capcut_edit._load(Path(path)))["segments"]
    a = segs[0]["source_timerange"]["start"] / video_edit.US
    z = (segs[-1]["source_timerange"]["start"] + segs[-1]["source_timerange"]["duration"]) / video_edit.US
    px = []
    for k in range(6):
        r = sp.run([video_edit.FFMPEG, "-v", "error", "-ss", f"{a + (z - a) * (k + 0.5) / 6:.2f}", "-i", src, "-frames:v", "1",
                    "-vf", "scale=96:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
        if r.returncode or not r.stdout:
            raise VideoEditError("อ่านสีของภาพไม่ได้: " + r.stderr.decode("utf-8", "replace")[-200:])
        px.append(np.frombuffer(r.stdout, np.uint8).reshape(-1, 3) / 255)
    p = np.concatenate(px)
    luma = p @ [0.299, 0.587, 0.114]
    mean = p.mean(0)
    gains = [round(float(np.clip(1 + (mean.mean() / m - 1) * 0.6, 0.85, 1.15)), 3) for m in mean]
    spread = float(np.percentile(luma, 95) - np.percentile(luma, 5))
    y = float(luma.mean())
    color = {"r": gains[0], "g": gains[1], "b": gains[2],
             # gentle on purpose: +12 % contrast / +10 % colour looked too strong on real footage
             "brightness": round(max(-0.12, min(0.12, 0.47 - y)) * 0.3, 3),
             "contrast": round(min(1.05, max(1.0, 0.75 / max(spread, 0.1))), 3), "saturation": 1.0, "warmth": 0.0}
    f = Path(path) / "clipkit_style.json"
    style = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    color["warmth"] = float((style.get("color") or {}).get("warmth", 0))
    style["color"] = color
    atomic.write_text(f, json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return {"color": color, "measured_luma": round(y, 3), "measured_rgb": [round(float(x), 3) for x in mean],
            "spread": round(spread, 3)}


@router.post("/draft/auto-color")
@project_lock.edit
def draft_auto_color(req: DraftPath) -> dict[str, Any]:
    try:
        return auto_color(req.path)
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc



class LineTime(BaseModel):
    path: str
    line: str
    cue_id: str | None = None
    start: float = Field(ge=0)
    end: float


@router.post("/draft/line-time")
@project_lock.edit
def draft_line_time(req: LineTime) -> dict[str, Any]:
    try:
        return capcut_edit.set_line_time(req.path, req.line, req.start, req.end, req.cue_id)
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc


class HookLine(BaseModel):
    text: str
    color: str = Field(pattern="^(white|orange|red)$")


class HookEdit(BaseModel):
    path: str
    lines: list[HookLine] = Field(max_length=2)   # none = no title


@router.post("/draft/hook")
@project_lock.edit
def draft_hook(req: HookEdit) -> dict[str, Any]:
    """The clip title (first 4 s), edited in the editor: 1-2 lines, each white / orange / red."""
    import render
    f = Path(req.path) / "clipkit_hook.json"
    lines = [{"text": x.text.strip(), "color": x.color} for x in req.lines if x.text.strip()]
    if not lines:
        f.unlink(missing_ok=True)
        return {"hook": []}
    old = f.read_text(encoding="utf-8") if f.is_file() else None
    atomic.write_text(f, json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    try:
        return {"hook": render._hook(Path(req.path))}
    except VideoEditError as exc:
        if old is None:
            f.unlink()
        else:
            atomic.write_text(f, old, encoding="utf-8")
        raise HTTPException(400, str(exc)) from exc


class CaptionEdit(BaseModel):
    path: str
    line: str
    cue_id: str | None = None
    text: str


@router.post("/draft/caption")
@project_lock.edit
def draft_caption(req: CaptionEdit) -> dict[str, Any]:
    """The small translated caption of one spoken line (empty = none for that line)."""
    import web_ai_json
    try:
        rows = capcut_edit.read_draft(req.path)["subtitles"]
        selected = cue_data.select(rows, req.line, req.cue_id)
        data = cue_data.load(req.path, "clipkit_caption.json", rows)
        if req.text.strip():
            data[cue_data.key(selected["id"])] = req.text.strip()
        else:
            data.pop(cue_data.key(selected["id"]), None)
        web_ai_json.validate_subtitles(req.path, captions=data)
        cue_data.save(req.path, "clipkit_caption.json", data)
        return {"lines": len(data)}
    except VideoEditError as exc:
        raise HTTPException(422, str(exc)) from exc


class CutRange(BaseModel):
    path: str
    start: float = Field(ge=0)
    end: float


UNDO_FILES = ("draft_content.json", "clipkit_words.json", "clipkit_punch.json", "clipkit_caption.json", "clipkit_cues.json", "clipkit_placed.json", "clipkit_overlays.json", "clipkit_style.json")


def _undo_steps(folder):
    box = folder / "clipkit_undo"
    # Transactional deletion can leave empty containers; only metadata is an undo step.
    return sorted(step for step in box.iterdir() if step.is_dir() and any(step.iterdir())) if box.is_dir() else []


@router.post("/draft/cut")
@project_lock.edit
def draft_cut(req: CutRange) -> dict[str, Any]:
    """Cut a stretch of the talk out of the clip (subtitles inside it go too, later ones move up); undoable."""
    import web_ai_transaction
    if not web_ai_transaction.staged(req.path):
        try:
            return web_ai_transaction.manual(req.path, lambda work: draft_cut(req.model_copy(update={"path":str(work)})))
        except VideoEditError as exc:
            raise HTTPException(400, str(exc)) from exc
    import shutil
    import time
    if req.end - req.start < 0.1:
        raise HTTPException(400, "ช่วงที่จะตัดสั้นเกินไป")
    folder = Path(req.path)
    before = {name: web_ai_transaction._hash(file) for name, file in web_ai_transaction._files(folder).items()}
    undo = folder / "clipkit_undo" / (time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}")
    undo.mkdir(parents=True)
    for name in UNDO_FILES:
        if (folder / name).is_file():
            shutil.copy2(folder / name, undo / name)
    atomic.write_text(undo / "files.json", json.dumps({"version":2,"files":[name for name in UNDO_FILES if (undo / name).is_file()]}))
    try:
        r = capcut_edit.trim_pauses(req.path, ranges=[(req.start, req.end)])
    except BaseException as exc:
        try:
            restored = {name: web_ai_transaction._hash(file) for name, file in web_ai_transaction._files(folder).items()} == before
        except (OSError, VideoEditError):
            restored = False
        if restored:
            shutil.rmtree(undo)
        if isinstance(exc, VideoEditError):
            raise HTTPException(400, str(exc)) from exc
        raise
    return {"length": r.get("result_length"), "undo": len(_undo_steps(folder))}


@router.post("/draft/uncut")
@project_lock.edit
def draft_uncut(req: DraftPath) -> dict[str, Any]:
    """Undo the last cut made in the editor."""
    import web_ai_transaction
    if not web_ai_transaction.staged(req.path):
        try:
            return web_ai_transaction.manual(req.path, lambda work: draft_uncut(req.model_copy(update={"path":str(work)})))
        except VideoEditError as exc:
            raise HTTPException(400, str(exc)) from exc
    import shutil
    steps = _undo_steps(Path(req.path))
    if not steps:
        raise HTTPException(400, "ไม่มีการตัดให้ย้อนแล้ว")
    folder = Path(req.path)
    saved = {name: file.read_bytes() for name, file in web_ai_transaction._files(folder).items()}
    manifest = steps[-1] / "files.json"
    present = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else None
    tracked = tuple(name for name in UNDO_FILES if name != "clipkit_style.json")
    if isinstance(present, dict):
        if set(present) != {"version", "files"} or present["version"] != 2:
            raise HTTPException(400, "ข้อมูลย้อนการตัดเป็นรุ่นที่ไม่รองรับ")
        present, tracked = present["files"], UNDO_FILES
    if present is not None and (not isinstance(present, list) or any(name not in UNDO_FILES for name in present)):
        raise HTTPException(400, "ข้อมูลย้อนการตัดเสียหาย — เก็บไฟล์เดิมไว้เพื่อตรวจสอบ")
    try:
        for name in tracked:
            source = steps[-1] / name
            if source.is_file():
                atomic.write_bytes(folder / name, source.read_bytes())
            elif present is not None:
                if name in present:
                    raise VideoEditError(f"ไฟล์ย้อนการตัดหาย: {name}")
                (folder / name).unlink(missing_ok=True)
        restored = (folder / "draft_content.json").read_text(encoding="utf-8")
        capcut_edit._seal(folder, restored)
        capcut_edit.sync_timeline(folder, restored)
    except Exception:
        for name, file in web_ai_transaction._files(folder).items():
            if name not in saved:
                file.unlink()
        for name, content in saved.items():
            atomic.write_bytes(folder / name, content)
        raise
    shutil.rmtree(steps[-1])
    return {"undo": len(steps) - 1}


@project_lock.edit
def auto_cover(path: str) -> str:
    """A cover from the story title on the frame where the speaker's face is biggest; kept in clipkit_style.json."""
    import re
    import kit_settings
    meta = json.loads((Path(path) / "clipkit.json").read_text(encoding="utf-8")) if (Path(path) / "clipkit.json").is_file() else {}
    raw = meta.get("raw") or capcut_edit.read_draft(path)["source"]
    start, end = meta.get("start") or 0, meta.get("end")
    f = video_edit.find_focus(raw, start, end, frames=8)
    title = re.sub(r"^.* - \d+ ", "", Path(path).name).strip() or Path(path).name
    import render
    lines, _ = render._fit(title, render.DEFAULT_FONT, 100, 1080 * render.SAFE_W)
    l1, l2 = lines[0], " ".join(lines[1:])
    r = kit_settings.make_cover(kit_settings.CoverRequest(source=raw, at=f.get("best_at", start + 1), l1=l1, l2=l2))
    sf = Path(path) / "clipkit_style.json"
    style = json.loads(sf.read_text(encoding="utf-8")) if sf.is_file() else {}
    style["cover"] = r["file"]
    atomic.write_text(sf, json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return r["file"]


@router.post("/draft/auto-cover")
@project_lock.edit
def draft_auto_cover(req: DraftPath) -> dict[str, Any]:
    try:
        return {"file": auto_cover(req.path)}
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc



def _hf_key(path: str) -> str:
    import base64
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode().rstrip("=")


@router.post("/draft/hf-build")
def draft_hf_build(req: DraftPath) -> dict[str, Any]:
    """Write the project as a HyperFrames composition; the editor's player shows it live."""
    import hf_build
    try:
        hf_build.build(req.path, repair=True)
    except hf_build.BuildBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"url": f"/api/video/hfp/{_hf_key(req.path)}/index.html"}


@router.get("/hfp/{key}/{file:path}")
def hf_page(key: str, file: str) -> FileResponse:
    """The composition folder for the live player (index.html and its media/)."""
    import base64
    from urllib.parse import unquote
    root = (Path(base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)).decode("utf-8")) / "clipkit_hf").resolve()
    f = (root / unquote(file)).resolve()
    if root not in f.parents or not f.is_file():
        raise HTTPException(404, "not in the composition")
    return FileResponse(f, headers={"Cache-Control": "no-store"} if f.suffix == ".html" else None)


@router.post("/draft/hf-export")
def draft_hf_export(req: ExportRequest) -> dict[str, Any]:
    """MP4 from the same composition the player shows (hyperframes render)."""
    import shutil
    import tempfile
    import time as _t
    import hf_build
    import kit_settings
    import render
    import render_process
    cfg = kit_settings._read_config()
    out = cfg.get("output_dir") or cfg.get("work_root")
    if not out:
        raise HTTPException(400, "ยังไม่ได้ตั้งที่เก็บไฟล์ส่งออก — ไปที่ ตั้งค่า > โฟลเดอร์")
    npx = shutil.which("npx")
    if not npx:
        raise HTTPException(400, "ไม่พบ npx · ตรวจการติดตั้ง Node.js ก่อนส่งออก")
    target = Path(out) / "exports" / f"{Path(req.path).name}{' - ตัวอย่าง' if req.preview else ''}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    t0 = _t.time()
    cleanup_warning = ""
    try:
        with hf_build.build_lock(req.path), hf_build.build_lock(str(target)):
            import web_ai_transaction
            web_ai_transaction.recover("clip", req.path)
            import web_ai_identity
            check_export = web_ai_identity.export_check(req.path, target)
            expected = capcut_edit._load(Path(req.path))
            page = hf_build._build(req.path, final=not req.preview, audio_check=check_export.bind_media)  # reservation stays held while HyperFrames reads its composition
            stage = Path(tempfile.mkdtemp(prefix=".render-", dir=target.parent))
            staged = stage / "output.mp4"
            tmp = stage / "frames"
            try:
                tmp.mkdir()
                args = [npx, "--yes", f"hyperframes@{kit_settings.HF_VERSION}", "render", "--quiet", "--sdr", "--workers", "4", "-o", str(staged)]
                if req.preview:
                    args += ["--quality", "draft"]
                env = {**__import__("os").environ, "HYPERFRAMES_SKIP_SKILLS": "1", "TEMP": str(tmp), "TMP": str(tmp)}
                if host_platform.is_macos():
                    args = host_platform.hyperframes_command(args, page.parent, env)
                result = render_process.run(args, cwd=str(page.parent), capture_output=True, text=True, encoding="utf-8",
                                        errors="replace", env=env, timeout=3600)
                atomic.write_text(stage / "render.log", (result.stdout or "") + "\n" + (result.stderr or ""))
                if result.returncode or not staged.is_file() or not staged.stat().st_size:
                    raise VideoEditError("render failed: " + (result.stderr or result.stdout).strip()[-800:])
                render.normalize_loudness(staged)
                canvas = expected["canvas_config"]
                info = render.validate_output(staged, canvas["width"], canvas["height"], expected["duration"] / video_edit.US,
                                              preview=req.preview, decode_log=stage / "decode.log")
                import delivery_manifest
                evidence = delivery_manifest.prepare(req.path, staged, target, "mp4", check_export(),
                    options={"backend": "hyperframes", "version": kit_settings.HF_VERSION, "preview": req.preview},
                    checks={"encode": "passed", "full_av_decode": "passed", "dimensions": "passed", "duration": "passed", "audio": "passed", "probe": info})
                check_export()
                staged.replace(target)
            except Exception as exc:
                details = str(exc)
                if isinstance(exc, subprocess.TimeoutExpired):
                    details += "\n" + str(exc.stdout or "") + "\n" + str(exc.stderr or "")
                try:
                    atomic.write_text(stage / "error.log", details)
                except OSError:
                    pass  # A full disk must not hide the original failure or the preserved final output.
                raise HTTPException(500, f"ส่งออกไม่สำเร็จ · เก็บหลักฐานที่ {stage}: {exc}") from exc
            try:
                shutil.rmtree(stage)
            except OSError as exc:
                cleanup_warning = f"ไฟล์ส่งออกสำเร็จ แต่ล้างไฟล์พักไม่ได้: {stage}: {exc}"
    except hf_build.BuildBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"file": str(target), "delivery_manifest": str(evidence), "seconds": round(_t.time() - t0, 1), **({"warning": cleanup_warning} if cleanup_warning else {})}
