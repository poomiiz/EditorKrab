"""Settings API for the ClipKit app: machine check, setup, config folders, update.

Local tool only (the app binds 127.0.0.1). Long jobs (setup, update, login) run in the background
and report through GET /api/kit/job/{name}.
"""
from __future__ import annotations

import atomic
from contextlib import contextmanager
from contextvars import ContextVar
import host_platform
import jobstore
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

KIT = Path(__file__).resolve().parents[1]
CONFIG = KIT / "config.json"
EXAMPLE = KIT / "config.example.json"

# keys the settings page may edit; everything else in config.json is kept untouched
FOLDER_KEYS = ["work_root", "capcut_drafts", "stock_video", "stock_music", "sfx", "output_dir", "models_dir", "capcut_resources", "fonts_dir"]
TEXT_KEYS = ["card_font", "whisper_device", "whisper_model", "bot_api_url", "workspace",
             "pixabay_key"]
# standard layout under one workspace folder: every machine in the team looks the same
WORKSPACE_LAYOUT = {"work_root": "footage", "stock_video": str(Path("stock") / "video"),
                    "stock_music": str(Path("stock") / "music"),
                    "sfx": "sfx", "output_dir": "output", "models_dir": "models"}


def capcut_default_drafts() -> str:
    """Where CapCut keeps this user's projects, or '' if CapCut was never opened.
    1. the folder chosen in CapCut's settings (Config/globalSetting currentCustomDraftPath)
    2. the folder most of CapCut's listed projects sit in (root_meta_info.json)
    3. CapCut's default folder"""
    import os
    import re
    from collections import Counter
    cfg = _read_config()
    data = host_platform.capcut_data_root(cfg)
    setting = data / "Config" / "globalSetting"
    if setting.is_file():
        m = re.search(r"^currentCustomDraftPath=(.+)$", setting.read_text(encoding="utf-8", errors="replace"), re.M)
        custom = Path(m.group(1).strip().replace("\\\\", "\\")) if m else None
        if custom and custom.is_dir():
            return str(custom)
    default = data / "Projects" / "com.lveditor.draft"
    meta = host_platform.capcut_registry(cfg)
    if meta.is_file():
        try:
            store = json.loads(meta.read_text(encoding="utf-8")).get("all_draft_store") or []
        except (OSError, ValueError):
            store = []
        parents = Counter(str(Path(d["draft_fold_path"]).parent) for d in store if d.get("draft_fold_path"))
        for folder, _ in parents.most_common():
            if Path(folder).is_dir():
                return folder
    return str(default) if default.is_dir() else ""

router = APIRouter(prefix="/api/kit", tags=["kit-settings"])
_jobs = jobstore.JobStore("kit")


def _read_config() -> dict[str, Any]:
    src = CONFIG if CONFIG.is_file() else EXAMPLE
    return json.loads(src.read_text(encoding="utf-8-sig"))


def _run_job(name: str, cmd: list[str], cwd: Path = KIT) -> None:
    job = _jobs[name] = {"status": "running", "log": "", "started": time.time()}
    _jobs.save()
    try:
        p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            job["log"] += line
        p.wait()
        job["status"] = "done" if p.returncode == 0 else "failed"
        job["code"] = p.returncode
    except Exception as exc:  # report, never swallow
        job["status"] = "failed"
        job["log"] += f"\n{exc}"
    _jobs.save()


def _start(name: str, cmd: list[str]) -> dict[str, Any]:
    if _jobs.get(name, {}).get("status") == "running":
        raise HTTPException(409, f"{name} is already running")
    threading.Thread(target=_run_job, args=(name, cmd), daemon=True).start()
    return {"job": name, "status": "running"}


@router.get("/doctor")
def doctor(asr: bool = False) -> dict[str, Any]:
    """Run scripts/doctor.py and return one row per check."""
    cmd = [sys.executable, str(KIT / "scripts" / "doctor.py")] + (["--asr"] if asr else [])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=420 if asr else 120)
        out, code = p.stdout, p.returncode
    except subprocess.TimeoutExpired as exc:   # report the checks that finished and name the one that hung
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        done = [line[5:23].strip() for line in out.splitlines() if line[:4].strip() in ("OK", "FAIL", "WARN")]
        out += f"FAIL {'timeout':18} hung after '{done[-1] if done else 'start'}' for {exc.timeout:.0f} s\n"
        code = 1
    rows = []
    for line in out.splitlines():
        state, rest = line[:4].strip(), line[5:]
        if state in ("OK", "FAIL", "WARN"):
            rows.append({"state": state, "name": rest[:18].strip(), "detail": rest[18:].strip()})
    if not rows:
        raise HTTPException(500, f"doctor produced no result: {p.stderr.strip()[-400:]}")
    return {"ok": code == 0, "checks": rows}


@router.get("/config")
def get_config() -> dict[str, Any]:
    cfg = _read_config()
    return {"exists": CONFIG.is_file(), "folders": {k: cfg.get(k, "") for k in FOLDER_KEYS},
            "options": {k: cfg.get(k, "") for k in TEXT_KEYS}, "layout": WORKSPACE_LAYOUT}


class ConfigUpdate(BaseModel):
    values: dict[str, str]


@router.post("/config")
def save_config(body: ConfigUpdate) -> dict[str, Any]:
    cfg = _read_config()
    bad = [k for k in body.values if k not in FOLDER_KEYS + TEXT_KEYS]
    if bad:
        raise HTTPException(400, f"unknown setting: {', '.join(bad)}")
    missing = [f"{k}={v}" for k, v in body.values.items() if k in FOLDER_KEYS and v and not Path(v).is_dir()]
    if missing:
        raise HTTPException(400, "folder not found: " + "; ".join(missing))
    cfg.update({k: v for k, v in body.values.items()})
    atomic.write_text(CONFIG, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"saved": True, "note": "restart the app for scripts already running to pick up new paths"}


class Workspace(BaseModel):
    root: str


@router.post("/workspace")
def set_workspace(body: Workspace) -> dict[str, Any]:
    """Pick one workspace folder: create the standard subfolders and point every path at them."""
    root = Path(body.root.strip())
    if not body.root.strip() or not root.anchor:
        raise HTTPException(400, "choose a full folder path, e.g. D:\\ClipKit")
    if not Path(root.anchor).exists():
        raise HTTPException(400, f"drive not found: {root.anchor}")
    cfg = _read_config()
    paths = {k: str(root / sub) for k, sub in WORKSPACE_LAYOUT.items()}
    for p in paths.values():
        Path(p).mkdir(parents=True, exist_ok=True)
    cfg.update(paths)
    cfg["workspace"] = str(root)
    if not cfg.get("capcut_drafts") or not Path(cfg["capcut_drafts"]).is_dir():
        found = capcut_default_drafts()
        if found:
            cfg["capcut_drafts"] = found
    atomic.write_text(CONFIG, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"saved": True, "paths": paths, "capcut_drafts": cfg.get("capcut_drafts", "")}


class HyperframesRequest(BaseModel):
    file: str
    name: str
    start: float | None = None  # one story out of a long file
    end: float | None = None
    resolution: str = "portrait"  # portrait (footage zoomed to fill 9:16), landscape, square


HF_VERSION = "0.8.101"
HF_PORT = 3002
_hf_preview: dict[str, Any] = {}


