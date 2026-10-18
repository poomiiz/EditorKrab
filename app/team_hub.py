"""Supabase-backed organization identity; provider tokens stay in the local server."""
from __future__ import annotations

import atomic
import json
import base64
import hashlib
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

import kit_settings

router = APIRouter(prefix="/api/team", tags=["editing-team"])
_sessions: dict[str, dict[str, Any]] = {}
_session_lock = threading.RLock()
COOKIE = "clipkit_session"
OAUTH_COOKIE = "clipkit_oauth"
_oauth: dict[str, dict] = {}


def _config() -> tuple[str, str]:
    cfg = kit_settings._read_config()
    return _validate_config(cfg)


def _validate_config(cfg: dict) -> tuple[str, str]:
    url, key = str(cfg.get("supabase_url", "")).rstrip("/"), str(cfg.get("supabase_anon_key", ""))
    parsed = urllib.parse.urlsplit(url)
    if not key or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise HTTPException(503, "ยังไม่ได้ตั้งค่าระบบบัญชีผู้ใช้และองค์กร")
    if key.startswith("sb_secret_"):
        raise HTTPException(503, "ระบบบัญชีต้องใช้ Publishable หรือ anon key เท่านั้น")
    if key.count(".") == 2:
        try:
            payload = key.split(".")[1]
            role = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("role")
        except (ValueError, UnicodeError):
            raise HTTPException(503, "ค่า anon key ของระบบบัญชีไม่ถูกต้อง")
        if role != "anon":
            raise HTTPException(503, "ระบบบัญชีต้องใช้ anon key เท่านั้น")
    return url, key


def _provider(path: str, method: str = "GET", body: dict | None = None, token: str = "") -> Any:
    url, key = _config()
    headers = {"apikey": key, "Authorization": "Bearer " + (token or key), "Content-Type": "application/json"}
    request = urllib.request.Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as result:
            data = result.read()
            return json.loads(data) if data else None
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read())
        except ValueError:
            data = {}
        if data.get("code") in ("PGRST202","PGRST205"):
            raise HTTPException(503,"ยังไม่ได้ติดตั้งฐานข้อมูลองค์กรใน Supabase ให้ผู้ดูแลติดตั้งก่อนเปิดระบบทีม") from exc
        raise HTTPException(exc.code, data.get("msg") or data.get("message") or data.get("error_description") or "ระบบทีมปฏิเสธคำขอ") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(502, "ติดต่อระบบบัญชีผู้ใช้และองค์กรไม่ได้") from exc


def _same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "คำขอต้องมาจากหน้า EditorKrab นี้")


def _remember(data: dict, response: Response) -> bool:
    if not data.get("access_token"):
        return False
    session_id = secrets.token_urlsafe(32)
    with _session_lock:
        _sessions[session_id] = {**data, "expires_at": time.time() + data.get("expires_in", 3600), "ends_at": time.time() + 86400}
    response.set_cookie(COOKIE, session_id, httponly=True, samesite="lax", max_age=86400, path="/")
    return True


def session(request: Request) -> dict[str, Any]:
    session_id = request.cookies.get(COOKIE, "")
    with _session_lock:
        data = _sessions.get(session_id)
        if not data:
            raise HTTPException(401, "เข้าสู่ระบบก่อน")
        if data["ends_at"] <= time.time():
            _sessions.pop(session_id)
            raise HTTPException(401, "หมดเวลาเข้าสู่ระบบ กรุณาเข้าสู่ระบบใหม่")
        if data["expires_at"] <= time.time() + 30:
            try:
                renewed = _provider("/auth/v1/token?grant_type=refresh_token", "POST", {"refresh_token": data["refresh_token"]})
            except HTTPException as exc:
                if exc.status_code in (400, 401, 403):
                    _sessions.pop(session_id)
                    raise HTTPException(401, "หมดเวลาเข้าสู่ระบบ กรุณาเข้าสู่ระบบใหม่") from exc
                raise
            data.update(renewed)
            data["expires_at"] = time.time() + renewed.get("expires_in", 3600)
        return data.copy()


def rpc(request: Request, name: str, body: dict) -> Any:
    _same_origin(request)
    return _provider("/rest/v1/rpc/" + name, "POST", body, session(request)["access_token"])


class Setup(BaseModel):
    supabase_url: str = Field(max_length=500)
    supabase_anon_key: str = Field(min_length=1, max_length=5000)


@router.get("/setup")
def get_setup() -> dict:
    cfg=kit_settings._read_config()
    try:_validate_config(cfg)
    except HTTPException:return {"supabase_url":cfg.get("supabase_url",""),"supabase_anon_key":""}
    return {key:cfg.get(key,"") for key in Setup.model_fields}


