"""Native host operations; paths and subprocess arguments stay separate from UI code."""
from __future__ import annotations

import json
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import atomic

HF_CLEANUP_SHA = "580371f7be167b57d27cf0ed756d9ec3c6573e609aa350cb9fd2e9120c926777"


def hyperframes_command(command: list[str], project: Path, env: dict[str, str]) -> list[str]:
    """Keep Mac preview/render cleanup inside the process tree EditorKrab owns."""
    if not is_macos():
        return command
    if command[2:3] != ["hyperframes@0.8.101"] or command[3] not in {"preview", "render", "check"}:
        raise RuntimeError("Mac launcher needs review for this HyperFrames version/command")
    node = shutil.which("node", path=env.get("PATH"))
    if not node:
        raise RuntimeError("Node.js is missing")
    cache = Path(__file__).resolve().parents[1] / "state" / "hyperframes-npm"
    runtime = cache.parent / "hyperframes"
    if any(p.is_symlink() for p in (cache, runtime, cache.parent)):
        raise RuntimeError("HyperFrames cache must not be linked to another directory")
    env["npm_config_cache"] = str(cache)
    env["PWD"] = str(project.resolve())
    module = runtime / "node_modules/hyperframes/dist/chunk-LAOCA3MQ.js"
    if not module.resolve().is_relative_to(runtime.resolve()):
        raise RuntimeError("HyperFrames module is outside the private runtime")
    if not module.is_file():
        npm = shutil.which("npm", path=env.get("PATH"))
        if not npm:
            raise RuntimeError("npm is missing")
        install = subprocess.run([npm, "install", "--prefix", str(runtime), "--no-save", "--no-package-lock",
                                  "--no-audit", "--no-fund", command[2]], cwd=project, env=env,
                                 capture_output=True, text=True, timeout=240)
        if install.returncode:
            raise RuntimeError("HyperFrames preparation failed: " + (install.stderr or install.stdout)[-600:])
    if not module.is_file() or not module.resolve().is_relative_to(runtime.resolve()):
        raise RuntimeError("Pinned HyperFrames cleanup module not found in the private runtime")
    source = module.read_bytes()
    entry = b"function killOrphanedProcesses() {"
    guarded = entry + b"\n  return 0;"
    original = source.replace(guarded, entry, 1)
    if hashlib.sha256(original).hexdigest() != HF_CLEANUP_SHA or original.count(entry) != 1:
        raise RuntimeError("Pinned HyperFrames cleanup module changed; refusing to run it")
    # shortcut: pinned internal module; use upstream scoped cleanup when it is available.
    if source == original:
        atomic.write_text(module, source.replace(entry, guarded, 1).decode("utf-8"), encoding="utf-8")
    cli = module.parent.parent / "bin" / "hyperframes.mjs"
    if not cli.is_file():
        raise RuntimeError("Pinned HyperFrames launcher is missing")
    args = command[3:]
    if args[0] == "preview" and "--foreground" not in args:
        args = [*args, "--foreground"]
    return [node, str(cli), *args]


def is_macos() -> bool:
    return platform.system() == "Darwin"


def runtime_environment(config: dict[str, Any] | None = None) -> dict[str, str]:
    """Add setup-recorded executable folders without dropping the user's existing PATH."""
    configured = (config or {}).get("runtime_bin_dirs", [])
    if not isinstance(configured, list) or not all(isinstance(value, str) for value in configured):
        raise ValueError("runtime_bin_dirs must be a list of absolute directory paths")
    directories = []
    for value in configured:
        path = Path(value).expanduser()
        if not path.is_absolute() or not path.is_dir() or os.pathsep in value:
            raise ValueError("runtime_bin_dirs contains an invalid directory")
        resolved = str(path.resolve())
        if resolved not in directories:
            directories.append(resolved)
    environment = dict(os.environ)
    if directories:
        environment["PATH"] = os.pathsep.join(directories + [environment.get("PATH", "")])
    return environment


