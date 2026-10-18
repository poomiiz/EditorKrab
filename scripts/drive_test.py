"""Drive: PKCE, restricted uploads, content cache and no automatic public permissions.
Google is faked; nothing leaves this machine. Run: python scripts/drive_test.py"""
import base64
import hashlib
import io
import json
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import drive
import jobstore

ORG, PROJECT = "00000000-0000-0000-0000-000000000010", "00000000-0000-0000-0000-000000000020"
CLIENT = "123-abc.apps.googleusercontent.com"
real_urlopen = urllib.request.urlopen


class Reply(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(json.dumps(body).encode() if body is not None else b"")
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def main():
    calls = []

    def google(request, timeout=0):
        url = request.full_url
        if url.startswith("http://127.0.0.1"):
            return real_urlopen(request, timeout=timeout)   # the loopback return server is real
        body = request.data
        if hasattr(body, "read"):
            body = body.read()
        calls.append((request.get_method(), url, body, dict(request.header_items())))
        if url == drive.TOKEN_URL:
            form = dict(urllib.parse.parse_qsl(body.decode()))
            if form["grant_type"] == "authorization_code":
                challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).decode().rstrip("=")
                assert challenge == sent["code_challenge"] and form["code"] == "good-code", form
                return Reply({"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})
            assert form == {"client_id": CLIENT, "client_secret": "s3cret", "refresh_token": "rt-1",
                            "grant_type": "refresh_token"}, form
            return Reply({"access_token": "at-2", "expires_in": 3600})
        if url.endswith("/files?fields=id"):
            assert json.loads(body)["mimeType"] == "application/vnd.google-apps.folder"
            return Reply({"id": "folder-1"})
        if url == drive.UPLOAD:
            meta = json.loads(body)
            assert meta == {"name": "cut.mp4", "parents": ["folder-1"]}, meta
            return Reply(None, {"Location": "https://upload.example/session-1"})
        if url == "https://upload.example/session-1":
            assert body in (b"mp4-bytes", b"new-bytes") and request.get_method() == "PUT"
            return Reply({"id": "file-1", "webViewLink": "https://drive.google.com/file/d/file-1/view"})
        if "/permissions?" in url:
            assert request.get_method() == "GET" and body is None
            return Reply({"permissions": [{"type": "user"}]})
        raise AssertionError("unexpected call " + url)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        config = root / "config.json"
        config.write_text("{}", encoding="utf-8")
        exports = root / "team" / "exports"
        exports.mkdir(parents=True)
        (exports / "cut.mp4").write_bytes(b"mp4-bytes")
        item = {"id": PROJECT, "export_name": "cut.mp4", "export_size": 9}
        app = FastAPI()
        app.include_router(drive.router)
        with patch.object(jobstore, "STATE", root / "state"), \
                patch.object(drive.kit, "CONFIG", config), \
                patch.object(drive.kit, "_read_config", lambda: json.loads(config.read_text(encoding="utf-8"))), \
                patch.object(drive.workspace, "projects", return_value=[item]), \
                patch.object(drive.workspace, "workspace", return_value=root / "team"), \
                patch.object(drive.hub, "rpc") as rpc, \
                patch("urllib.request.urlopen", google), TestClient(app) as client:
            assert client.get("/api/drive/status").json() == {"configured": False, "connected": False, "client_id": ""}
            assert client.post("/api/drive/connect").status_code == 409
            share = f"/api/drive/organizations/{ORG}/projects/{PROJECT}/share"
            assert client.post(share).status_code == 409, "shared before Drive was connected"
            bad = client.post("/api/drive/setup", json={"google_drive_client_id": "not-a-google-client", "google_drive_client_secret": "s3cret"})
            assert bad.status_code == 400
            assert client.post("/api/drive/setup", json={"google_drive_client_id": CLIENT, "google_drive_client_secret": "s3cret"},
                               headers={"origin": "https://evil.example"}).status_code == 403
            assert client.post("/api/drive/setup", json={"google_drive_client_id": CLIENT, "google_drive_client_secret": "s3cret"}).json() == {"saved": True}

            url = client.post("/api/drive/connect").json()["url"]
            sent = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            assert url.startswith(drive.AUTH_URL) and sent["scope"] == drive.SCOPE and sent["code_challenge_method"] == "S256"
            assert sent["redirect_uri"].startswith("http://127.0.0.1:") and sent["access_type"] == "offline"
            back = sent["redirect_uri"] + "?" + urllib.parse.urlencode({"state": "forged", "code": "good-code"})
            try:
                real_urlopen(back, timeout=5)
                raise AssertionError("a forged state was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
            assert not client.get("/api/drive/status").json()["connected"]
            url = client.post("/api/drive/connect").json()["url"]   # a fresh sign-in after the refused one
            sent = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            back = sent["redirect_uri"] + "?" + urllib.parse.urlencode({"state": sent["state"], "code": "good-code"})
            with real_urlopen(back, timeout=5) as page:
                assert "เชื่อม Google Drive แล้ว" in page.read().decode("utf-8")
            assert client.get("/api/drive/status").json()["connected"]

            stored = json.loads((root / "state" / "google-drive.json").read_text(encoding="utf-8"))
            stored["expires_at"] = time.time() - 1   # force the refresh path
            (root / "state" / "google-drive.json").write_text(json.dumps(stored), encoding="utf-8")
            first = client.post(share).json()
            assert first == {"id": "file-1", "link": "https://drive.google.com/file/d/file-1/view", "uploaded": True, "access": "restricted"}, first
            assert any(c[3].get("Authorization") == "Bearer at-2" for c in calls), "the refreshed token was not used"
            assert not any(c[0] == "POST" and "/permissions" in c[1] for c in calls), "public permissions were created"
            rpc.assert_called_with(rpc.call_args.args[0], "clipkit_set_share_link",
                                   {"org": ORG, "project": PROJECT, "link": first["link"]})
            count = len(calls)
            again = client.post(share).json()
            assert again["link"] == first["link"] and again["uploaded"] is False and len(calls) == count + 1, "cache must recheck permissions without uploading"
            for permissions in ({"permissions": [{"type": "anyone"}]}, {"permissions": [{"type": "domain"}]},
                                {"permissions": []}, {"permissions": [{"type": "user"}], "nextPageToken": "more"}):
                with patch.object(drive, "_call", return_value=(permissions, {})):
                    try:
                        drive.share_file(exports / "cut.mp4")
                        raise AssertionError("unverified or broad cache access was accepted")
                    except HTTPException as exc:
                        assert exc.status_code == 409
            before = len([c for c in calls if c[1] == drive.UPLOAD])
            (exports / "cut.mp4").write_bytes(b"new-bytes")  # same size, different contents must not use the old cache
            assert client.post(share).json()["uploaded"] is True
            assert len([c for c in calls if c[1] == drive.UPLOAD]) == before + 1

            (exports / "cut.mp4").write_bytes(b"different size")
            assert client.post(share).status_code == 409, "a file still syncing was shared"
            assert client.post("/api/drive/disconnect").json() == {"disconnected": True}
            assert not client.get("/api/drive/status").json()["connected"]
    print("PASS Drive: PKCE, forged state refusal, refresh, restricted upload, content cache, permission checks, disconnect")


if __name__ == "__main__":
    main()
