"""Check this machine is ready for ClipKit. Prints one line per check; exits 1 if any check fails.

    python scripts/doctor.py           # quick checks
    python scripts/doctor.py --asr     # also transcribe 3 s of silence-free test tone to prove Whisper loads
"""
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "capcut"))
import kitconfig  # apply the configured tool PATH before checking ffmpeg and Node
results = []
sys.stdout.reconfigure(encoding="utf-8")


def check(name, fn, optional=False):
    try:
        detail = fn()
        results.append((True, name, detail or ""))
    except Exception as e:  # report every failure, keep checking the rest
        results.append(("WARN" if optional else False, name, str(e)))
    ok, _, detail = results[-1]
    # printed as each check ends, so the app can name the check that hung when it times out
    print(f"{'WARN' if ok == 'WARN' else 'OK  ' if ok else 'FAIL'} {name:18} {detail}", flush=True)


def py():
    if sys.version_info < (3, 10):
        raise RuntimeError(f"Python {sys.version.split()[0]}; need 3.10+")
    return sys.version.split()[0]


def modules():
    missing = [m for m in ("faster_whisper", "pythainlp", "soundfile", "ctranslate2", "fastapi", "uvicorn", "cv2", "fontTools", "psutil") if not importlib.util.find_spec(m)]
    if missing:
        setup = "scripts/setup.sh" if sys.platform == "darwin" else "scripts/setup.ps1"
        raise RuntimeError("missing: " + ", ".join(missing) + " - run " + setup)


def tools():
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        raise RuntimeError("not on PATH: " + ", ".join(missing))
    for option, feature in (("-filters", "subtitles"), ("-encoders", "libx264")):
        out = subprocess.run(["ffmpeg", "-hide_banner", option], capture_output=True, text=True,
                             check=True, timeout=30).stdout
        if not re.search(r"^\s+\S+\s+" + feature + r"\s", out, re.M):
            raise RuntimeError("ffmpeg is missing " + feature + " support; install a compatible build")


def node():
    # the MP4 export and the live preview run HyperFrames through npx
    if not shutil.which("npx"):
        raise RuntimeError("Node.js not found (npx missing) - install Node.js LTS from nodejs.org")
    return subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()


def config():
    import kitconfig
    bad = [f"{k}={kitconfig.CFG.get(k)}" for k in ("capcut_drafts", "stock_video", "stock_music", "work_root")
           if not kitconfig.CFG.get(k) or not Path(kitconfig.CFG[k]).is_dir()]
    if bad:
        raise RuntimeError("folders not found: " + "; ".join(bad))
    return "config.json OK"


def team_workspace():
    import kitconfig
    import tempfile
    configured=kitconfig.CFG.get('team_workspaces',{})
    if not configured:return 'no team workspace configured'
    for item in configured.values():
        root=Path(item['path'])
        for folder in ('footage','exports'):
            target=root/folder
            if not target.is_dir():raise RuntimeError(f'team folder missing: {target}')
            with tempfile.TemporaryFile(dir=target) as probe:probe.write(b'check');probe.flush()
    return f'{len(configured)} team workspace(s) writable'


def font():
    import kitconfig
    return kitconfig.font_file()


def gpu():
    import kitconfig
    if kitconfig.CFG.get("whisper_device", "cpu" if sys.platform == "darwin" else "cuda") != "cuda":
        return "CPU mode (slow)"
    if not shutil.which("nvidia-smi"):
        raise RuntimeError("no NVIDIA GPU found; set whisper_device to cpu in config.json")
    out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                         capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    return out


def model():
    import kitconfig
    from faster_whisper.utils import download_model
    name = kitconfig.CFG.get("whisper_model") or "large-v3"
    try:
        # same folder fetch_model.py downloads into (models_dir), else the default cache
        download_model(name, local_files_only=True, cache_dir=kitconfig.CFG.get("models_dir") or None)
    except Exception as exc:
        raise RuntimeError(f"Whisper {name} not downloaded yet - press Install (or run scripts/fetch_model.py)") from exc
    return name


def disk():
    low = []
    for d in {Path.home().anchor, *(Path(p).anchor for p in [ROOT])}:
        free = shutil.disk_usage(d).free / 2**30
        if free < 5:
            low.append(f"{d} {free:.1f} GB")
    if low:
        raise RuntimeError("less than 5 GB free: " + ", ".join(low))


def asr():
    import tempfile
    wav = Path(tempfile.gettempdir()) / "clipkit_doctor.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=3", str(wav)], check=True, timeout=60)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "clipkit.py"), "transcribe", str(wav), "--out",
                        str(wav.with_suffix(".json")), "--language", "en"], capture_output=True, text=True, timeout=300)
    # a pure tone has no speech: "no speech found" proves the model loaded and ran
    if r.returncode != 0 and "no speech found" not in r.stderr:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr else "transcribe failed")
    return "Whisper loaded and ran"


for n, f in [("python", py), ("python packages", modules), ("ffmpeg", tools), ("node", node), ("config + folders", config),
             ("team workspace", team_workspace), ("GPU", gpu), ("disk space", disk)]:
    check(n, f)
if "--skip-model" in sys.argv:
    print("WARN speech model       skipped by request; transcription readiness has not been checked", flush=True)
else:
    check("speech model", model)
# fonts are each editor's own choice; only the preview render needs "card_font" installed
check("preview font", font, optional=True)
if "--asr" in sys.argv:
    check("speech-to-text", asr)

sys.exit(0 if all(r[0] for r in results) else 1)
