"""Keep an isolated native ASR browser fixture running; tiny is test-only, never a product default."""
from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import threading
import os
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from rust_jobs_test import EXE, FLAGS, ROOT, ready, request, stop

PORT = 18871
PROJECT = "native-browser-check"
SOURCE = "native-one"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unconfigured", action="store_true", help="Run without a native speech model on port 18872")
    args = parser.parse_args()
    number = 18872 if args.unconfigured else PORT
    native = ROOT / "rust" / "target" / "native"
    engine = native / "whisper-v1.9.2-win-x64" / "Release" / "whisper-cli.exe"
    model = native / "ggml-tiny.bin"
    original = native / "jfk.wav"
    for path in (EXE, engine, model, original):
        assert path.is_file(), f"Fixture prerequisite missing: {path}"
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg and shutil.which("ffprobe"), "Native FFmpeg/ffprobe required"
    # Refuse an occupied fixture port before creating or replacing any fixture files.
    with socket.socket() as check:
        check.bind(("127.0.0.1", number))
    folder = ROOT / "output" / "transcription-browser-fixture" / str(uuid.uuid4())
    assert folder.resolve().is_relative_to(ROOT.resolve()), "Fixture must stay inside this repository"
    folder.mkdir(parents=True)
    source = folder / "บทพูดทดสอบ.wav"
    shutil.copyfile(original, source)
    silent = folder / "ภาพไม่มีเสียง.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=16x16:r=10:d=0.2",
                    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)],
                   check=True, capture_output=True, creationflags=FLAGS)

    def selected_file(path, source_id):
        metadata = path.stat()
        return dict(source_id=source_id, name=path.name, path=str(path),
                    size=metadata.st_size, modified_ns=str(metadata.st_mtime_ns))

    selected = selected_file(source, SOURCE)
    selected_silent = selected_file(silent, "silent-one")
    state = folder / "state.sqlite"
    base = f"http://127.0.0.1:{number}"
    native_env = {**os.environ, "EDITORKRAB_RUST_STATE": str(state), "EDITORKRAB_RUST_PORT": str(number)}
    for key in ("EDITORKRAB_WHISPER", "EDITORKRAB_WHISPER_MODEL", "EDITORKRAB_FFMPEG"):
        native_env.pop(key, None)
    # The app also finds tools in its data folder; keep that inside the fixture, never the real one.
    data = folder / "data"
    native_env["EDITORKRAB_DATA"] = str(data)
    native_env.pop("EDITORKRAB_DOWNLOAD_MANIFEST", None)
    model_server = None
    if not args.unconfigured:
        native_env.update(EDITORKRAB_WHISPER=str(engine), EDITORKRAB_WHISPER_MODEL=str(model),
                          EDITORKRAB_FFMPEG=ffmpeg)
    else:
        # The in-app download gets the tiny model from a local server instead of 3.1 GB of large-v3;
        # the runtime and ffmpeg are already in place, as an installed app would have them.
        shutil.copytree(engine.parent, data / "whisper")
        (data / "ffmpeg").mkdir(parents=True)
        shutil.copyfile(ffmpeg, data / "ffmpeg" / Path(ffmpeg).name)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(model.parent))
        model_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=model_server.serve_forever, daemon=True).start()
        manifest = folder / "download-manifest.json"
        manifest.write_text(json.dumps(dict(
            url=f"http://127.0.0.1:{model_server.server_address[1]}/{model.name}",
            size=model.stat().st_size, sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
            dest=str(data / "models" / model.name))), encoding="utf-8")
        native_env["EDITORKRAB_DOWNLOAD_MANIFEST"] = str(manifest)
    process = None
    try:
        process = subprocess.Popen([str(EXE)], env=native_env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, creationflags=FLAGS)
        ready(process, base)
        status, settings = request(base, "GET", "/api/v1/transcription/settings")
        assert status == 200, (status, settings)
        if args.unconfigured:
            assert settings is None, settings
        else:
            assert settings is not None, settings
            assert Path(settings["model"]["path"]).samefile(model)
            assert Path(settings["engine"]["path"]).samefile(engine)
        document = dict(flow_version=2, step=1, title="ทดสอบถอดเสียง Native", language="auto",
                        files=[selected, selected_silent], plan="", style="bold16", headline_items=[], text_roles=[])
        status, saved = request(base, "PUT", f"/api/v1/drafts/{PROJECT}",
                                dict(operation_id="fixture-initial", expected_revision=0, document=document))
        assert status == 200 and saved["revision"] == 1, (status, saved)
        evidence = dict(base=base, project=PROJECT, source=selected, silent_source=selected_silent,
                        url=f"{base}/?project={PROJECT}", state=str(state), server_pid=process.pid,
                        integration_fixture_only=True, download=model_server is not None,
                        model="not configured" if args.unconfigured else "multilingual tiny; not production")
        evidence_name = "transcription-browser-unconfigured.json" if args.unconfigured else "transcription-browser-fixture.json"
        (ROOT / "output" / evidence_name).write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False), flush=True)
        while process.poll() is None:
            time.sleep(.5)
        raise RuntimeError(f"Fixture service stopped unexpectedly: {process.returncode}")
    except KeyboardInterrupt:
        pass
    finally:
        if process is not None:
            stop(process)
        if model_server is not None:
            model_server.shutdown()


if __name__ == "__main__":
    main()