def _start_preview(command: list[str], project: Path, env: dict[str, str], *, window_flags: bool = False) -> None:
    """Remember the exact preview process for scoped macOS cleanup."""
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if window_flags else {}
    command = host_platform.hyperframes_command(command, project, env)
    _hf_preview["proc"] = subprocess.Popen(command, cwd=str(project), env=env,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
    if host_platform.is_macos():
        _hf_preview["identity"] = host_platform.process_identity(_hf_preview["proc"].pid)


@router.post("/hyperframes")
def hyperframes_project(body: HyperframesRequest) -> dict[str, Any]:
    """Create a HyperFrames project from a raw clip under <output_dir>/hyperframes and open its Studio."""
    import re
    import shutil
    src = Path(body.file)
    if not src.is_file():
        raise HTTPException(400, f"file not found: {src}")
    if not shutil.which("npx"):
        raise HTTPException(400, "Node.js (npx) is not installed - run setup or install Node.js 22+")
    cfg = _read_config()
    base = cfg.get("output_dir") or cfg.get("work_root")
    if not base or not Path(base).is_dir():
        raise HTTPException(400, "set the workspace folder in Settings first")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", body.name).strip("-").lower() or "clip"
    root = Path(base) / "hyperframes"
    root.mkdir(parents=True, exist_ok=True)
    if body.resolution not in ("portrait", "landscape", "square"):
        raise HTTPException(400, f"unknown frame shape: {body.resolution}")
    if body.resolution != "portrait":  # each shape gets its own folder; the time range stays last
        slug = f"{slug}-{body.resolution}"
    if body.start is not None and body.end is not None:
        slug = f"{slug}-{int(body.start)}-{int(body.end)}"
    project = root / slug
    if not project.exists() and body.start is not None and body.end is not None:
        from video_edit import FFMPEG
        part = root / f"{slug}-source{src.suffix}"
        cut = subprocess.run([FFMPEG, "-y", "-ss", str(body.start), "-to", str(body.end), "-i", str(src),
                              "-c", "copy", str(part)], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=600)
        if cut.returncode != 0 or not part.is_file():
            raise HTTPException(500, "cutting the story failed: " + cut.stderr.strip()[-400:])
        src = part
    env = {**__import__("os").environ, "HYPERFRAMES_SKIP_SKILLS": "1"}
    npx = shutil.which("npx")
    if not project.exists():
        p = subprocess.run([npx, "--yes", f"hyperframes@{HF_VERSION}", "init", slug, "--example", "blank",
                            "--video", str(src), "--resolution", body.resolution, "--skip-transcribe", "--non-interactive"],
                           cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.returncode != 0 or not (project / "index.html").is_file():
            raise HTTPException(500, "hyperframes init failed: " + (p.stderr or p.stdout).strip()[-600:])
        if src.name.endswith("-source" + src.suffix) and src.parent == root:
            src.unlink()  # init copied the cut into the project; the loose copy would outlive a deleted project
    atomic.write_text((project / "clipkit.json"), json.dumps({"raw": body.file, "start": body.start, "end": body.end},
                                                     ensure_ascii=False), encoding="utf-8")
    _stop_studio()
    _start_preview(
        [npx, "--yes", f"hyperframes@{HF_VERSION}", "preview", "--port", str(HF_PORT), "--no-open"],
        project, env, window_flags=True)
    import urllib.request
    for _ in range(60):  # wait until the studio answers
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{HF_PORT}/", timeout=1)
            break
        except Exception:
            time.sleep(1)
    else:
        raise HTTPException(500, f"HyperFrames studio did not start on port {HF_PORT}")
    return {"project": str(project), "url": f"http://localhost:{HF_PORT}/#project/{slug}"}


class CoverRequest(BaseModel):
    source: str            # an image, or a video (a frame is taken at `at` seconds)
    at: float = 1.0
    l1: str
    l2: str = ""
    c1: str = "#ff7d00"
    s1: str = "#ffffff"
    c2: str = "#ffffff"
    s2: str = "#111111"
    font: str = ""
    layout: str = "cover-a"


@router.post("/cover")
def make_cover(body: CoverRequest) -> dict[str, Any]:
    """Render a 1080x1920 cover PNG from a motion/cover-* template into <output_dir>/covers."""
    import re
    import shutil
    import tempfile
    src = Path(body.source)
    if not src.is_file():
        raise HTTPException(400, f"file not found: {src}")
    tpl = KIT / "motion" / body.layout
    if not (tpl / "index.html").is_file() or not body.layout.startswith("cover-"):
        raise HTTPException(400, f"unknown cover layout: {body.layout}")
    if not body.l1.strip():
        raise HTTPException(400, "headline (l1) is empty")
    npx = shutil.which("npx")
    if not npx:
        raise HTTPException(400, "Node.js (npx) is not installed - run setup")
    cfg = _read_config()
    out_dir = Path(cfg.get("output_dir") or cfg.get("work_root") or "") / "covers"
    if not out_dir.parent.is_dir():
        raise HTTPException(400, "set the workspace folder in Settings first")
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="clipkit_cover_"))
    shutil.copytree(tpl, work, dirs_exist_ok=True, ignore=shutil.ignore_patterns("preview-bg*"))
    bg = work / "preview-bg.jpg"
    if src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", str(bg)]
    else:
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(body.at), "-i", str(src), "-frames:v", "1",
               "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", str(bg)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if p.returncode != 0 or not bg.is_file():
        raise HTTPException(500, "could not read the background: " + p.stderr.strip()[-300:])
    page = (work / "index.html").read_text(encoding="utf-8")
    bad = [v for v in (body.c1, body.s1, body.c2, body.s2) if not re.fullmatch(r"#[0-9a-fA-F]{6}", v)]
    if bad:
        raise HTTPException(400, f"colour must look like #ff7d00: {bad}")
    cover = json.dumps({"l1": body.l1, "l2": body.l2, "bg": "preview-bg.jpg",
                        "c1": body.c1, "s1": body.s1, "c2": body.c2, "s2": body.s2, "font": body.font}, ensure_ascii=False)
    page, n = re.subn(r"const COVER = \{.*?\};", lambda _: f"const COVER = {cover};", page, count=1, flags=re.S)  # COVER spans several lines in the template
    if not n:
        raise HTTPException(500, "template has no COVER line")
    atomic.write_text((work / "index.html"), page, encoding="utf-8")
    frames = work / "_frames"
    env = {**__import__("os").environ, "HYPERFRAMES_SKIP_SKILLS": "1"}
    command = host_platform.hyperframes_command(
        [npx, "--yes", f"hyperframes@{HF_VERSION}", "render", "--format", "png-sequence", "--quiet", "-o", str(frames)], work, env)
    r = subprocess.run(command, cwd=str(work), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=600)
    first = sorted(frames.glob("*.png"))[:1] if frames.is_dir() else []
    if r.returncode != 0 or not first:
        raise HTTPException(500, "cover render failed: " + (r.stderr or r.stdout).strip()[-500:])
    name = re.sub(r'[\\/:*?"<>|]+', "", body.l1).strip()[:40] or "cover"
    target = out_dir / f"{name}.png"
    shutil.copy2(first[0], target)
    shutil.rmtree(work, ignore_errors=True)
    return {"file": str(target)}


