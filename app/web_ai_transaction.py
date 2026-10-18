"""Stage AI edits locally; a durable journal restores interrupted applications before any next edit."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import atomic
import web_ai_identity as identity
from video_edit import VideoEditError


_work = ContextVar("transaction_work", default=None)


def staged(path):
    return _work.get() == Path(path).resolve()


@contextmanager
def _staging(work):
    token = _work.set(work.resolve())
    try:
        yield
    finally:
        _work.reset(token)


def manual(target, prepare):
    """Caller holds the writer lock; manual edits share durable publication and recovery."""
    import kit_settings
    answer = {"clipkit_web_ai":identity.VERSION,"step":"clip", **identity.issue("clip", target, operation="manual")}
    def run(work, assets):
        with kit_settings.motion_destination(assets):
            return prepare(work)
    return apply("clip", target, answer, run)


def _hash(file: Path) -> str | None:
    if file.is_symlink():
        raise VideoEditError(f"AI import refuses a linked project file: {file.name}")
    if not file.exists():
        return None
    digest = hashlib.sha256()
    with file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(file: Path):
    return json.loads(file.read_text(encoding="utf-8"))


def _write(file: Path, value):
    atomic.write_text(file, json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False))


def _copy(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream, source.open("rb") as original:
            shutil.copyfileobj(original, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def _files(folder: Path) -> dict[str, Path]:
    files = {}
    for file in folder.iterdir():
        name = file.name
        if (name.startswith("draft_content.json") or name == "draft_meta_info.json" or
                name.startswith("clipkit") and not name.startswith("clipkit_web_ai") and
                (name.endswith((".json", ".jsonl", ".legacy.bak")))):
            if not file.is_file() or file.is_symlink():
                raise VideoEditError(f"Invalid project metadata: {name}")
            files[name] = file
    for pattern in ("Timelines/project.json", "Timelines/*/draft_content.json", "clipkit_history/*.json.gz",
                    "clipkit_history/*.json.gz.corrections.json", "clipkit_undo/*/*.json"):
        for file in folder.glob(pattern):
            parents = file.relative_to(folder).parents
            if file.is_symlink() or any((folder / p).is_symlink() for p in parents):
                raise VideoEditError(f"AI import refuses linked metadata: {file.name}")
            files[file.relative_to(folder).as_posix()] = file
    return files


def _destination(step: str, target: Path, name: str) -> Path:
    if step == "stories":
        suffixes = {"stories": ".stories.json", "selection": ".story-selection.json"}
        if name not in suffixes:
            raise VideoEditError("Invalid story recovery journal")
        return Path(str(target) + suffixes[name])
    relative = Path(name)
    if relative.is_absolute() or not relative.parts or any(p in ("..", ".") for p in relative.parts):
        raise VideoEditError("Invalid AI recovery path")
    file = target / relative
    if not file.resolve().is_relative_to(target.resolve()) or file.is_symlink():
        raise VideoEditError("AI recovery path leaves the project")
    return file


def answer_hash(answer: dict) -> str:
    try:
        return hashlib.sha256(json.dumps(answer, sort_keys=True, ensure_ascii=False,
                                        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except ValueError as exc:
        raise VideoEditError("คำตอบ AI มีตัวเลขที่ไม่ถูกต้อง") from exc


def _packet(answer: dict) -> str:
    try:
        value = str(uuid.UUID(answer.get("packet_id", "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise VideoEditError("คำตอบไม่มี packet_id รุ่นปัจจุบัน · ส่งออกคำสั่ง AI ใหม่") from exc
    return value


def receipt(step: str, target: str, answer: dict):
    file = identity.state_dir(step, target) / "receipts" / (_packet(answer) + ".json")
    if not file.is_file():
        return None
    saved = _json(file)
    if saved.get("step") != step or saved.get("target_id") != identity._path_id(target):
        raise VideoEditError("AI receipt เป็นของโปรเจกต์ต้นทางอื่น · ส่งออกคำสั่ง AI ใหม่")
    if saved["answer_hash"] != answer_hash(answer):
        raise VideoEditError("packet นี้นำเข้าแล้วด้วยคำตอบอื่น · ส่งออกคำสั่ง AI ใหม่")
    return saved["result"]


def recover(step: str, target: str):
    """Caller holds the project writer lock. No receipt means restore the complete pre-import state."""
    root = identity.state_dir(step, target)
    transactions = root / "transactions"
    if not transactions.is_dir():
        return
    for journal in sorted(transactions.glob("*/journal.json")):
        saved = _json(journal)
        if saved["phase"] in ("committed", "rolled_back"):
            continue
        if saved.get("step") != step or saved.get("target_id") != identity._path_id(target):
            raise VideoEditError("AI recovery journal เป็นของโปรเจกต์ต้นทางอื่น · ย้ายประวัติที่คัดลอกไปเก็บสำรองก่อนทำต่อ")
        tx = journal.parent
        done = root / "receipts" / (_packet(saved["answer"]) + ".json")
        if done.is_file():
            if _json(done) != saved["receipt"]:
                raise VideoEditError("AI receipt does not match its recovery journal")
            saved["phase"] = "committed"
            _write(journal, saved)
            continue
        if step == "clip":
            import host_platform
            if host_platform.capcut_running():
                raise VideoEditError("ปิด CapCut ก่อนกู้คืน AI import ที่หยุดกลางทาง")
        # Check every file before restoring any: preserve a manual/native change after a crash.
        for index, entry in enumerate(saved["files"]):
            file = _destination(step, Path(target), entry["name"])
            if _hash(file) not in (entry["before"], entry["after"]):
                raise VideoEditError(f"พบงานแก้ใหม่ระหว่างกู้ AI import: {entry['name']} · เก็บทั้งสองเวอร์ชันไว้ใน {tx}")
            if entry["before"] is not None and _hash(tx / "before" / str(index)) != entry["before"]:
                raise VideoEditError("AI recovery backup is incomplete · retain the transaction folder")
        for index, entry in reversed(list(enumerate(saved["files"]))):
            file = _destination(step, Path(target), entry["name"])
            actual = _hash(file)
            if actual == entry["before"]:
                continue
            if actual != entry["after"]:
                raise VideoEditError(f"พบงานแก้ใหม่ระหว่างกู้คืน: {entry['name']} · เก็บเวอร์ชันไว้ใน {tx}")
            if entry["before"] is None:
                file.unlink(missing_ok=True)
            else:
                _copy(tx / "before" / str(index), file)
        for name in sorted(saved.get("directories", []), key=lambda n: len(Path(n).parts), reverse=True):
            directory = _destination("clip", Path(target), name)
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        if any(_hash(_destination(step, Path(target), entry["name"])) != entry["before"] for entry in saved["files"]):
            raise VideoEditError("มีงานแก้จากภายนอกขณะกู้คืน AI import · เก็บ transaction ไว้เพื่อตรวจ")
        saved["phase"] = "rolled_back"
        _write(journal, saved)


def preview_clip(target: str, answer: dict, prepare):
    """Stage the same edits in disposable metadata, without receipts or project writes."""
    identity.verify("clip", target, answer)
    original = _files(Path(target))
    before = {name: _hash(file) for name, file in original.items()}
    with tempfile.TemporaryDirectory(prefix="clipkit_preview_") as temporary:
        work = Path(temporary) / Path(target).name
        work.mkdir()
        for name, source in original.items():
            destination = work / name
            _copy(source, destination)
            if _hash(destination) != before[name]:
                raise VideoEditError("โปรเจกต์เปลี่ยนระหว่างเตรียมตัวอย่าง · ตรวจใหม่")
        with _staging(work):
            result = prepare(work)
        identity.verify("clip", target, answer)
        if {name: _hash(file) for name, file in _files(Path(target)).items()} != before:
            raise VideoEditError("โปรเจกต์เปลี่ยนระหว่างตรวจแผน · ตรวจใหม่")
        return result


def apply(step: str, target: str, answer: dict, prepare):
    """Caller holds writer lock. prepare runs only against staged metadata and private motion assets."""
    root = identity.state_dir(step, target)
    tx = root / "transactions" / uuid.uuid4().hex
    work = tx / "work" / Path(target).name
    work.mkdir(parents=True)
    target_path = Path(target)
    original = (_files(target_path) if step == "clip" else
                {name: _destination(step, target_path, name) for name in ("stories", "selection")})
    before = {name: _hash(file) for name, file in original.items()}
    if step == "clip":
        for name, source in original.items():
            destination = work / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    try:
        with _staging(work):
            result = prepare(work, tx / "assets")
        # The source, preset and every other packet dependency must still be the exported revision.
        identity.verify(step, target, answer)
        if step == "clip":
            import host_platform
            if host_platform.capcut_running():
                raise VideoEditError("CapCut เปิดระหว่างเตรียม AI import · ปิดแล้วลองนำเข้าใหม่")
        current = (_files(target_path) if step == "clip" else original)
        if {name: _hash(file) for name, file in current.items()} != before:
            raise VideoEditError("โปรเจกต์เปลี่ยนระหว่างเตรียม AI import · ส่งออกคำสั่งใหม่")
        after = _files(work) if step == "clip" else {"stories": work / "stories.json", "selection": work / "selection.json"}
        entries = []
        for name in sorted(set(before) | set(after)):
            new_hash = _hash(after[name]) if name in after else None
            if before.get(name) == new_hash:
                continue
            index = len(entries)
            for sub, file in (("before", original.get(name)), ("after", after.get(name))):
                if file and file.is_file():
                    destination = tx / sub / str(index)
                    destination.parent.mkdir(exist_ok=True)
                    _copy(file, destination)
            entries.append({"name": name, "before": before.get(name), "after": new_hash})
        done = {"step": step, "target_id": identity._path_id(target),
                "answer_hash": answer_hash(answer), "result": result}
        directories = set()
        for entry in entries:
            parent = _destination(step, target_path, entry["name"]).parent
            while parent != target_path and parent.is_relative_to(target_path) and not parent.exists():
                directories.add(parent.relative_to(target_path).as_posix())
                parent = parent.parent
        saved = {"step": step, "target_id": identity._path_id(target),
                 "phase": "prepared", "answer": answer, "files": entries,
                 "directories": sorted(directories), "receipt": done}
        journal = tx / "journal.json"
        _write(journal, saved)
        for index, entry in enumerate(entries):
            destination = _destination(step, target_path, entry["name"])
            if _hash(destination) != entry["before"]:
                raise VideoEditError("โปรเจกต์เปลี่ยนระหว่างบันทึก AI import · หยุดเพื่อรักษางานใหม่")
            if entry["after"] is None:
                destination.unlink()
            else:
                _copy(tx / "after" / str(index), destination)
        receipts = root / "receipts"
        receipts.mkdir(exist_ok=True)
        expected = dict(before)
        for entry in entries:
            if entry["after"] is None:
                expected.pop(entry["name"], None)
            else:
                expected[entry["name"]] = entry["after"]
        current = (_files(target_path) if step == "clip" else original)
        if {name: _hash(file) for name, file in current.items()} != expected:
            raise VideoEditError("พบงานแก้จากภายนอกระหว่างบันทึก AI import · เก็บเวอร์ชันไว้เพื่อกู้คืน")
        if step == "clip" and host_platform.capcut_running():
            raise VideoEditError("CapCut เปิดขณะบันทึก AI import · ปิดแล้วลองใหม่")
        record = _json(root / "packets" / (answer["packet_id"] + ".json"))
        snapshot, _ = identity._snapshot(step, target, record["identity"]["preferences"], record["sources"],
                                         operation=record["identity"].get("operation"))
        original_sources = {}
        for key, source in record["sources"].items():
            path = source.get("path")
            if not isinstance(path, str) or identity._path_id(path) != key:
                raise VideoEditError("คำสั่ง AI ไม่มีพาธต้นฉบับสำหรับตรวจหลังบันทึก · ส่งออกคำสั่งใหม่")
            # Recheck bytes even if an applied edit removed this input reference. New generated
            # derivative sources are legitimate effects of the transaction, not original inputs.
            current_source, _ = identity._media(Path(path), {})
            original_sources[key] = current_source
        snapshot["sources"] = original_sources
        for entry in entries:
            if entry["name"] in snapshot["files"]:
                snapshot["files"][entry["name"]] = entry["before"]
        if identity._hash(snapshot) != answer["revision"]:
            raise VideoEditError("ข้อมูลอ้างอิง AI เปลี่ยนขณะบันทึก · ส่งออกคำสั่งใหม่")
        _write(receipts / (_packet(answer) + ".json"), done)
    except Exception as exc:
        recover(step, target)
        committed = receipt(step, target, answer)
        if committed is not None:
            return committed
        # No committed overlay can refer to these assets after a complete rollback.
        shutil.rmtree(work.parent)
        if (tx / "assets").is_dir():
            shutil.rmtree(tx / "assets")
        _write(tx / "failure.json", {"error": str(exc)})
        raise
    # A durable receipt is the commit point. Failures after it must never roll back a successful edit.
    saved["phase"] = "committed"
    try:
        _write(journal, saved)
        shutil.rmtree(work.parent)
    except OSError:
        pass  # receipt proves success; the next recovery finalizes this retained journal
    return result
