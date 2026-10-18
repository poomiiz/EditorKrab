"""Private immutable delivery evidence. It never grants team or human approval."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

import atomic
import web_ai_identity as identity
from video_edit import VideoEditError


def commit() -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(identity.KIT), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", value) else None


def artifact(path: Path, kind: str) -> dict:
    if path.is_symlink() or path.is_junction():
        raise VideoEditError("Delivery artifact must not be a symbolic link")
    if kind == "mp4":
        if not path.is_file() or path.stat().st_size <= 0:
            raise VideoEditError("Delivery MP4 is missing or empty")
        return {"sha256": identity._file(path, required=True), "bytes": path.stat().st_size}
    if kind != "capcut" or not path.is_dir() or not (path / "draft_content.json").is_file():
        raise VideoEditError("Delivery must be an MP4 or a CapCut project")
    files = {}
    for file in sorted(path.rglob("*")):
        if file.is_symlink() or file.is_junction():
            raise VideoEditError("Native delivery contains a symbolic link")
        if file.is_file():
            files[file.relative_to(path).as_posix()] = identity._file(file, required=True)
    return {"files": files}


def prepare(project: str | Path, staged: Path, output: Path, kind: str, inputs: dict,
            *, options: dict, checks: dict) -> Path:
    """Write evidence before publication; a failed publication leaves an unaccepted orphan record."""
    if (not isinstance(inputs, dict) or not isinstance(inputs.get("snapshot"), dict)
            or inputs["snapshot"].get("target_id") != identity._path_id(project)):
        raise VideoEditError("Delivery evidence needs the checked export input snapshot")
    record = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "project_id": identity._path_id(project), "output_id": identity._path_id(output),
              "kind": kind, "inputs": inputs, "artifact": artifact(staged, kind),
              "commit": commit(), "options": options, "checks": checks,
              "human_audio_sync": "pending", "human_editorial_review": "pending", "capcut_open": "pending"}
    record["id"] = identity._hash(record)
    folder = output.parent / ".clipkit-deliveries"
    if folder.is_symlink() or folder.is_junction():
        raise VideoEditError("Delivery evidence folder must not be a link")
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / (record["id"] + ".private.json")
    if file.exists():
        raise VideoEditError("Delivery evidence already exists; it must not be overwritten")
    atomic.write_text(file, json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    return file


def status(project: str | Path, manifest: str | Path, output: str | Path) -> dict:
    """Read back current inputs and bytes; historical team status never establishes local approval."""
    try:
        record = json.loads(Path(manifest).read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("record must be an object")
        body = {k: v for k, v in record.items() if k != "id"}
        if (record.get("version") != 1 or record.get("id") != identity._hash(body)
                or record.get("project_id") != identity._path_id(project)
                or record.get("output_id") != identity._path_id(output)):
            raise ValueError("record is changed or belongs to another delivery")
        current = identity.export_check(project)()
        if "resolved_media" in record["inputs"]:
            current["resolved_media"] = {file: identity._file(Path(file)) for file in record["inputs"]["resolved_media"]}
        inputs_match = current == record["inputs"]
        artifact_matches = artifact(Path(output), record["kind"]) == record["artifact"]
        pending_checks = not record.get("commit") or any(value != "passed" for key, value in record["checks"].items() if key != "probe")
        return {"manifest_id": record["id"], "kind": record["kind"], "checks": record["checks"], "inputs_match": inputs_match,
                "artifact_matches": artifact_matches, "current": inputs_match and artifact_matches,
                "commit_provenance": "recorded" if record.get("commit") else "pending",
                "human_audio_sync": "pending", "human_editorial_review": "pending", "capcut_open": "pending",
                "status": ("pending_checks" if pending_checks else "pending_review") if inputs_match and artifact_matches else "stale"}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise VideoEditError(f"Invalid delivery evidence: {exc}") from exc