def capcut_data_candidates(config: dict[str, Any] | None = None) -> list[Path]:
    config = config or {}
    if config.get("capcut_user_data"):
        return [Path(config["capcut_user_data"]).expanduser()]
    if is_macos():
        home = Path.home()
        return [home / "Movies" / "CapCut" / "User Data",
                home / "Library" / "Application Support" / "CapCut" / "User Data",
                home / "Library" / "Containers" / "com.lemon.lvoverseas" / "Data" /
                "Movies" / "CapCut" / "User Data"]
    return [Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "User Data"]


def capcut_data_root(config: dict[str, Any] | None = None) -> Path:
    candidates = capcut_data_candidates(config)
    for path in candidates:
        if ((path / "Config" / "globalSetting").is_file() or
                (path / "Projects" / "com.lveditor.draft" / "root_meta_info.json").is_file()):
            return path
    return next((path for path in candidates if path.is_dir()), candidates[0])


def capcut_registry(config: dict[str, Any] | None = None) -> Path:
    config = config or {}
    if config.get("capcut_root_meta"):
        return Path(config["capcut_root_meta"]).expanduser()
    return capcut_data_root(config) / "Projects" / "com.lveditor.draft" / "root_meta_info.json"


def capcut_bundle(config: dict[str, Any] | None = None) -> Path:
    config = config or {}
    candidates = ([Path(config["capcut_app"]).expanduser()] if config.get("capcut_app") else
                  [Path("/Applications/CapCut.app"), Path.home() / "Applications" / "CapCut.app"])
    for path in candidates:
        if path.is_absolute() and path.is_dir() and path.suffix.lower() == ".app":
            return path
    raise FileNotFoundError("CapCut.app not found; set capcut_app in config.json")


def capcut_resources(config: dict[str, Any] | None = None) -> Path:
    config = config or {}
    if config.get("capcut_resources"):
        path = Path(config["capcut_resources"]).expanduser()
        if not path.is_dir():
            raise FileNotFoundError("Configured CapCut resources folder does not exist")
        return path
    return capcut_bundle(config) / "Contents" / "Resources"


def font_directories(config: dict[str, Any] | None = None) -> list[Path]:
    config = config or {}
    extra = [Path(config["fonts_dir"]).expanduser()] if config.get("fonts_dir") else []
    if extra and not extra[0].is_dir():
        raise FileNotFoundError("Configured fonts folder does not exist")
    if is_macos():
        return extra + [Path.home() / "Library" / "Fonts", Path("/Library/Fonts"),
                        Path("/System/Library/Fonts"),
                        Path(__file__).resolve().parents[1] / "fonts"]
    return extra + [Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
                    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]


def _font_files(config: dict[str, Any] | None = None):
    seen: set[Path] = set()
    for directory in font_directories(config):
        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".ttc"} and path not in seen:
                    seen.add(path)
                    yield path


def _font_family(path: Path) -> str:
    from fontTools.ttLib import TTFont
    with TTFont(str(path), fontNumber=0, lazy=True) as font:
        return font["name"].getDebugName(16) or font["name"].getDebugName(1) or path.stem


def find_font(name: str, config: dict[str, Any] | None = None) -> Path | None:
    if not name:
        raise ValueError("Font name is empty")
    direct = Path(name).expanduser()
    if direct.is_file():
        return direct
    files = list(_font_files(config))
    for path in files:
        if path.stem.casefold() == name.casefold() or path.name.casefold() == name.casefold():
            return path
    if is_macos():
        for path in files:
            if _font_family(path).casefold() == name.casefold():
                return path
    return None


def font_names(config: dict[str, Any] | None = None) -> list[str]:
    return sorted({_font_family(path) for path in _font_files(config)}, key=str.casefold)


