"""Agent endpoint (/mcp and `editorkrab --mcp`) against a disposable Rust server with no speech engine."""
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from rust_jobs_test import EXE, FLAGS, port, ready, stop

EXE = Path(os.environ.get("EDITORKRAB_EXE", EXE))
BASE = ""
SEQUENCE = iter(range(1, 10_000))


def post(body, headers=None):
    req = urllib.request.Request(BASE + "/mcp", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            text = response.read().decode()
            return response.status, json.loads(text) if text else None
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def rpc(method, params=None):
    status, reply = post({"jsonrpc": "2.0", "id": next(SEQUENCE), "method": method, "params": params or {}})
    assert status == 200, (status, reply)
    return reply


def tool(name, **arguments):
    result = rpc("tools/call", {"name": name, "arguments": arguments})["result"]
    return result["isError"], json.loads(result["content"][0]["text"])


def bridge(number, lines):
    env = {**os.environ, "EDITORKRAB_RUST_PORT": str(number)}
    done = subprocess.run([str(EXE), "--mcp"], input="\n".join(lines) + "\n", env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=30, creationflags=FLAGS)
    assert done.returncode == 0, done.stderr
    return [json.loads(line) for line in done.stdout.splitlines()]


def main():
    global BASE
    with tempfile.TemporaryDirectory(prefix="editorkrab-mcp-") as temporary:
        folder = Path(temporary)
        number = port()
        BASE = f"http://127.0.0.1:{number}"
        env = {key: value for key, value in os.environ.items()
               if key not in ("EDITORKRAB_WHISPER", "EDITORKRAB_WHISPER_MODEL", "EDITORKRAB_FFMPEG")}
        env.update(EDITORKRAB_RUST_STATE=str(folder / "state.sqlite"), EDITORKRAB_RUST_PORT=str(number),
                   EDITORKRAB_DATA=str(folder / "data"), EDITORKRAB_EXPORT_DIR=str(folder / "out"))
        process = subprocess.Popen([str(EXE)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=FLAGS)
        try:
            ready(process, BASE)
            checks = check(folder, number)
        finally:
            stop(process)
    print(json.dumps({"checks": checks}, ensure_ascii=False))


def check(folder, number):
    checks = []
    hello = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                               "clientInfo": {"name": "test", "version": "0"}})["result"]
    assert hello["protocolVersion"] == "2025-03-26" and hello["serverInfo"]["name"] == "editorkrab", hello
    assert post({"jsonrpc": "2.0", "method": "notifications/initialized"}) == (202, None)
    names = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
    assert names == ["import_videos", "transcribe", "get_project", "get_transcript", "set_clips", "export_clips"], names
    checks.append("handshake")

    assert post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, {"Origin": "https://example.com"})[0] == 403
    assert rpc("tools/call", {"name": "delete_everything", "arguments": {}})["error"]["code"] == -32602
    assert rpc("nope")["error"]["code"] == -32601
    checks.append("foreign_origin_and_unknown_calls_refused")

    source = folder / "ทดสอบ เสียง.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 3200)
    failed, answer = tool("import_videos", paths=["relative.wav"])
    assert failed and answer["error"] == "absolute_paths_required", answer
    failed, answer = tool("import_videos", paths=[str(source)], title="ทดสอบ agent", language="th")
    assert not failed and len(answer["files"]) == 1, answer
    project = answer["project"]
    assert answer["open_in_app"] == f"{BASE}/?project={project}", answer
    failed, again = tool("import_videos", paths=[str(folder)], project=project)
    assert not failed and again["files"] == answer["files"], again
    checks.append("import_dedupes_by_path")

    deadline = time.monotonic() + 15
    while True:
        failed, state = tool("get_project", project=project)
        assert not failed, state
        if state["files"][0]["duration_s"] is not None or time.monotonic() > deadline:
            break
        time.sleep(0.2)
    assert abs(state["files"][0]["duration_s"] - 0.2) < 0.001, state
    assert state["title"] == "ทดสอบ agent" and state["language"] == "th" and not state["transcription_ready"], state
    assert state["files"][0]["transcription"]["state"] == "not_started" and state["export"]["state"] == "idle", state
    checks.append("project_state_readback")

    failed, answer = tool("transcribe", project=project)
    assert failed and answer["error"] == "transcription_not_configured", answer
    failed, answer = tool("get_transcript", project=project)
    assert failed and answer["error"] == "transcript_not_ready", answer
    failed, answer = tool("set_clips", project=project, clips=[{"title": "x", "segment_ids": ["missing"]}])
    assert failed and answer["error"] == "segment_unknown:missing", answer
    failed, answer = tool("export_clips", project=project)
    assert failed and answer["error"] == "transcription_not_configured", answer
    failed, answer = tool("get_project", project="../escape")
    assert failed and answer["error"] == "project_required", answer
    checks.append("missing_engine_and_bad_input_are_tool_errors")

    replies = bridge(number, [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "get_project", "arguments": {"project": project}}}),
    ])
    assert [r["id"] for r in replies] == [1, 2], replies
    assert json.loads(replies[1]["result"]["content"][0]["text"])["project"] == project, replies
    closed = bridge(port(), [json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})])
    assert closed[0]["id"] == 9 and "not running" in closed[0]["error"]["message"], closed
    checks.append("stdio_bridge")
    return checks


if __name__ == "__main__":
    main()
