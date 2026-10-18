#!/usr/bin/env python3
"""ClipKit Video -> CapCut. Local only: http://127.0.0.1:8770/video-editor.html"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

if sys.stdout:  # a Thai project name in a log line would stop the Autosave thread on a cp874/cp1252 console
    sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402

# first run on a new machine: create config.json from the example (same as setup.ps1) so the app can open
# and the Settings page can show what is still missing; the folders stay unset until the person picks them
_CFG = HERE.parent / "config.json"
if not _CFG.is_file():
    import shutil
    shutil.copyfile(HERE.parent / "config.example.json", _CFG)

# Windows: every helper the app starts (ffmpeg, npx, powershell, python) runs without a console window,
# otherwise a black terminal flashes up for each cover, motion render, check or file dialog
if os.name == "nt":
    import subprocess
    _Popen_init = subprocess.Popen.__init__

    def _no_window(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0)
        if not flags & subprocess.CREATE_NEW_CONSOLE:   # a window asked for on purpose (sign-in) stays visible
            flags |= subprocess.CREATE_NO_WINDOW
        kwargs["creationflags"] = flags
        _Popen_init(self, *args, **kwargs)
    subprocess.Popen.__init__ = _no_window

import kit_settings  # noqa: E402
import style_lab_api  # noqa: E402
import video_editor  # noqa: E402
import project_lock  # noqa: E402
import team_hub  # noqa: E402
import team_workspace  # noqa: E402
import drive  # noqa: E402
from window_lifecycle import window_router  # noqa: E402

PORT = int(os.environ.get("VIDEO_EDITOR_PORT", "8770"))
# Optional local helper (the MoonRacle dashboard and bot): config.json "bot_api_url", empty = off. It may embed
# these pages and restart a hung server; without it nothing outside this app is trusted.
HELPER_URL = str(kit_settings._read_config().get("bot_api_url") or "").rstrip("/")


def _widget_origins(url: str) -> tuple[str, ...]:
    if not url:
        return ()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f'config.json "bot_api_url" must be an http(s) URL, got {url!r}')
    port = f":{parts.port}" if parts.port else ""
    hosts = {"localhost", "127.0.0.1"} if parts.hostname in ("localhost", "127.0.0.1") else {parts.hostname}
    return tuple(sorted(f"{parts.scheme}://{h}{port}" for h in hosts))


WIDGET_ORIGINS = _widget_origins(HELPER_URL)


def jobs_running() -> bool:
    if project_lock.busy():
        return True
    if any(job.get("status") == "running" for job in list(kit_settings._jobs.values())):
        return True
    if any(proc.poll() is None for proc in list(video_editor._transcribe_jobs.values())):
        return True
    return any(job.get("status") == "running" for job in list(video_editor._story_jobs.values()))


def exit_server():
    app.state.exit_requested = True
    app.state.server.should_exit = True

app = FastAPI(title="EditorKrab", version="1.0.0")
app.state.exit_requested = False
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"], www_redirect=False)
app.include_router(video_editor.router)
app.include_router(kit_settings.router)
app.include_router(style_lab_api.router)
app.include_router(team_hub.router)
app.include_router(team_workspace.router)
app.include_router(drive.router)
app.include_router(window_router(PORT, exit_server, jobs_running, lambda: not app.state.exit_requested,
                                 helper_url=HELPER_URL))


@app.exception_handler(Exception)
async def report_crash(request, exc: Exception) -> JSONResponse:
    """Return the error to the local caller without sending diagnostics to an external service."""
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


@app.middleware("http")
async def no_cache_html(request, call_next):
    # The local dashboard may embed pages, read readiness and use explicit host controls.
    origin = request.headers.get("origin")
    try:
        referrer = urlsplit(request.headers.get("referer", ""))
    except ValueError:
        return JSONResponse({"detail": "Invalid referrer"}, status_code=400)
    referrer_origin = f"{referrer.scheme}://{referrer.netloc}"
    is_page = request.url.path.endswith(".html") or request.url.path == "/"
    widget_navigation = (request.method == "GET" and is_page
                         and request.headers.get("sec-fetch-dest") == "iframe"
                         and request.headers.get("sec-fetch-mode") == "navigate"
                         and referrer_origin in WIDGET_ORIGINS
                         and (origin is None or origin in WIDGET_ORIGINS))
    widget_health = (request.method == "GET" and request.url.path == "/api/window/health"
                     and origin in WIDGET_ORIGINS)
    widget_control = (request.method == "POST" and request.url.path in
                      {"/api/window/restart", "/api/window/shutdown", "/api/window/cancel-shutdown"} and origin in WIDGET_ORIGINS)
    foreign = ((origin is not None and origin != f"{request.url.scheme}://{request.url.netloc}")
               or request.headers.get("sec-fetch-site") == "cross-site")
    oauth_return = (request.method == "GET" and request.url.path == "/api/team/google/callback"
                    and request.headers.get("sec-fetch-mode") == "navigate"
                    and request.headers.get("sec-fetch-dest") == "document")
    # OAuth redirects retain cross-site Fetch Metadata through the landing page.
    page_navigation = (request.method == "GET" and is_page
                       and request.headers.get("sec-fetch-mode") == "navigate"
                       and request.headers.get("sec-fetch-dest") == "document"
                       and (origin is None or origin == f"{request.url.scheme}://{request.url.netloc}"))
    if foreign and not (widget_navigation or widget_health or widget_control or oauth_return or page_navigation):
        return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)
    # Gate pages, files and editing APIs before they can read local projects.
    public = request.url.path in {
        '/login.html', '/login.css', '/login.js', '/logo.svg', '/logo-256.png', '/editorkrab.ico', '/ui.js', '/i18n/en.json',
        '/api/team/session', '/api/team/google', '/api/team/google/callback',
        '/api/team/logout',
    } or request.url.path.startswith('/api/window/')
    if not public:
        from fastapi import HTTPException
        from starlette.concurrency import run_in_threadpool
        try:
            await run_in_threadpool(team_hub.session, request)
        except HTTPException as exc:
            if is_page and exc.status_code == 401:
                return RedirectResponse('/login.html', status_code=303)
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                                headers={'Cache-Control': 'no-store'})
    if request.method=='GET' and os.path.normpath('/'+request.url.path.replace('\\','/').lstrip('/')).replace('\\','/').lower()=='/review.html':
        from fastapi import HTTPException
        from starlette.concurrency import run_in_threadpool
        try: await run_in_threadpool(team_workspace.require_review_access,request)
        except HTTPException as exc:
            if exc.status_code==401:return RedirectResponse('/team.html',status_code=303)
            if exc.status_code==403:return HTMLResponse('<!doctype html><html lang="th"><meta charset="utf-8"><link rel="stylesheet" href="/team.css"><main><h1>เริ่มที่หน้าทีมตัดต่อ</h1><p>ถ้ายังไม่มีทีม สร้างทีมแล้วคุณจะเป็นผู้ดูแล</p><p>ถ้าอยู่ในทีมแล้ว ให้ผู้ดูแลเพิ่มสิทธิ์ตรวจงาน</p><a href="/team.html">เปิดหน้าทีมตัดต่อ</a></main></html>',status_code=403)
            return JSONResponse({'detail':exc.detail},status_code=exc.status_code)
    if request.url.path.startswith('/api/') and not request.url.path.startswith('/api/team/'):
        from fastapi import HTTPException
        from starlette.concurrency import run_in_threadpool
        values = dict(request.query_params)
        if request.url.path.startswith('/api/video/hfp/'):
            import base64
            key=request.url.path.split('/')[4]
            try: values['preview_path']=base64.urlsafe_b64decode(key+'='*(-len(key)%4)).decode('utf-8')
            except (ValueError,UnicodeError): return JSONResponse({'detail':'Invalid preview path'},status_code=400)
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and request.headers.get('content-type', '').startswith('application/json') and await request.body():
            try: values['body'] = await request.json()
            except ValueError: return JSONResponse({'detail':'Invalid JSON'},status_code=400)
        try: await run_in_threadpool(team_workspace.guard_paths, values, request)
        except HTTPException as exc: return JSONResponse({'detail':exc.detail},status_code=exc.status_code)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self'" + (" " + " ".join(WIDGET_ORIGINS) if is_page and WIDGET_ORIGINS else "")
    if widget_health or widget_control:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    if is_page:
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    else:
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/video-editor.html")


# motion / cover templates, so the cover page can show a live preview of the real template
app.mount("/motion", StaticFiles(directory=str(HERE.parent / "motion"), html=True), name="motion")
app.mount("/ckfonts", StaticFiles(directory=str(HERE.parent / "fonts")), name="ckfonts")  # Kanit for the live player
app.mount("/", StaticFiles(directory=str(HERE / "static"), html=True), name="static")


def _watch_drafts() -> None:
    """ClipKit - Autosave: record every project CapCut saves (capcut_edit.watch), in a thread, on every machine.
    Stays on this machine; config.json "autosave": false turns it off."""
    import threading
    import capcut_edit
    cfg = kit_settings._read_config()
    if cfg.get("autosave", True) and cfg.get("capcut_drafts"):
        threading.Thread(target=capcut_edit.watch, args=(cfg["capcut_drafts"],), daemon=True).start()
        threading.Thread(target=_profile_daily, daemon=True).start()


def _profile_daily() -> None:
    """Remake the Autosave style profile (and presets/my-style.json) once a day; checked every hour."""
    import time
    sys.path.insert(0, str(HERE.parent / "scripts"))
    import autosave_profile
    while True:
        if kit_settings._read_config().get("output_dir"):
            autosave_profile.refresh()
        time.sleep(3600)


if __name__ == "__main__":
    _watch_drafts()
    if sys.stdout:  # pythonw (autostart) has no console
        print(f"Video -> CapCut: http://127.0.0.1:{PORT}/video-editor.html")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="info",
                           log_config=None if sys.stderr is None else uvicorn.config.LOGGING_CONFIG,
                           timeout_graceful_shutdown=10))
    app.state.server = server
    server.run()