def mac_template_paths(text: str, config: dict[str, Any] | None = None) -> str:
    """Resolve existing Windows template tokens using this Mac's configured locations."""
    config = config or {}
    resources = capcut_resources(config).as_posix()
    data = capcut_data_root(config).as_posix()
    font_prefix = "{LOCALAPPDATA}/Microsoft/Windows/Fonts/"

    def walk(value):
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, str):
            return value
        # Text materials contain a second JSON object encoded inside the outer draft.
        if value.lstrip().startswith(("{", "[")):
            try:
                nested = json.loads(value)
            except ValueError:
                pass  # ordinary text may start with a brace
            else:
                return json.dumps(walk(nested), ensure_ascii=False)
        if value.startswith(font_prefix):
            name = Path(value.removeprefix(font_prefix)).name
            found = find_font(name, config)
            # The caller reports missing fonts and substitutes its bundled Kanit font.
            return (found or font_directories(config)[0] / name).as_posix()
        return value.replace("{CAPCUT_APP}/Resources", resources).replace(
            "{LOCALAPPDATA}/CapCut/User Data", data)

    return json.dumps(walk(json.loads(text)), ensure_ascii=False)


MAC_PICK = """
use framework "AppKit"
use framework "Foundation"
use scripting additions
on run argv
    set mode to item 1 of argv
    set panel to current application's NSOpenPanel's openPanel()
    panel's setAllowsMultipleSelection:false
    panel's setCanChooseFiles:(mode is not "folder")
    panel's setCanChooseDirectories:(mode is not "file")
    if (count of argv) > 1 then
        panel's setDirectoryURL:(current application's NSURL's fileURLWithPath:(item 2 of argv))
    end if
    set applicationObject to current application's NSApplication's sharedApplication()
    applicationObject's activateIgnoringOtherApps:true
    if (panel's runModal() as integer) is 1 then
        return ((panel's URL()'s |path|()) as text)
    end if
    return ""
end run
"""


def pick_path(start: str = "", kind: str = "both") -> str:
    if kind not in {"both", "file", "folder"}:
        raise ValueError("Invalid picker kind")
    args = ["/usr/bin/osascript", "-e", MAC_PICK, "--", kind]
    if start:
        path = Path(start).expanduser()
        if path.is_file():
            path = path.parent
        if not path.is_dir():
            raise ValueError("Picker start folder does not exist")
        args.append(str(path.resolve()))
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=900)
    if result.returncode:
        raise OSError("Native file picker failed: " + result.stderr.strip()[-200:])
    selected = result.stdout.strip()
    if selected and not Path(selected).exists():
        raise OSError("Native file picker returned a missing path")
    return selected