@router.post("/setup")
def save_setup(body: Setup,request: Request) -> dict:
    _same_origin(request)
    values={key:value.strip() for key,value in body.model_dump().items()}
    _validate_config(values)
    cfg=kit_settings._read_config();cfg.update(values)
    atomic.write_text(kit_settings.CONFIG, json.dumps(cfg,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with _session_lock:_sessions.clear()
    return {"saved":True,"note":"บันทึกแล้ว กรุณาเข้าสู่ระบบใหม่"}


@router.post("/google")
def google_login(request: Request, response: Response) -> dict:
    _same_origin(request)
    url, _ = _config()
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    with _session_lock:
        for key in list(_oauth):
            if _oauth[key]["expires_at"] <= time.time():
                del _oauth[key]
        _oauth.pop(request.cookies.get(OAUTH_COOKIE, ""), None)
        _oauth[state] = {"verifier": verifier, "expires_at": time.time() + 600, "provider": url,
                         "origin": str(request.base_url).rstrip("/")}
    # Lax permits Google's top-level return; the login session itself stays Strict.
    response.set_cookie(OAUTH_COOKIE, state, httponly=True, samesite="lax", max_age=600, path="/api/team/google/callback")
    callback = str(request.base_url).rstrip("/") + "/api/team/google/callback?state=" + state
    return {"url": url + "/auth/v1/authorize?" + urllib.parse.urlencode({"provider": "google", "redirect_to": callback,
            "code_challenge": challenge, "code_challenge_method": "s256"})}


@router.get("/google/callback")
def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    cookie = request.cookies.get(OAUTH_COOKIE, "")
    if not state or not cookie or not secrets.compare_digest(state, cookie):
        raise HTTPException(400, "คำขอเข้าสู่ระบบไม่ตรงกับเครื่องนี้ กรุณาเริ่มใหม่")
    with _session_lock:
        pending = _oauth.pop(state, None)
    if not pending or pending["expires_at"] <= time.time() or pending["origin"] != str(request.base_url).rstrip("/"):
        raise HTTPException(400, "คำขอเข้าสู่ระบบหมดอายุ กรุณาเริ่มใหม่")
    response = RedirectResponse("/team.html", status_code=303)
    response.delete_cookie(OAUTH_COOKIE, path="/api/team/google/callback")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    try:
        if error or not code:
            raise HTTPException(400, "ยกเลิกการเข้าสู่ระบบ Google กรุณาลองใหม่")
        if pending["provider"] != _config()[0]:
            raise HTTPException(400, "ระบบบัญชีเปลี่ยน กรุณาเข้าสู่ระบบใหม่")
        data = _provider("/auth/v1/token?grant_type=pkce", "POST", {"auth_code": code, "code_verifier": pending["verifier"]})
        if not _remember(data, response):
            raise HTTPException(401, "เข้าสู่ระบบ Google ไม่สำเร็จ")
    except HTTPException:
        response.headers["location"] = "/login.html?login_error=1"
    return response


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    _same_origin(request)
    with _session_lock:
        _sessions.pop(request.cookies.get(COOKIE, ""), None)
    response.delete_cookie(COOKIE, path="/")
    return {"signed_out": True}


@router.get("/session")
def identity(request: Request) -> dict:
    try:
        _config()
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        return {"configured": False, "signed_in": False}
    if request.cookies.get(COOKIE, "") not in _sessions:
        return {"configured": True, "signed_in": False}
    data = session(request)
    user = data["user"]
    return {"configured": True, "signed_in": True, "user": {"id": user["id"], "email": user.get("email"),
            "display_name": user.get("user_metadata", {}).get("display_name") or user.get("user_metadata", {}).get("full_name", "")}}


@router.get("/organizations")
def organizations(request: Request) -> Any:
    token = session(request)["access_token"]
    path = "/rest/v1/clipkit_organizations?select=*,clipkit_members(*)&order=created_at"
    found = _provider(path, token=token)
    if not found:
        invitations = rpc(request, "clipkit_pending_invitations", {})
        # A single invitation opens the team on first login; multiple invitations need a choice.
        if len(invitations) == 1:
            rpc(request, "clipkit_accept_invitation", {"org": invitations[0]["organization_id"]})
            found = _provider(path, token=token)
    return found


def owner_organization(org: UUID,request: Request) -> dict:
    user=session(request)["user"]["id"]
    found=next((row for row in organizations(request) if row["id"]==str(org)),None)
    if not found or not any(member["user_id"]==user and member["role"]=="admin" for member in found["clipkit_members"]):raise HTTPException(403,"เจ้าขององค์กรเท่านั้น")
    return found


class Organization(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.post("/organizations")
def create_organization(body: Organization, request: Request) -> dict:
    return {"id": rpc(request, "clipkit_create_organization", {"org_name": body.name.strip()})}


class Member(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["lead", "editor"]


@router.post("/organizations/{org}/members")
def add_member(org: UUID, body: Member, request: Request) -> dict:
    _same_origin(request)
    organization=owner_organization(org,request)
    rpc(request,"clipkit_add_member",{"org":str(org),"member_email":body.email.strip(),"member_role":body.role})
    return {"saved":True}


@router.get("/organizations/{org}/members")
def member_directory(org: UUID, request: Request) -> Any:
    return rpc(request, "clipkit_member_directory", {"org": str(org)})


@router.get("/organizations/{org}/activity")
def activity(org: UUID, request: Request) -> Any:
    return _provider("/rest/v1/clipkit_activity?organization_id=eq." + str(org) + "&select=*&order=created_at.desc&limit=100", token=session(request)["access_token"])


@router.get("/invitations")
def invitations(request: Request) -> Any:
    return rpc(request, "clipkit_pending_invitations", {})


@router.post("/invitations/{org}/accept")
def accept_invitation(org: UUID, request: Request) -> dict:
    return {"id": rpc(request, "clipkit_accept_invitation", {"org": str(org)})}


@router.get("/organizations/{org}/invitations")
def organization_invitations(org: UUID, request: Request) -> Any:
    owner_organization(org, request)
    return _provider("/rest/v1/clipkit_invitations?organization_id=eq." + str(org) + "&select=email,role,expires_at&order=expires_at.desc", token=session(request)["access_token"])


class InvitationEmail(BaseModel):
    email: str = Field(min_length=3, max_length=320)


@router.post("/organizations/{org}/invitations/cancel")
def cancel_invitation(org: UUID, body: InvitationEmail, request: Request) -> dict:
    rpc(request, "clipkit_cancel_invitation", {"org": str(org), "member_email": body.email.strip()})
    return {"cancelled": True}