def motion_templates() -> dict[str, dict[str, Any]]:
    """Every motion/<name>/index.html that carries a <script id="clipkit-motion"> spec (label, fields, length).
    A new effect pushed to GitHub shows up on the motion page after 'update', with no code change."""
    import re
    out = {}
    for page in sorted((KIT / "motion").glob("*/index.html")):
        m = re.search(r'<script type="application/json" id="clipkit-motion">(.*?)</script>',
                      page.read_text(encoding="utf-8"), re.S)
        if not m:
            continue   # covers and work-in-progress templates
        try:
            spec = json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            raise HTTPException(500, f"motion/{page.parent.name}: bad clipkit-motion JSON: {exc}")
        if spec.get("status", "ready") != "ready":
            continue
        out[page.parent.name] = spec
    return out


@router.get("/library")
def library() -> dict[str, Any]:
    """Everything the motion library page shows: our own templates (ready to use) and the sorted HyperFrames
    registry (motion/library/hyperframes.json, rebuilt by scripts/catalog_hf.py)."""
    ours = [{"name": k, "title": v.get("label", k), "role": v.get("role", "ข้อความ"), "mood": v.get("mood", ""),
             "source": "clipkit", "bg": (KIT / "motion" / k / "preview-bg.jpg").is_file()}
            for k, v in motion_templates().items()]
    ours += [{"name": d.name, "title": "ปก " + d.name.split("-")[-1].upper(), "role": "ปก", "mood": "", "source": "clipkit"}
             for d in sorted((KIT / "motion").glob("cover-*")) if (d / "index.html").is_file()]
    hf_file = KIT / "motion" / "library" / "hyperframes.json"
    hf = json.loads(hf_file.read_text(encoding="utf-8")) if hf_file.is_file() else []
    # subtitle styles: the person's own (my-*, local only) and the team's (team-*), each with its GIF preview
    from urllib.parse import quote
    import style_lab_api
    col = style_lab_api.style_lab.collection()
    styles = [{"name": s["name"], "title": s["name"].split("-", 1)[1], "description": s["about"], "role": "ซับ",
               "mood": "", "source": "style", "own": s["name"].startswith("my-"),
               "gif": "/api/style/file?path=" + quote(s["gif"]) if s["gif"] else ""}
              for s in col["presets"] + col["team"]]
    hidden = set(_read_config().get("library_hidden") or [])
    keep = lambda rows: [r for r in rows if f'{r.get("source", "hyperframes")}:{r["name"]}' not in hidden]  # noqa: E731
    # making new templates is the owner's job: only a machine whose config.json has "creator": true
    # (set by hand, not from the Settings page) sees the create buttons
    return {"ours": keep(ours), "styles": keep(styles), "hyperframes": keep(hf), "hidden": len(hidden),
            "can_create": bool(_read_config().get("creator"))}


class Hide(BaseModel):
    keys: list[str]        # "<source>:<name>"; empty with restore=True brings every hidden item back
    restore: bool = False


@router.post("/library/hide")
def library_hide(body: Hide) -> dict[str, Any]:
    """Take items off this machine's library page (repo items come back with every update, so they are hidden,
    not deleted); restore=True with no keys shows them all again."""
    cfg = _read_config()
    hidden = set(cfg.get("library_hidden") or [])
    hidden = set() if body.restore and not body.keys else hidden - set(body.keys) if body.restore else hidden | set(body.keys)
    cfg["library_hidden"] = sorted(hidden)
    atomic.write_text(CONFIG, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"hidden": len(hidden)}


@router.get("/motion-templates")
def list_motion_templates() -> dict[str, Any]:
    return {"templates": motion_templates()}


class MotionRequest(BaseModel):
    template: str
    name: str              # output file name (no extension)
    params: dict[str, Any]
    width: int | None = Field(default=None, ge=1, le=8192, strict=True)
    height: int | None = Field(default=None, ge=1, le=8192, strict=True)
    placement: dict[str, Any] | None = None


_motion_destination = ContextVar("motion_destination", default=None)


@contextmanager
def motion_destination(directory: Path):
    """AI transactions own their new assets; never render over an existing project's MOV."""
    token = _motion_destination.set(directory)
    try:
        yield
    finally:
        _motion_destination.reset(token)