def open_capcut(config: dict[str, Any] | None = None) -> None:
    bundle = capcut_bundle(config)
    result = subprocess.run(["/usr/bin/open", str(bundle)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise OSError("CapCut could not open: " + result.stderr.strip()[-200:])


def move_to_trash(path: Path) -> None:
    path = path.resolve(strict=True)
    if not path.is_dir() or path == Path(path.anchor) or path == Path.home().resolve():
        raise ValueError("Invalid project folder")
    script = '''on run argv
tell application "Finder" to delete (POSIX file (item 1 of argv))
end run'''
    result = subprocess.run(["/usr/bin/osascript", "-e", script, "--", str(path)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode or path.exists():
        raise OSError("Could not move project to Trash: " + result.stderr.strip()[-200:])


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created: float
    parent: int
    executable: str
    command: tuple[str, ...]


def process_identity(pid: int) -> ProcessIdentity:
    import psutil
    if pid <= 1:
        raise ValueError("Invalid process ID")
    proc = psutil.Process(pid)
    if hasattr(os, "getuid") and proc.uids().real != os.getuid():
        raise RuntimeError("Process belongs to another user")
    return ProcessIdentity(pid, proc.create_time(), proc.ppid(), str(Path(proc.exe()).resolve()),
                           tuple(proc.cmdline()))


def same_process(before: ProcessIdentity, current: ProcessIdentity, *, reparented: bool = False) -> bool:
    parent_ok = before.parent == current.parent or (reparented and current.parent == 1)
    return (before.pid == current.pid and before.created == current.created and parent_ok and
            before.executable == current.executable and before.command == current.command)


def capcut_running() -> bool:
    import psutil
    for proc in psutil.process_iter(["name", "exe"]):
        name, exe = proc.info.get("name") or "", proc.info.get("exe") or ""
        if "capcut" in name.casefold() or "/capcut.app/contents/" in exe.casefold():
            return True
    return False


def is_preview_process(child: ProcessIdentity) -> bool:
    return ("preview" in child.command and any(
        arg == "hyperframes" or arg.startswith("hyperframes@") or "hyperframes" in Path(arg).parts
        for arg in child.command))


def _is_controller(child: ProcessIdentity, owner: ProcessIdentity) -> bool:
    command = child.command
    if (len(command) < 4 or len(command) % 2 or child.executable != owner.executable or
            not Path(command[1]).is_absolute() or
            Path(command[1]).resolve() != Path(__file__).resolve().with_name("control.py")):
        return False
    options = dict(zip(command[2::2], command[3::2]))
    required = {"--action", "--port", "--python", "--expected-pid", "--expected-created", "--issued-at"}
    try:
        return (set(options) == required and len(command) == 14 and
                options["--action"] in {"restart", "shutdown"} and
                int(options["--expected-pid"]) == owner.pid and
                float(options["--expected-created"]) == owner.created and
                1 <= int(options["--port"]) <= 65535 and
                Path(options["--python"]).is_absolute() and
                Path(options["--python"]).resolve() == Path(sys.executable).resolve() and
                -5 <= time.time() - int(options["--issued-at"]) <= 20)
    except (ValueError, OSError):
        return False


def server_has_active_helpers(owner: ProcessIdentity) -> bool:
    """Recheck live workers at the server boundary, excluding verified control and preview trees."""
    import psutil
    if not same_process(owner, process_identity(owner.pid)):
        raise RuntimeError("Server process identity changed")
    snapshots = []
    for child in psutil.Process(owner.pid).children(recursive=True):
        try:
            snapshots.append(process_identity(child.pid))
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, RuntimeError):
            return True  # an unreadable live child cannot be declared idle
    excluded = {child.pid for child in snapshots if
                _is_controller(child, owner) or is_preview_process(child)}
    changed = True
    while changed:
        changed = False
        for child in snapshots:
            if child.parent in excluded and child.pid not in excluded:
                excluded.add(child.pid)
                changed = True
    return any(child.pid not in excluded for child in snapshots)


def stop_owned_preview(proc, expected: ProcessIdentity | None) -> None:
    """Stop only the preview tree whose identity was recorded when this server started it."""
    import psutil
    if proc.poll() is not None:
        return
    if expected is None or not same_process(expected, process_identity(proc.pid)):
        raise RuntimeError("Preview process identity changed; refusing to stop it")
    root = psutil.Process(proc.pid)
    children = []
    for child in root.children(recursive=True):
        try:
            children.append(process_identity(child.pid))
        except psutil.NoSuchProcess:
            continue  # Includes a child that became a zombie during enumeration.
    if not same_process(expected, process_identity(proc.pid)):
        raise RuntimeError("Preview process identity changed; refusing to stop it")
    for snapshot in reversed(children):
        try:
            if same_process(snapshot, process_identity(snapshot.pid), reparented=True):
                child = psutil.Process(snapshot.pid)
                if same_process(snapshot, process_identity(snapshot.pid), reparented=True):
                    child.terminate()
                    child.wait(timeout=5)
        except psutil.NoSuchProcess:
            continue
        except psutil.TimeoutExpired as exc:
            raise RuntimeError("Owned preview child did not stop") from exc
    try:
        current = process_identity(proc.pid)
    except psutil.NoSuchProcess:
        return
    if not same_process(expected, current):
        raise RuntimeError("Preview process identity changed; refusing to stop it")
    root.terminate()
    try:
        root.wait(timeout=10)
    except psutil.TimeoutExpired as exc:
        raise RuntimeError("Owned preview did not stop") from exc
