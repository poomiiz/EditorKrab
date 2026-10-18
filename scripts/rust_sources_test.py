"""Source registration acceptance against the disposable Rust preview, without UI automation."""
import json
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path
from rust_jobs_test import launch, port, ready, stop

BASE = ""  # set by main() to the disposable server it starts


def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="GET" if data is None else "PUT")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def wait_job(project, count):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status, jobs = request(f"/api/v1/projects/{project}/jobs")
        assert status == 200
        if len(jobs) == count and all(j["status"] in ("succeeded", "failed") for j in jobs):
            return jobs
        time.sleep(0.1)
    raise AssertionError("source probe did not finish")


def main():
    global BASE
    project = "sources-native-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="editorkrab-sources-http-") as temporary:
        number = port()
        BASE = f"http://127.0.0.1:{number}"
        process = launch(Path(temporary) / "state.sqlite", number)
        try:
            ready(process, BASE)
            evidence = check(project, temporary)
        finally:
            stop(process)
    output = Path(__file__).resolve().parents[1] / "output/rust-sources-native-readback.json"
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"project": project, "checks": evidence["checks"], "evidence": str(output)}, ensure_ascii=False))


def check(project, temporary):
    source_path = Path(temporary) / "ทดสอบ เสียง.wav"
    with wave.open(str(source_path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 3200)
    stat = source_path.stat()
    source = {"source_id": uuid.uuid4().hex, "path": str(source_path),
              "name": source_path.name, "size": stat.st_size,
              "modified_ns": str(stat.st_mtime_ns)}
    draft = {"step": 1, "title": "ตรวจ source native", "language": "auto",
             "files": [source], "style": "bold16", "plan": ""}
    save = {"operation_id": "select", "expected_revision": 0, "document": draft}
    assert request(f"/api/v1/drafts/{project}", save)[0] == 200
    assert request(f"/api/v1/drafts/{project}", save)[0] == 200
    jobs = wait_job(project, 1)
    assert jobs[0]["status"] == "succeeded", jobs
    assert abs(jobs[0]["result"]["duration_s"] - 0.2) < 0.001
    assert jobs[0]["attempt"] == 1
    # A changed source identity must fail explicitly rather than reuse metadata.
    changed = {**source, "source_id": uuid.uuid4().hex, "modified_ns": "0"}
    draft = {**draft, "files": [source, changed]}
    update = {"operation_id": "changed", "expected_revision": 1, "document": draft}
    assert request(f"/api/v1/drafts/{project}", update)[0] == 200
    jobs = wait_job(project, 2)
    assert jobs[1]["status"] == "failed" and jobs[1]["error"] == "source_changed", jobs
    invalid = {**draft, "files": [source, {**source, "path": "different.wav"}]}
    assert request(f"/api/v1/drafts/{project}", {"operation_id": "invalid",
                   "expected_revision": 2, "document": invalid})[0] == 400
    status, restored = request(f"/api/v1/drafts/{project}")
    assert status == 200 and restored["revision"] == 2 and restored["document"] == draft
    assert len(request(f"/api/v1/projects/{project}/jobs")[1]) == 2
    evidence = {"project": project, "jobs": jobs,
                "checks": ["Thai file path", "source stamp", "automatic probe", "idempotent save",
                           "changed source rejected", "invalid import atomic rollback"],
                "native_picker_selection": "unverified"}
    return evidence


if __name__ == "__main__":
    main()
