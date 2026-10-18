"""Send a finished MP4 to Google Drive and hand back a share link, so a reviewer can watch it on a phone.

Uses the person's own Google account with the drive.file scope (EditorKrab only sees the files it uploads).
config.json holds the OAuth client of a Google Cloud "Desktop app" (google_drive_client_id / _secret); the
refresh token stays on this machine in state/google-drive.json. Sign-in returns to a one-shot loopback server
on a free port, so the app's own login gate is never opened for it.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import atomic
import jobstore
import kit_settings as kit
import team_hub as hub
import team_workspace as workspace

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,webViewLink"
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER = "EditorKrab"
SIGN_IN_SECONDS = 300

router = APIRouter(prefix="/api/drive", tags=["drive"])
_lock = threading.Lock()


def _store() -> Path:
    return jobstore.STATE / "google-drive.json"


def _load() -> dict[str, Any]:
    path = _store()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _save(data: dict[str, Any]) -> None:
    path = _store()
    path.parent.mkdir(exist_ok=True)
    atomic.write_text(path, json.dumps(data, ensure_ascii=False, indent=1))


def _client() -> tuple[str, str]:
    cfg = kit._read_config()
    client_id, secret = str(cfg.get("google_drive_client_id", "")).strip(), str(cfg.get("google_drive_client_secret", "")).strip()
    if not client_id or not secret:
        raise HTTPException(409, "ยังไม่ได้ใส่ Google Drive client ID / secret ในหน้าทีม")
    return client_id, secret


def _call(url: str, data: bytes | None = None, headers: dict[str, str] | None = None, method: str | None = None,
          timeout: int = 60) -> tuple[dict[str, Any], Any]:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return (json.loads(body) if body else {}), response.headers
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise HTTPException(502, f"Google Drive ตอบกลับ {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(502, f"ติดต่อ Google Drive ไม่ได้: {exc.reason}") from exc


def _form(values: dict[str, str]) -> bytes:
    return urllib.parse.urlencode(values).encode()


def _access_token() -> str:
    data = _load()
    if not data.get("refresh_token"):
        raise HTTPException(409, "ยังไม่ได้เชื่อม Google Drive")
    if data.get("access_token") and data.get("expires_at", 0) > time.time() + 60:
        return data["access_token"]
    client_id, secret = _client()
    token, _ = _call(TOKEN_URL, _form({"client_id": client_id, "client_secret": secret,
                                       "refresh_token": data["refresh_token"], "grant_type": "refresh_token"}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
    data.update(access_token=token["access_token"], expires_at=time.time() + int(token.get("expires_in", 3600)))
    _save(data)
    return data["access_token"]


def _auth(token: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    return {"Authorization": "Bearer " + token, **(extra or {})}


class _Return(http.server.BaseHTTPRequestHandler):
    """Takes Google's redirect once: checks state, swaps the code for tokens, then the server stops."""
    pending: dict[str, Any] = {}

    def do_GET(self) -> None:  # noqa: N802 (http.server name)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        pending, ok = self.pending, False
        try:
            if not secrets.compare_digest(query.get("state", ""), pending["state"]):
                raise HTTPException(400, "คำขอไม่ตรงกับเครื่องนี้")
            if query.get("error") or not query.get("code"):
                raise HTTPException(400, "ยกเลิกการเชื่อม Google Drive")
            token, _ = _call(TOKEN_URL, _form({
                "client_id": pending["client_id"], "client_secret": pending["secret"], "code": query["code"],
                "code_verifier": pending["verifier"], "redirect_uri": pending["redirect"],
                "grant_type": "authorization_code"}), {"Content-Type": "application/x-www-form-urlencoded"})
            if not token.get("refresh_token"):
                raise HTTPException(502, "Google ไม่ส่ง refresh token กลับมา ลองเชื่อมใหม่")
            with _lock:
                data = _load()
                data.update(refresh_token=token["refresh_token"], access_token=token["access_token"],
                            expires_at=time.time() + int(token.get("expires_in", 3600)))
                _save(data)
            ok, text = True, "เชื่อม Google Drive แล้ว ปิดหน้านี้แล้วกลับไปที่ EditorKrab ได้เลย"
        except HTTPException as exc:
            text = "เชื่อม Google Drive ไม่สำเร็จ: " + str(exc.detail)
        except KeyError:
            text = "คำขอนี้หมดอายุแล้ว กดเชื่อมใหม่ใน EditorKrab"
        page = f"<!doctype html><meta charset='utf-8'><title>EditorKrab</title><p style='font:16px system-ui;padding:24px'>{text}</p>"
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(page.encode("utf-8"))
        self.server.done = True  # type: ignore[attr-defined]

    def log_message(self, *args: Any) -> None:
        pass


def _serve_once(server: http.server.HTTPServer) -> None:
    server.done = False  # type: ignore[attr-defined]
    server.timeout = 1
    deadline = time.time() + SIGN_IN_SECONDS
    while not server.done and time.time() < deadline:  # type: ignore[attr-defined]
        server.handle_request()
    server.server_close()


