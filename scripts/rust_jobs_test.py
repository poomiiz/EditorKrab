"""Native Rust queue/SSE acceptance on disposable media/state. Python is test tooling only."""
from __future__ import annotations
import json
from contextlib import closing
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "rust" / "target" / "debug" / ("editorkrab.exe" if os.name == "nt" else "editorkrab")
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def request(base, method, path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def launch(state, number):
    env = {**os.environ, "EDITORKRAB_RUST_STATE": str(state), "EDITORKRAB_RUST_PORT": str(number)}
    return subprocess.Popen([str(EXE)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=FLAGS)


def ready(process, base):
    end = time.monotonic() + 15
    while time.monotonic() < end:
        if process.poll() is not None:
            raise AssertionError(process.communicate())
        try:
            with urllib.request.urlopen(base + "/", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(.05)
    raise AssertionError("Rust service did not listen")


def events(base, after, done, header=False):
    headers = {"Last-Event-ID": str(after)} if header else {}
    req = urllib.request.Request(base + f"/api/v1/projects/queue-test/events?after={after}", headers=headers)
    collected, frame = [], {}
    with urllib.request.urlopen(req, timeout=10) as response:
        while True:
            line = response.readline().decode().rstrip("\r\n")
            if not line:
                if "data" in frame:
                    frame["data"] = json.loads(frame["data"])
                    collected.append(frame)
                    if done(collected):
                        return collected
                frame = {}
            elif ":" in line and not line.startswith(":"):
                key, value = line.split(":", 1)
                frame[key] = value.lstrip()


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    process.communicate()


def main():
    assert EXE.is_file(), "Run cargo build --manifest-path rust/Cargo.toml first"
    assert shutil.which("ffprobe") and shutil.which("ffmpeg"), "Native media tools required for this acceptance run"
    with tempfile.TemporaryDirectory(prefix="editorkrab-native-jobs-") as temporary:
        folder = Path(temporary)
        wav = folder / "เสียงทดสอบ.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2", str(wav)],
                       check=True, creationflags=FLAGS)
        state = folder / "state.sqlite"
        number = port()
        base = f"http://127.0.0.1:{number}"
        process = launch(state, number)
        evidence = {"platform": os.name, "media_engine": "native ffprobe", "checks": []}
        try:
            ready(process, base)
            assert request(base,"PUT","/api/v1/drafts/queue-test",{
                "operation_id":"initial","expected_revision":0,"document":{"title":"คิวทดสอบ"}})[0] == 200
            missing = folder / "ยังไม่มี.wav"
            body = {"request_id":"five","revision":1,"items":[
                {"key":f"clip{index}","task":{"kind":"probe","path":str(missing if index == 0 else wav)}}
                for index in range(5)]}
            code, jobs = request(base,"POST","/api/v1/projects/queue-test/jobs",body)
            assert code == 200 and len(jobs) == 5, (code,jobs)
            again = request(base,"POST","/api/v1/projects/queue-test/jobs",body)[1]
            assert {j["id"] for j in again} == {j["id"] for j in jobs}
            changed = {**body,"items":body["items"][:-1]}
            assert request(base,"POST","/api/v1/projects/queue-test/jobs",changed)[0] == 409
            ids = {job["id"] for job in jobs}
            terminal = {"succeeded","failed","cancelled"}
            completed = lambda rows: {r["data"]["id"] for r in rows if r.get("event") == "job" and r["data"]["status"] in terminal} >= ids
            rows = events(base,0,completed)
            sequences = [int(row["id"]) for row in rows]
            assert sequences == sorted(set(sequences))
            code, final = request(base,"GET","/api/v1/projects/queue-test/jobs")
            assert code == 200 and len(final) == 5
            assert [job["status"] for job in final] == ["failed"] + ["succeeded"]*4, final
            assert all(abs(job["result"]["duration_s"]-.2)<.01 for job in final[1:])
            evidence["checks"] += ["five native jobs retained","idempotent batch request","changed request rejected",
                                   "one failure does not stop later work","real duration metadata","SSE ordered persisted events"]
            replay = events(base,sequences[-3],lambda records:len(records)==2,header=True)
            assert [int(row["id"]) for row in replay] == sequences[-2:]
            reset = events(base,999999999,lambda records:len(records)==1,header=True)[0]
            assert reset["event"] == "reset" and len(reset["data"]) == 5
            evidence["checks"] += ["Last-Event-ID replay","cursor reset after older database restore"]
            shutil.copyfile(wav,missing)
            failed_id = final[0]["id"]
            assert request(base,"POST",f"/api/v1/jobs/{failed_id}/retry",{})[0] == 200
            events(base,sequences[-1],lambda records:any(row["data"]["id"]==failed_id and row["data"]["status"]=="succeeded" for row in records))
            final = request(base,"GET","/api/v1/projects/queue-test/jobs")[1]
            assert [job["attempt"] for job in final] == [2,1,1,1,1]
            assert request(base,"POST",f"/api/v1/jobs/{failed_id}/retry",{})[0] == 409
            evidence["checks"] += ["retry only failed item","successful jobs protected from retry"]

            second = launch(state,port())
            second.communicate(timeout=10)
            assert second.returncode != 0, "Second instance must not recover live worker jobs"
            evidence["checks"].append("exclusive state-file lock")
            code, _ = request(base,"POST","/api/v1/projects/queue-test/jobs",{**body,"request_id":"restart"})
            assert code == 200
            # This is our disposable test process. A hard stop checks durable queue recovery.
            process.kill();process.wait(timeout=5);process.communicate()
            with closing(sqlite3.connect(state)) as connection:
                before = dict(connection.execute("SELECT id,status FROM jobs"))
            assert any(value in {"queued","running"} for value in before.values()), "restart case did not interrupt unfinished work"
            process = launch(state,number);ready(process,base)
            end = time.monotonic()+15
            while time.monotonic()<end:
                final = request(base,"GET","/api/v1/projects/queue-test/jobs")[1]
                if len(final)==10 and all(job["status"]=="succeeded" for job in final):break
                time.sleep(.1)
            else:raise AssertionError(final)
            assert len({job["id"] for job in final}) == 10
            assert [job["attempt"] for job in final[:5]] == [2,1,1,1,1]
            evidence["checks"] += ["real process restart resumes unfinished batch","completed results not repeated"]
            evidence["final_jobs"] = [{key:job[key] for key in ("id","status","attempt")} for job in final]
            evidence["scope"] = "Native metadata jobs only; does not prove CapCut batch export, ASR or Mac acceptance"
            destination = ROOT/"output"/"rust-jobs-native-readback.json"
            destination.parent.mkdir(exist_ok=True)
            destination.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps({"checks":len(evidence["checks"]),"jobs":len(final),"evidence":str(destination)}),flush=True)
        finally:
            stop(process)


if __name__ == "__main__":
    main()