def sentence_pair_params(params: dict[str, Any]) -> dict[str, Any]:
    """Reject invented clocks before starting a renderer; measured project events are the preferred input."""
    import math
    import render
    import subtitle_plan
    from video_edit import VideoEditError
    out = dict(params)
    try:
        W, H = out.get("width", 1080), out.get("height", 1920)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 < v <= 8192 or int(v) != v for v in (W, H)):
            raise VideoEditError("sentence-pair: canvas width/height ไม่ถูกต้อง")
        events = out.get("subtitle_events")
        if events is None:
            pairs, clocks = out.get("pairs"), out.get("word_times")
            if not isinstance(pairs, list) or not pairs:
                raise VideoEditError("sentence-pair: เลือกช่วงซับจริงจาก Timeline ก่อนสร้าง motion")
            if not isinstance(clocks, (list, dict)):
                raise VideoEditError("sentence-pair: ไม่มีเวลาระดับคำจริง · เลือกซับจาก Timeline หรือแนบ word_times ก่อนสร้าง")
            family, font = render._font(out.get("font"))
            look = render.pair_look({}, int(W), int(H))
            scale = render.CAPCUT_PX * min(W, H) / 1080
            for role, size, top in (("lead", "normal_size", "normal_top"), ("punch", "punch_size", "punch_top")):
                part = look[role]
                look[role] = ((float(out[size]) * H / 1920 / scale) if size in out else part[0],
                              (1 - 2 * float(out[top]) / 1920) if top in out else part[1], *part[2:])
            look["fixed_size"] = out.get("sizing") == "fixed"
            phrases = []
            for n, pair in enumerate(pairs):
                if not isinstance(pair, list) or len(pair) != 5:
                    raise VideoEditError(f"sentence-pair {n}: pairs ไม่ถูกต้อง")
                a, b, lead, punch, _ = pair
                words = clocks[n] if isinstance(clocks, list) and n < len(clocks) else clocks.get(str(n)) if isinstance(clocks, dict) else None
                phrases.extend(subtitle_plan.resolve(lead + " " + punch, [[lead, punch]], words,
                                                     cue_id=f"pair-{n}", start=a, end=b))
            events = render.pair_events(phrases, look, int(W), int(H), family, font)
        if not isinstance(events, list) or not events:
            raise VideoEditError("sentence-pair: ไม่มีข้อความที่แสดงในช่วงนี้")
        for event in events:
            if not isinstance(event, dict) or event.get("role") not in {"normal", "emphasis", "caption", "second"}:
                raise VideoEditError("sentence-pair: role event ไม่ถูกต้อง")
            keys = ("start", "end", "size_px", "center_x_px", "center_y_px", "rotation", "outline_px", "shadow")
            if any(isinstance(event.get(k), bool) or not isinstance(event.get(k), (int, float)) or not math.isfinite(event[k]) for k in keys):
                raise VideoEditError("sentence-pair: event ต้องเป็นตัวเลข finite")
            if not 0 <= event["start"] < event["end"] <= 120 or event["size_px"] <= 0 or event["outline_px"] < 0 or event["shadow"] < 0:
                raise VideoEditError("sentence-pair: ช่วงหรือขนาดข้อความไม่ถูกต้อง")
            if not isinstance(event.get("text"), str) or not event["text"].strip() or event.get("effect") not in {"none", "fade", "pop"}:
                raise VideoEditError("sentence-pair: ข้อความหรือ effect ไม่ถูกต้อง")
            for color in (event.get("fill"), event.get("stroke")):
                if not isinstance(color, list) or len(color) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in color):
                    raise VideoEditError("sentence-pair: สีข้อความไม่ถูกต้อง")
            font = Path(event.get("font_file") or "")
            if font.suffix.lower() not in {".ttf", ".otf", ".ttc"} or not font.is_file() or not isinstance(event.get("font_family"), str):
                raise VideoEditError("sentence-pair: ไม่พบฟอนต์ที่ใช้วัดข้อความ")
            if render._font(str(font))[0] != event["font_family"]:
                raise VideoEditError("sentence-pair: ชื่อฟอนต์ไม่ตรงกับไฟล์ที่ใช้วัด")
        events = render.check_pair_events(events, int(W), int(H))
        out.update(width=W, height=H, subtitle_events=events)
        return out
    except (VideoEditError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _motion_project(plan: dict, work: Path) -> Path:
    """Build only inside the caller's owned staging folder using the measured font bytes."""
    import re
    import shutil
    shutil.copytree(Path(plan["inputs"]["template"]).parent, work, dirs_exist_ok=True)
    page = (work / "index.html").read_text(encoding="utf-8")
    params = dict(plan["source_params"])
    if plan["layout"] is not None:
        params["layout"] = plan["layout"]
    kit = json.dumps(params, ensure_ascii=False).replace("</", "<\\/")
    script = f"<script>window.KIT = {kit};</script>"
    if plan["layout"] is not None:
        script += "<script>" + Path(plan["inputs"]["runtime"]).read_text(encoding="utf-8").replace("</", "<\\/") + "</script>"
    page, count = re.subn(r"<head>", lambda _: "<head>\n" + script, page, count=1)
    if count != 1:
        raise HTTPException(400, "motion template has no <head>")
    length = f'{plan["duration"]:.9f}'.rstrip("0").rstrip(".")
    page, count = re.subn(r'data-duration="[\d.]+"', f'data-duration="{length}"', page)
    if not count:
        raise HTTPException(400, "motion template has no duration")
    W, H = plan["canvas"]["width"], plan["canvas"]["height"]
    page = re.sub(r'<meta name="viewport"[^>]*>', f'<meta name="viewport" content="width={W}, height={H}">', page)
    page = re.sub(r'data-width="[\d.]+"', f'data-width="{W}"', page)
    page = re.sub(r'data-height="[\d.]+"', f'data-height="{H}"', page)
    fonts = (list(dict.fromkeys((e["font_family"], e["font_file"]) for e in params["subtitle_events"]))
             if plan["layout"] is None else [(plan["layout"]["font_face"], plan["inputs"]["font"])])
    rules = [f'html,body{{width:{W}px;height:{H}px;font-synthesis:none}}']
    for index, (family, source) in enumerate(fonts):
        font = Path(source)
        filename = f'{"subtitle" if plan["layout"] is None else "motion"}-font-{index}{font.suffix}'
        shutil.copyfile(font, work / filename)
        rules.append(f'@font-face{{font-family:{json.dumps(family)};src:url("{filename}");font-weight:700;font-style:normal}}')
    page = page.replace("</head>", "<style>" + "".join(rules).replace("</", "<\\/") + "</style></head>", 1)
    atomic.write_text(work / "index.html", page, encoding="utf-8")
    return work / "index.html"


@router.post("/motion")
def make_motion(body: MotionRequest) -> dict[str, Any]:
    """Validate, check and decode a new exact-canvas motion revision before publishing it."""
    import re
    import shutil
    import tempfile
    import motion_plan
    import render_process
    from video_edit import VideoEditError
    if body.template not in motion_templates():
        raise HTTPException(400, f"unknown motion template: {body.template}")
    try:
        plan = motion_plan.prepare_template(body.template, body.params, width=body.width,
                                            height=body.height, placement=body.placement)
        if not .5 <= plan["duration"] <= 120:
            raise VideoEditError("motion duration must be0.5-120seconds")
    except VideoEditError as exc:
        raise HTTPException(400, str(exc)) from exc
    npx = shutil.which("npx")
    if not npx:
        raise HTTPException(400, "Node.js (npx) is not installed - run setup")
    cfg = _read_config()
    private = _motion_destination.get()
    out_dir = private if private is not None else Path(cfg.get("output_dir") or cfg.get("work_root") or "") / "motion"
    if private is None and not out_dir.parent.is_dir():
        raise HTTPException(400, "set the workspace folder in Settings first")
    out_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[\\/:*?"<>|]+', "", body.name).strip()[:40] or body.template
    target = out_dir / f"{name}-{__import__('uuid').uuid4().hex}.mov"
    work = Path(tempfile.mkdtemp(prefix="clipkit_motion_"))
    try:
        _motion_project(plan, work)
        env = {**__import__("os").environ, "HYPERFRAMES_SKIP_SKILLS": "1", "HYPERFRAMES_NO_TELEMETRY": "1"}
        length = plan["duration"]
        times = sorted({0, min(.04, length / 10), length * .15, length * .35, length * .65, max(0, length - 1 / 30)})
        command = host_platform.hyperframes_command([npx, "--yes", f"hyperframes@{HF_VERSION}", "check", "--json",
                    "--at", ",".join(f"{t:.6f}" for t in times), "--at-transitions"], work, env)
        checked = render_process.run(command, cwd=str(work), capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", env=env, timeout=300)
        try:
            proof = json.loads(checked.stdout)
        except (ValueError, TypeError) as exc:
            raise HTTPException(500, "motion check returned no valid report: " + (checked.stderr or checked.stdout)[-600:]) from exc
        if checked.returncode or not isinstance(proof, dict) or proof.get("ok") is not True:
            raise HTTPException(400, "motion check failed before encoding: " + (checked.stdout or checked.stderr)[-800:])
        motion_plan.verify_inputs(plan)
        command = host_platform.hyperframes_command([npx, "--yes", f"hyperframes@{HF_VERSION}", "render",
                    "--format", "mov", "--quiet", "--workers", "4", "-o", str(target)], work, env)
        rendered = render_process.run(command, cwd=str(work), capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", env=env, timeout=900)
        if rendered.returncode or not target.is_file():
            raise HTTPException(500, "motion render failed: " + (rendered.stderr or rendered.stdout)[-600:])
        media = motion_plan.verify_media(target, plan)
        motion_plan.verify_inputs(plan)
        recipe = {"version": 1, "template": body.template, "params": plan["source_params"], "inputs": plan["inputs"],
                  "hashes": plan["hashes"], "layout_sha256": plan["layout_sha256"], "canvas": plan["canvas"],
                  "placement": plan["placement"], "bounds": plan["bounds"], "duration": plan["duration"], "media": media}
        return {"file": str(target), "recipe": recipe, "check": proof}
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work)


class FramesRequest(BaseModel):
    source: str
    count: int = 6


_FRAME_DIR = Path(__import__("tempfile").gettempdir()) / "clipkit_cover_frames"


@router.post("/cover-frames")
def cover_frames(body: FramesRequest) -> dict[str, Any]:
    """Suggest cover frames: sample the clip, score each frame (sharp + bright enough + a face, bigger is better),
    return the best `count` spread across the clip."""
    import shutil
    try:
        import cv2
    except ImportError as exc:
        raise HTTPException(400, "opencv-python is not installed - run setup") from exc
    src = Path(body.source)
    if not src.is_file():
        raise HTTPException(400, f"file not found: {src}")
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        raise HTTPException(400, "cannot read this video")
    face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    samples = min(48, max(body.count * 4, total // int(fps)))  # about one per second, at most 48
    cands = []
    for i in range(samples):
        idx = int((i + 0.5) * total / samples)
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        small = cv2.resize(frame, (360, int(360 * frame.shape[0] / frame.shape[1])))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        sharp = cv2.Laplacian(gray, cv2.CV_64F).var()
        bright = gray.mean()
        faces = face.detectMultiScale(gray, 1.15, 5, minSize=(40, 40))
        area = max((w * h for (_x, _y, w, h) in faces), default=0) / (gray.shape[0] * gray.shape[1])
        score = sharp * (1.0 if 60 < bright < 200 else 0.4) * (1 + 20 * area) * (1.0 if len(faces) else 0.5)
        cands.append((score, idx / fps, small))
    cap.release()
    if not cands:
        raise HTTPException(400, "no readable frames in this video")
    # best first, but at least ~1.5 s apart so the choices are different moments
    chosen = []
    for c in sorted(cands, key=lambda c: -c[0]):
        if all(abs(c[1] - k[1]) >= 1.5 for k in chosen):
            chosen.append(c)
        if len(chosen) == body.count:
            break
    shutil.rmtree(_FRAME_DIR, ignore_errors=True)
    _FRAME_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for n, (_s, t, img) in enumerate(sorted(chosen, key=lambda c: c[1])):
        f = _FRAME_DIR / f"f{n}_{t:.2f}.jpg"
        cv2.imwrite(str(f), img)
        out.append({"at": round(t, 2), "thumb": f"/api/kit/cover-frame/{f.name}"})
    return {"frames": out}


@router.get("/frame")
def frame(source: str, at: float = 0.0):
    """One 1080x1920 JPEG from a video (or the image itself) for the live cover preview."""
    from fastapi.responses import Response
    src = Path(source)
    if not src.is_file():
        raise HTTPException(404, "file not found")
    seek = [] if src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") else ["-ss", str(max(at, 0))]
    p = subprocess.run(["ffmpeg", "-v", "error", *seek, "-i", str(src), "-frames:v", "1",
                        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                        "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "pipe:1"], capture_output=True, timeout=60)
    if p.returncode != 0 or not p.stdout:
        raise HTTPException(500, "could not read a frame: " + p.stderr.decode(errors="replace")[-200:])
    return Response(p.stdout, media_type="image/jpeg")


VIDEO_TYPES = {".mov": "video/quicktime", ".mp4": "video/mp4", ".m4v": "video/mp4", ".mkv": "video/x-matroska",
               ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
               ".aac": "audio/aac", ".flac": "audio/flac", ".png": "image/png", ".jpg": "image/jpeg"}  # audio too: music is auditioned on the review tab


@router.get("/media")
def media(path: str):
    """Play a local clip in the editor (FileResponse answers Range requests, so seeking works)."""
    from fastapi.responses import FileResponse
    p = Path(path)
    if p.suffix.lower() not in VIDEO_TYPES or not p.is_file():
        raise HTTPException(404, f"not a video file: {path}")
    return FileResponse(p, media_type=VIDEO_TYPES[p.suffix.lower()])


_RAW_THUMB_DIR = Path(__import__("tempfile").gettempdir()) / "clipkit_raw_thumbs"


@router.get("/raw-thumb")
def raw_thumb(path: str):
    """Small cover frame (1 s in) for a raw-file card in the sidebar, cached per file version."""
    import hashlib
    from fastapi.responses import FileResponse
    p = Path(path)
    if p.suffix.lower() not in VIDEO_TYPES or not p.is_file():
        raise HTTPException(404, f"not a video file: {path}")
    st = p.stat()
    key = hashlib.sha1(f"{p.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()
    out = _RAW_THUMB_DIR / f"{key}.jpg"
    if not out.is_file():
        _RAW_THUMB_DIR.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(p), "-frames:v", "1", "-vf", "scale=320:-2",
                            "-f", "image2", "-c:v", "mjpeg", "-q:v", "5", "pipe:1"], capture_output=True, timeout=60)
        if r.returncode != 0 or not r.stdout:
            raise HTTPException(500, "could not read a frame: " + r.stderr.decode(errors="replace")[-200:])
        tmp = out.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_bytes(r.stdout)
        tmp.replace(out)   # two cards asking at once never see half a file
    return FileResponse(str(out), media_type="image/jpeg")


@router.get("/cover-frame/{name}")
def cover_frame(name: str):
    from fastapi.responses import FileResponse
    p = _FRAME_DIR / Path(name).name
    if not p.is_file():
        raise HTTPException(404, "frame not found")
    return FileResponse(str(p))


@router.get("/cover-file")
def cover_file(path: str):
    """Serve a rendered cover so the page can show it (only files under a covers folder)."""
    from fastapi.responses import FileResponse
    p = Path(path)
    if p.parent.name != "covers" or p.suffix.lower() != ".png" or not p.is_file():
        raise HTTPException(404, "not a cover file")
    return FileResponse(str(p))


@router.get("/capcut-detect")
def capcut_detect() -> dict[str, str]:
    return {"path": capcut_default_drafts()}


@router.post("/browse")
def browse(start: str = "") -> dict[str, str]:
    """Folder pick (settings): same Windows Open dialog; a picked file means its folder."""
    r = pick(start, "folder") if host_platform.is_macos() else pick(start)
    return {"path": r["path"] if r["kind"] != "file" else str(Path(r["path"]).parent)}


@router.post("/browse-file")
def browse_file(start: str = "") -> dict[str, str]:
    """File pick (cover background): same Windows Open dialog; a folder is not an answer here."""
    r = pick(start, "file") if host_platform.is_macos() else pick(start)
    if r["kind"] == "folder":
        raise HTTPException(400, "เลือกไฟล์วิดีโอหรือรูป ไม่ใช่โฟลเดอร์")
    return {"path": r["path"]}


PICK_PS = r"""
# sharp text on scaled screens (otherwise Windows stretches a 96-dpi bitmap and it looks blurry)
Add-Type -Namespace W -Name U -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
[void][W.U]::SetProcessDPIAware()
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = 'เลือกไฟล์วิดีโอ หรือเข้าไปในโฟลเดอร์แล้วกด Open เพื่อเลือกทั้งโฟลเดอร์'
$d.Filter = 'วิดีโอ|*.mov;*.mp4;*.mkv;*.m4v;*.avi|ทุกไฟล์|*.*'
$d.CheckFileExists = $false; $d.ValidateNames = $false
$d.FileName = 'เลือกโฟลเดอร์นี้'
if ($args[0]) { $d.InitialDirectory = $args[0] }
# a background process may not take the foreground, so own the dialog with a tiny topmost window that is
# actually shown: the dialog then opens above the browser instead of behind it
$f = New-Object System.Windows.Forms.Form
$f.TopMost = $true; $f.ShowInTaskbar = $false; $f.FormBorderStyle = 'None'; $f.Size = New-Object System.Drawing.Size(1,1)
$f.StartPosition = 'CenterScreen'; $f.Opacity = 0
$f.Show(); $f.Activate()
if ($d.ShowDialog($f) -eq 'OK') { [Console]::OutputEncoding = [Text.Encoding]::UTF8; $d.FileName }
"""


@router.post("/pick")
def pick(start: str = "", kind: str = "both") -> dict[str, str]:
    """Windows' own Open dialog: pick a video file, or open a folder and press Open to take the whole folder
    (the classic 'file name = select this folder' trick, since Windows has no single file-or-folder dialog)."""
    if kind not in {"both", "file", "folder"}:
        raise HTTPException(400, "Invalid picker kind")
    if host_platform.is_macos():
        try:
            out = host_platform.pick_path(start, kind)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, str(exc)) from exc
    else:
        script = Path(tempfile_dir()) / "clipkit_pick.ps1"
        atomic.write_text(script, PICK_PS, encoding="utf-8-sig")   # BOM: Windows PowerShell reads Thai text correctly
        # CREATE_NO_WINDOW: no black console window behind the dialog (only the Open dialog shows)
        p = subprocess.run(["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                            "-File", str(script), start],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = p.stdout.strip()
    if not out:
        return {"path": "", "kind": ""}
    path = Path(out)
    if path.is_file():
        return {"path": str(path), "kind": "file"}
    folder = path if path.is_dir() else path.parent    # "เลือกโฟลเดอร์นี้" does not exist: its folder is the pick
    if not folder.is_dir():
        raise HTTPException(400, f"not found: {out}")
    return {"path": str(folder), "kind": "folder"}


def tempfile_dir() -> str:
    import tempfile
    return tempfile.gettempdir()


class LocateRequest(BaseModel):
    name: str
    size: int = -1      # bytes for a file, -1 for a folder


@router.post("/locate")
def locate(body: LocateRequest) -> dict[str, Any]:
    """Find a file/folder dropped onto the page. Browsers hand the page only the name and size, never the
    path, so look for an exact name (+ size) match under the workspace folders. Several matches or none = say so."""
    cfg = _read_config()
    roots = [Path(cfg[k]) for k in ("work_root", "output_dir", "workspace", "stock_video") if cfg.get(k)]
    roots += [Path(item["path"]) / "footage" for item in cfg.get("team_workspaces", {}).values() if item.get("path")]
    roots = [r for r in dict.fromkeys(roots) if r.is_dir()]
    if not roots:
        raise HTTPException(400, "set the workspace folder in Settings first")
    # Windows user folders people drag from (Desktop, Downloads...), searched only 2 levels deep:
    # Downloads can hold whole repos and a full walk there would take minutes
    home = Path.home()
    shallow = [home / d for d in ("Desktop", "Downloads", "Videos", "Documents") if (home / d).is_dir()]
    hits: list[str] = []
    for r in shallow:
        for pat in (body.name, "*/" + body.name, "*/*/" + body.name):
            for p in r.glob(pat):
                ok = p.is_dir() if body.size < 0 else (p.is_file() and p.stat().st_size == body.size)
                if ok and str(p) not in hits:
                    hits.append(str(p))
    for r in roots:
        for p in r.rglob(body.name):
            ok = p.is_dir() if body.size < 0 else (p.is_file() and p.stat().st_size == body.size)
            if ok and str(p) not in hits:
                hits.append(str(p))
            if len(hits) > 5:
                break
    if not hits:
        raise HTTPException(404, f"ไม่เจอ {body.name} ในโฟลเดอร์งาน, Desktop หรือ Downloads — กดช่องสร้างใหม่เพื่อเลือกไฟล์แทน")
    if len(hits) > 1:
        raise HTTPException(409, f"เจอ {body.name} หลายที่ ({len(hits)}) — กดปุ่มเลือกไฟล์แทน")
    p = Path(hits[0])
    return {"path": str(p), "kind": "folder" if p.is_dir() else "file"}


class DraftPath(BaseModel):
    path: str
    name: str = ""


def _project_roots() -> dict[str, Path]:
    cfg = _read_config()
    base = cfg.get("output_dir") or cfg.get("work_root") or ""
    return {"capcut": Path(cfg.get("capcut_drafts") or ""), "hyperframes": Path(base) / "hyperframes" if base else Path("")}


def _project(path: str) -> tuple[str, Path]:
    """Only folders directly inside the CapCut drafts folder or <output>/hyperframes may be opened, renamed or removed."""
    p = Path(path)
    for kind, root in _project_roots().items():
        marker = "draft_content.json" if kind == "capcut" else "index.html"
        if str(root) not in ("", ".") and root.is_dir() and p.resolve().parent == root.resolve() and (p / marker).is_file():
            return kind, p
    raise HTTPException(400, f"not a ClipKit project folder: {path}")


def _stop_studio() -> None:
    # npx starts node underneath: kill the whole tree, or the studio keeps the project files open
    old = _hf_preview.get("proc")
    if host_platform.is_macos():
        if old and old.poll() is None:
            host_platform.stop_owned_preview(old, _hf_preview.get("identity"))
        _hf_preview.pop("identity", None)
        _hf_preview.pop("proc", None)
        return
    if old and old.poll() is None:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(old.pid)], capture_output=True, timeout=60)
        old.wait(timeout=10)
    # studios left from earlier runs (the old code stopped only npx, so node kept running and kept the files
    # open): stop every HyperFrames preview process, nothing else
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { $_.CommandLine -match "
          "'hyperframes' -and $_.CommandLine -match ' preview ' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30)


VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}


def _raw_of(folder: Path, sources: list[str]) -> str:
    """The raw file a project came from: clipkit.json when ClipKit made it, else its biggest video source."""
    meta = folder / "clipkit.json"
    if meta.is_file():
        return json.loads(meta.read_text(encoding="utf-8"))["raw"]
    vids = [Path(s) for s in sources if Path(s).suffix.lower() in VIDEO_EXT and Path(s).is_file()]
    return str(max(vids, key=lambda p: p.stat().st_size)) if vids else ""


class BugReport(BaseModel):
    text: str
    page: str = ""
    errors: list[str] = []
    auto: bool = False   # sent by the app on its own when it hit an error, not typed by a person


def report_bug(text: str, page: str = "", errors: list[str] | None = None, auto: bool = False) -> dict[str, Any]:
    """Compatibility for old clients: never transmit diagnostics; return a manual GitHub draft."""
    import urllib.parse
    if auto:
        return {"sent": False, "skipped": True}
    if not text.strip():
        raise HTTPException(400, "เขียนอาการก่อน")
    url = "https://github.com/poomiiz/EditorKrab/issues/new?" + urllib.parse.urlencode(
        {"title": "[bug] EditorKrab", "body": text.strip()[:5000]})
    return {"sent": False, "issue_url": url}


@router.post("/bug-report")
def bug_report(body: BugReport) -> dict[str, Any]:
    return report_bug(body.text, body.page, body.errors, body.auto)


# display names people give raw files on the home page; the files themselves are never renamed
FOOTAGE_NAMES = KIT / "footage_names.json"


def _footage_names() -> dict[str, str]:
    return json.loads(FOOTAGE_NAMES.read_text(encoding="utf-8")) if FOOTAGE_NAMES.is_file() else {}


class FootageName(BaseModel):
    path: str
    name: str   # empty = back to the file name


@router.post("/footage-name")
def footage_name(body: FootageName) -> dict[str, str]:
    p = Path(body.path)
    if not p.is_file():
        raise HTTPException(404, f"file not found: {p}")
    names, key, name = _footage_names(), str(p).lower(), body.name.strip()
    if name:
        names[key] = name
    else:
        names.pop(key, None)
    atomic.write_text(FOOTAGE_NAMES, json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"name": name or p.stem}


@router.get("/raw-groups")
def raw_groups() -> dict[str, Any]:
    """Every project grouped under the raw file it was cut from (newest group first)."""
    import capcut_edit
    names = _footage_names()
    groups: dict[str, dict[str, Any]] = {}
    items = [("capcut", d) for d in capcut_edit.list_drafts()] + [("hyperframes", p) for p in hf_projects()["projects"]]
    for kind, d in items:
        raw = _raw_of(Path(d["path"]), d.get("sources") or [])
        key = str(Path(raw)).lower() if raw else ""
        g = groups.setdefault(key, {"raw": raw, "name": names.get(key) or Path(raw).stem if raw else "ไม่ทราบไฟล์ดิบ",
                                    "exists": bool(raw) and Path(raw).is_file(), "modified": 0, "projects": []})
        g["projects"].append({"kind": kind, "name": d["name"], "path": d["path"], "modified": d.get("modified") or 0})
        g["modified"] = max(g["modified"], d.get("modified") or 0)
    out = sorted(groups.values(), key=lambda g: (g["raw"] == "", -g["modified"]))
    for g in out:
        g["projects"].sort(key=lambda x: -x["modified"])
    return {"groups": out}


def _draft_dir(path: str) -> Path:
    return _project(path)[1]


DELIVERY_STATUSES = KIT / "delivery_statuses.json"
_delivery_lock = threading.RLock()


def _delivery_statuses() -> dict[str, Any]:
    return json.loads(DELIVERY_STATUSES.read_text(encoding="utf-8")) if DELIVERY_STATUSES.is_file() else {}


def _save_delivery_statuses(statuses: dict[str, Any]) -> None:
    temporary = DELIVERY_STATUSES.with_suffix(".json.tmp")
    atomic.write_text(temporary, json.dumps(statuses, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(DELIVERY_STATUSES)


DeliveryState = Literal["editing", "awaiting_review", "changes_requested", "approved"]


class DeliveryStatus(BaseModel):
    path: str
    status: DeliveryState


class BulkDeliveryStatus(BaseModel):
    paths: list[str] = Field(min_length=1)
    status: DeliveryState


def _set_delivery_status(paths: list[str], status: DeliveryState) -> dict[str, Any]:
    # Validate the entire selection before writing, so a stale project cannot leave a partial batch.
    keys = {str(_draft_dir(path).resolve()).lower() for path in paths}
    record = {"status": status, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    changed = dict.fromkeys(keys, record)
    with _delivery_lock:
        statuses = _delivery_statuses()
        statuses.update(changed)
        _save_delivery_statuses(statuses)
    return changed


@router.get("/delivery-status")
def delivery_statuses(request: Request) -> dict[str, Any]:
    import team_workspace
    with _delivery_lock:
        historical = _delivery_statuses()
    return {"statuses": team_workspace.delivery_view(request, historical)}


@router.post("/delivery-status")
def delivery_status(body: DeliveryStatus) -> dict[str, Any]:
    raise HTTPException(410, "สถานะส่งงานให้ใช้ระบบทีม คนตัดส่งหัวหน้าตรวจ และหัวหน้าบันทึกผลจากหน้าตรวจงาน")


@router.post("/delivery-status/bulk")
def bulk_delivery_status(body: BulkDeliveryStatus) -> dict[str, Any]:
    raise HTTPException(410, "เปลี่ยนสถานะหลายโปรเจกต์จากหน้าทีมหรือหน้าตรวจงาน")


@router.get("/hf-projects")
def hf_projects() -> dict[str, Any]:
    root = _project_roots()["hyperframes"]
    out = []
    if root.is_dir():
        for d in root.iterdir():
            if (d / "index.html").is_file():
                out.append({"name": d.name, "path": str(d), "modified": int((d / "index.html").stat().st_mtime)})
    return {"projects": sorted(out, key=lambda x: -x["modified"])}


def _capcut_running() -> bool:
    if host_platform.is_macos():
        return host_platform.capcut_running()
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq CapCut.exe", "/NH"], capture_output=True, text=True, timeout=60)
    return "CapCut.exe" in r.stdout


@router.post("/draft-rename")
def draft_rename(body: DraftPath) -> dict[str, str]:
    """Rename a project folder. CapCut keeps its own project list (root_meta_info.json), so CapCut must be closed
    and that list is updated too (a .bak copy is kept next to it)."""
    import re
    import shutil
    kind, p = _project(body.path)
    new = re.sub(r'[\\/:*?"<>|]+', " ", body.name).strip()
    if not new or new in (".", ".."):
        raise HTTPException(400, "ชื่อว่าง")
    target = p.parent / new
    if target.resolve().parent != p.parent.resolve():
        raise HTTPException(400, "ชื่อโปรเจกต์ใช้ไม่ได้")
    if target.exists():
        raise HTTPException(409, f"มีโปรเจกต์ชื่อ {new} อยู่แล้ว")
    if kind == "capcut":
        if _capcut_running():
            raise HTTPException(409, "ปิด CapCut ก่อนเปลี่ยนชื่อ (CapCut จะเขียนรายชื่อโปรเจกต์ทับ)")
        sys.path.insert(0, str(KIT / "scripts" / "capcut"))
        import kitconfig
        rm = Path(kitconfig.ROOT_META)
        p.rename(target)
        meta = target / "draft_meta_info.json"
        if meta.is_file():
            m = json.loads(meta.read_text(encoding="utf-8"))
            m["draft_name"] = new
            m["draft_fold_path"] = target.as_posix()
            atomic.write_text(meta, json.dumps(m, ensure_ascii=False), encoding="utf-8")
        if rm.is_file():
            shutil.copy2(rm, rm.with_suffix(".json.bak"))
            r = json.loads(rm.read_text(encoding="utf-8"))
            old_posix = p.as_posix()
            for e in r.get("all_draft_store", []):
                if Path(e.get("draft_fold_path", "")).resolve() == p.resolve():
                    e["draft_name"] = new
                    for k in ("draft_fold_path", "draft_json_file", "draft_cover"):
                        if isinstance(e.get(k), str):
                            e[k] = e[k].replace(old_posix, target.as_posix()).replace(str(p), str(target))
            atomic.write_text(rm, json.dumps(r, ensure_ascii=False), encoding="utf-8")
    else:
        p.rename(target)
    with _delivery_lock:
        statuses = _delivery_statuses()
        record = statuses.pop(str(p.resolve()).lower(), None)
        if record is not None:
            statuses[str(target.resolve()).lower()] = record
            _save_delivery_statuses(statuses)
    return {"path": str(target), "name": new}


@router.post("/draft-open")
def draft_open(body: DraftPath) -> dict[str, str]:
    """Bring up CapCut (it opens on its project list; CapCut has no command line to open one project)."""
    import os
    kind, p = _project(body.path)
    if kind == "hyperframes":
        return _open_hf_studio(p)
    if host_platform.is_macos():
        try:
            host_platform.open_capcut(_read_config())
        except FileNotFoundError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"opened": "CapCut"}
    exe = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "Apps" / "CapCut.exe"
    if not exe.is_file():
        raise HTTPException(400, f"CapCut not found at {exe}")
    subprocess.Popen([str(exe)])
    return {"opened": "CapCut"}


def _open_hf_studio(project: Path) -> dict[str, str]:
    import shutil
    import urllib.request
    npx = shutil.which("npx")
    if not npx:
        raise HTTPException(400, "Node.js (npx) is not installed - run setup")
    _stop_studio()
    env = {**__import__("os").environ, "HYPERFRAMES_SKIP_SKILLS": "1"}
    _start_preview([npx, "--yes", f"hyperframes@{HF_VERSION}", "preview", "--port", str(HF_PORT),
                    "--no-open", "--foreground"], project, env)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{HF_PORT}/", timeout=1)
            break
        except Exception:
            time.sleep(1)
    else:
        raise HTTPException(500, f"HyperFrames studio did not start on port {HF_PORT}")
    return {"opened": "hyperframes", "url": f"http://localhost:{HF_PORT}/#project/{project.name}"}


@router.post("/draft-delete")
def draft_delete(body: DraftPath) -> dict[str, str]:
    """Move one CapCut project folder to the Recycle Bin (restorable), never a permanent delete."""
    p = _draft_dir(body.path)
    import os
    if (p / "index.html").is_file():
        _stop_studio()
    if host_platform.is_macos():
        if not (p / "index.html").is_file() and _capcut_running():
            raise HTTPException(409, "ปิด CapCut ก่อนเปลี่ยนชื่อ (CapCut จะเขียนรายชื่อโปรเจกต์ทับ)")
        try:
            host_platform.move_to_trash(p)
        except (OSError, ValueError) as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"recycled": str(p)}
    ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
          "$env:CLIPKIT_TARGET, 'OnlyErrorDialogs', 'SendToRecycleBin')")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120, env={**os.environ, "CLIPKIT_TARGET": str(p)})
    if r.returncode != 0 or p.exists():
        err = (r.stderr or r.stdout).strip()
        if "IOException" in err or "being used" in err:
            raise HTTPException(409, f"ลบ {p.name} ไม่ได้ เพราะไฟล์ยังเปิดอยู่ (HyperFrames Studio หรือ CapCut) ปิดก่อนแล้วลองใหม่")
        raise HTTPException(500, "could not move to Recycle Bin: " + err[-300:])
    return {"recycled": str(p)}


@router.post("/setup")
def run_setup() -> dict[str, Any]:
    if host_platform.is_macos():
        workspace = _read_config().get("workspace")
        return _start("setup", ["/bin/bash", str(KIT / "scripts" / "setup.sh")] +
                      ([str(workspace)] if workspace else []))
    return _start("setup", ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(KIT / "scripts" / "setup.ps1")])


@router.post("/update")
def update() -> dict[str, Any]:
    if not (KIT / ".git").is_dir():
        raise HTTPException(400, "this copy is not a git clone - download a fresh copy from GitHub")
    return _start("update", [sys.executable, str(KIT / "scripts" / "update.py")])


@router.get("/job/{name}")
def job(name: str) -> dict[str, Any]:
    if name not in _jobs:
        return {"job": name, "status": "idle", "log": ""}
    j = _jobs[name]
    return {"job": name, "status": j["status"], "log": j["log"][-6000:]}


@router.get("/fonts")
def fonts() -> dict[str, Any]:
    """Font families installed on this machine (what the browser and the renderer can use by name)."""
    if host_platform.is_macos():
        try:
            names = host_platform.font_names(_read_config())
        except (OSError, ValueError) as exc:
            raise HTTPException(500, "could not read installed fonts: " + str(exc)[-200:]) from exc
        if not names:
            raise HTTPException(500, "could not read installed fonts")
        return {"fonts": names}
    ps = ("Add-Type -AssemblyName System.Drawing; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "(New-Object System.Drawing.Text.InstalledFontCollection).Families | ForEach-Object { $_.Name }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    names = sorted({n.strip() for n in r.stdout.splitlines() if n.strip()}, key=str.lower)
    if not names:
        raise HTTPException(500, "could not read installed fonts: " + r.stderr.strip()[-200:])
    return {"fonts": names}


@router.get("/update-check")
def update_check() -> dict[str, Any]:
    """Ask GitHub whether a newer ClipKit release exists (scripts/update.py --check)."""
    r = subprocess.run([sys.executable, str(KIT / "scripts" / "update.py"), "--check"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    if r.returncode != 0:
        return {"available": False, "error": (r.stderr or r.stdout or "update check failed").strip()[-200:]}
    u = json.loads(r.stdout)
    return {"available": u["count"] > 0, "count": u["count"], "changes": u["changes"], "release": u["release"]}


@router.get("/version")
def version() -> dict[str, Any]:
    meta = json.loads((KIT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    commit = subprocess.run(["git", "-C", str(KIT), "log", "-1", "--format=%h %cs"], capture_output=True,
                            text=True, timeout=60).stdout.strip()
    return {"version": meta.get("version"), "commit": commit}


# ── preset packs (.editorkrab): hand subtitle presets, covers and motion to the team as one file ──
class PackPath(BaseModel):
    path: str


class PackMake(BaseModel):
    id: str
    name: str
    author: str = ""
    tier: str = "team"
    subtitles: list[str] = []
    covers: list[str] = []
    motion: list[str] = []


def _pack(fn, *a):
    import preset_pack
    try:
        return fn(preset_pack, *a)
    except preset_pack.PackError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/pack/items")
def pack_items() -> dict[str, Any]:
    return _pack(lambda pp: {**pp.items(), "tiers": pp.TIERS})


@router.post("/pack/check")
def pack_check(body: PackPath) -> dict[str, Any]:
    return _pack(lambda pp: pp.read_pack(body.path))


@router.post("/pack/install")
def pack_install(body: PackPath) -> dict[str, Any]:
    return _pack(lambda pp: pp.install_pack(body.path))


@router.post("/pack/make")
def pack_make(body: PackMake) -> dict[str, Any]:
    """Write <output_dir>/packs/<id>.editorkrab."""
    cfg = _read_config()
    base = Path(cfg.get("output_dir") or "")
    if not cfg.get("output_dir") or not base.is_dir():
        raise HTTPException(400, "set the finished-files folder (output_dir) in Settings first")
    (base / "packs").mkdir(exist_ok=True)
    m = {"id": body.id, "name": body.name, "author": {"name": body.author}, "version": "1.0.0", "tier": body.tier,
         "subtitles": body.subtitles, "covers": body.covers, "motion": body.motion, "fonts": []}
    return {"path": str(_pack(lambda pp: pp.make_pack(base / "packs", m)))}