def _folder(token: str) -> str:
    data = _load()
    if data.get("folder_id"):
        return data["folder_id"]
    meta = json.dumps({"name": FOLDER, "mimeType": "application/vnd.google-apps.folder"}).encode()
    folder, _ = _call(API + "/files?fields=id", meta, _auth(token, {"Content-Type": "application/json"}), "POST")
    data["folder_id"] = folder["id"]
    _save(data)
    return folder["id"]


def _check_restricted(file_id: str, token: str) -> None:
    query = urllib.parse.urlencode({"fields": "permissions(type),nextPageToken", "pageSize": 100})
    data, _ = _call(f"{API}/files/{file_id}/permissions?{query}", None, _auth(token))
    permissions = data.get("permissions")
    if not isinstance(permissions, list) or not permissions or data.get("nextPageToken"):
        raise HTTPException(409, "ตรวจสิทธิ์ Drive ไม่ครบ เปิด Drive เพื่อตรวจสอบ")
    if any(p.get("type") not in ("user", "group") for p in permissions):
        raise HTTPException(409, "ไฟล์หรือโฟลเดอร์ Drive เปิดกว้างอยู่ ตั้งเป็นจำกัดก่อน")


def share_file(path: Path) -> dict[str, Any]:
    """Upload without granting public access; the owner shares with chosen reviewers in Drive."""
    size = path.stat().st_size
    with path.open("rb") as source:
        key = f"{path.name}|{hashlib.file_digest(source, 'sha256').hexdigest()}"
    with _lock:
        token = _access_token()
        known = _load().get("shared", {}).get(key)
        if known:
            _check_restricted(known["id"], token)
            return {**known, "uploaded": False, "access": "restricted"}
        folder = _folder(token)
        _check_restricted(folder, token)
        meta = json.dumps({"name": path.name, "parents": [folder]}).encode()
        _, headers = _call(UPLOAD, meta, _auth(token, {"Content-Type": "application/json; charset=UTF-8",
                                                      "X-Upload-Content-Type": "video/mp4",
                                                      "X-Upload-Content-Length": str(size)}), "POST")
        session = headers.get("Location")
        if not session:
            raise HTTPException(502, "Google Drive ไม่ให้ที่อยู่อัปโหลด")
        with path.open("rb") as stream:
            uploaded, _ = _call(session, stream, _auth(token, {"Content-Type": "video/mp4", "Content-Length": str(size)}),
                                "PUT", timeout=3600)
        _check_restricted(uploaded["id"], token)
        link = uploaded.get("webViewLink") or f"https://drive.google.com/file/d/{uploaded['id']}/view"
        data = _load()
        data.setdefault("shared", {})[key] = {"id": uploaded["id"], "link": link}
        _save(data)
        return {"id": uploaded["id"], "link": link, "uploaded": True, "access": "restricted"}


class Setup(BaseModel):
    google_drive_client_id: str = Field(min_length=10, max_length=300)
    google_drive_client_secret: str = Field(min_length=5, max_length=300)


@router.get("/status")
def status() -> dict[str, Any]:
    cfg = kit._read_config()
    return {"configured": bool(cfg.get("google_drive_client_id") and cfg.get("google_drive_client_secret")),
            "connected": bool(_load().get("refresh_token")),
            "client_id": cfg.get("google_drive_client_id", "")}


@router.post("/setup")
def setup(body: Setup, request: Request) -> dict[str, Any]:
    hub._same_origin(request)
    values = {k: v.strip() for k, v in body.model_dump().items()}
    if not values["google_drive_client_id"].endswith(".apps.googleusercontent.com"):
        raise HTTPException(400, "Client ID ต้องลงท้ายด้วย .apps.googleusercontent.com")
    cfg = kit._read_config()
    cfg.update(values)
    atomic.write_text(kit.CONFIG, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"saved": True}


@router.post("/connect")
def connect(request: Request) -> dict[str, str]:
    hub._same_origin(request)
    client_id, secret = _client()
    server = http.server.HTTPServer(("127.0.0.1", 0), _Return)
    redirect = f"http://127.0.0.1:{server.server_port}/"
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    _Return.pending = {"state": state, "verifier": verifier, "client_id": client_id, "secret": secret, "redirect": redirect}
    threading.Thread(target=_serve_once, args=(server,), daemon=True).start()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return {"url": AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent"})}


@router.post("/disconnect")
def disconnect(request: Request) -> dict[str, bool]:
    hub._same_origin(request)
    with _lock:
        _save({})
    return {"disconnected": True}


@router.post("/organizations/{org}/projects/{project}/share")
def share_export(org: UUID, project: UUID, request: Request) -> dict[str, Any]:
    """Upload the submitted MP4 without broadening Drive access; save the link on the team project."""
    hub._same_origin(request)
    item = next((p for p in workspace.projects(org, request) if p["id"] == str(project)), None)
    if not item:
        raise HTTPException(404, "ไม่พบงานในองค์กร")
    if not item.get("export_name"):
        raise HTTPException(409, "งานนี้ยังไม่ได้ส่ง MP4")
    path = workspace.file_at(workspace.workspace(org) / "exports", item["export_name"], item.get("export_size"))
    if not path:
        raise HTTPException(409, "MP4 ยังไม่ได้ sync มาเครื่องนี้")
    result = share_file(path)
    hub.rpc(request, "clipkit_set_share_link", {"org": str(org), "project": str(project), "link": result["link"]})
    return result
