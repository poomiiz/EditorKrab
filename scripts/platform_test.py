"""Host adapters and ownership regressions; native apps and process signals are mocked."""
import importlib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))

import control
import host_platform as host
import kit_settings
import window_lifecycle
from fastapi import FastAPI
from fastapi.testclient import TestClient


class PlatformTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "Native AppleScript compiler is macOS-only")
    def test_mac_picker_script_compiles_without_opening_a_dialog(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(["/usr/bin/osacompile", "-o", str(Path(folder) / "picker.scpt"),
                                     "-e", host.MAC_PICK], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_mac_capcut_movies_and_library_registry_candidates(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(host, "is_macos", return_value=True), \
                patch.object(host.Path, "home", return_value=Path(folder)):
            movies = Path(folder) / "Movies" / "CapCut" / "User Data"
            movies.mkdir(parents=True)
            library = Path(folder) / "Library" / "Application Support" / "CapCut" / "User Data"
            meta = library / "Projects" / "com.lveditor.draft" / "root_meta_info.json"
            meta.parent.mkdir(parents=True)
            meta.write_text('{"all_draft_store": []}')
            self.assertEqual(host.capcut_data_root(), library)
            self.assertEqual(host.capcut_registry(), meta)
            self.assertEqual(host.capcut_registry({"capcut_root_meta": str(meta)}), meta)
            custom = Path(folder) / "custom drafts"
            custom.mkdir()
            setting = library / "Config" / "globalSetting"
            setting.parent.mkdir()
            setting.write_text("currentCustomDraftPath=" + str(custom), encoding="utf-8")
            with patch.object(kit_settings, "_read_config", return_value={}):
                self.assertEqual(kit_settings.capcut_default_drafts(), str(custom))

    def test_windows_capcut_root_is_unchanged(self):
        with patch.object(host, "is_macos", return_value=False), \
                patch.dict(os.environ, {"LOCALAPPDATA": str(ROOT)}):
            self.assertEqual(host.capcut_registry(),
                             ROOT / "CapCut" / "User Data" / "Projects" /
                             "com.lveditor.draft" / "root_meta_info.json")

    def test_layout_uses_platform_path_separators(self):
        self.assertEqual(kit_settings.WORKSPACE_LAYOUT["stock_video"], str(Path("stock") / "video"))
        self.assertEqual(kit_settings.WORKSPACE_LAYOUT["stock_music"], str(Path("stock") / "music"))

    def test_mac_picker_passes_untrusted_paths_as_argv_not_applescript(self):
        with tempfile.TemporaryDirectory(prefix="editor ' $ ") as folder, \
                patch.object(host.subprocess, "run") as run:
            selected = Path(folder) / "clip ' $watch.mp4"
            selected.touch()
            run.return_value = SimpleNamespace(returncode=0, stdout=str(selected) + "\n", stderr="")
            self.assertEqual(host.pick_path(folder, "both"), str(selected))
            argv = run.call_args.args[0]
            self.assertEqual(argv[:2], ["/usr/bin/osascript", "-e"])
            self.assertEqual(argv[-3:], ["--", "both", str(Path(folder).resolve())])
            self.assertNotIn(folder, argv[2])
            self.assertNotIn("shell", run.call_args.kwargs)
            run.return_value.stdout = ""
            self.assertEqual(host.pick_path(folder), "")

    def test_picker_rejects_invalid_kind_and_missing_start_without_launch(self):
        with patch.object(host.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                host.pick_path(kind="shell")
            with self.assertRaises(ValueError):
                host.pick_path(str(ROOT / "missing-start-folder"))
            run.assert_not_called()

    def test_mac_native_picker_errors_are_not_hidden(self):
        with patch.object(host.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stdout="", stderr="permission denied")):
            with self.assertRaisesRegex(OSError, "permission denied"):
                host.pick_path()
        with patch.object(host, "is_macos", return_value=True), \
                patch.object(host, "pick_path", side_effect=OSError("picker failed")):
            with self.assertRaises(kit_settings.HTTPException) as failure:
                kit_settings.pick()
            self.assertEqual(failure.exception.status_code, 500)

    def test_mac_browse_routes_select_the_requested_kind(self):
        with patch.object(host, "is_macos", return_value=True), \
                patch.object(kit_settings, "pick", return_value={"path": "chosen", "kind": "folder"}) as pick:
            self.assertEqual(kit_settings.browse(), {"path": "chosen"})
            pick.assert_called_with("", "folder")
            pick.return_value = {"path": "chosen.mp4", "kind": "file"}
            self.assertEqual(kit_settings.browse_file(), {"path": "chosen.mp4"})
            pick.assert_called_with("", "file")

    def test_capcut_open_uses_only_a_validated_bundle_argument(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(host.subprocess, "run") as run:
            bundle = Path(folder) / "CapCut ' $.app"
            bundle.mkdir()
            run.return_value = SimpleNamespace(returncode=0, stderr="")
            host.open_capcut({"capcut_app": str(bundle)})
            self.assertEqual(run.call_args.args[0], ["/usr/bin/open", str(bundle)])
            run.reset_mock()
            with self.assertRaises(FileNotFoundError):
                host.open_capcut({"capcut_app": str(bundle / "missing.app")})
            run.assert_not_called()

    def test_trash_passes_path_in_argv_and_reports_failure(self):
        with tempfile.TemporaryDirectory(prefix="trash ' $ ") as folder:
            project = Path(folder) / "project ' $"
            project.mkdir()
            with patch.object(host.subprocess, "run", return_value=SimpleNamespace(
                    returncode=1, stderr="denied")) as run:
                with self.assertRaises(OSError):
                    host.move_to_trash(project)
                argv = run.call_args.args[0]
                self.assertEqual(argv[-2:], ["--", str(project.resolve())])
                self.assertNotIn(str(project), argv[2])
                self.assertTrue(project.is_dir())

    def test_mac_capcut_template_rewrites_nested_font_and_resource_json(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(host, "is_macos", return_value=True):
            base = Path(folder)
            resources = base / "resources ' $"
            resources.mkdir()
            fonts = base / "fonts"
            fonts.mkdir()
            font = fonts / "Example.ttf"
            font.touch()
            data = base / "user data"
            data.mkdir()
            text = json.dumps({
                "font_path": "{LOCALAPPDATA}/Microsoft/Windows/Fonts/Example.ttf",
                "content": json.dumps({"font": {"path":
                    "{LOCALAPPDATA}/Microsoft/Windows/Fonts/Example.ttf"}}),
                "effect": "{LOCALAPPDATA}/CapCut/User Data/Cache/effect/test",
                "resource": "{CAPCUT_APP}/Resources/Font/SystemFont/en.ttf"})
            result = json.loads(host.mac_template_paths(text, {
                "fonts_dir": str(fonts), "capcut_resources": str(resources),
                "capcut_user_data": str(data)}))
            self.assertEqual(result["font_path"], font.as_posix())
            self.assertEqual(json.loads(result["content"])["font"]["path"], font.as_posix())
            self.assertEqual(result["effect"], (data / "Cache" / "effect" / "test").as_posix())
            self.assertEqual(result["resource"], (resources / "Font" / "SystemFont" / "en.ttf").as_posix())
            self.assertNotIn("{LOCALAPPDATA}", json.dumps(result))

    def test_missing_mac_template_font_remains_detectable_for_explicit_warning(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(host, "is_macos", return_value=True), \
                patch.object(host, "find_font", return_value=None), \
                patch.object(host, "font_directories", return_value=[Path(folder)]):
            result = json.loads(host.mac_template_paths(
                '{"font": "{LOCALAPPDATA}/Microsoft/Windows/Fonts/Missing.ttf"}',
                {"capcut_resources": folder, "capcut_user_data": folder}))
            self.assertEqual(result["font"], (Path(folder) / "Missing.ttf").as_posix())
            self.assertFalse(Path(result["font"]).is_file())

    def test_stale_preview_identity_does_not_signal_any_process(self):
        old = host.ProcessIdentity(4201, 10, 100, "node", ("node", "preview"))
        proc = Mock(pid=4201)
        proc.poll.return_value = None
        with patch.object(host, "process_identity", return_value=replace(old, created=11)), \
                patch("psutil.Process") as process:
            with self.assertRaisesRegex(RuntimeError, "identity changed"):
                host.stop_owned_preview(proc, old)
            process.assert_not_called()

    def test_preview_child_reused_after_process_lookup_is_not_signalled(self):
        owner = host.ProcessIdentity(4201, 10, 100, "node", ("node", "preview"))
        child = replace(owner, pid=4202, parent=4201)
        current_child = [child]
        root = Mock()
        root.children.return_value = [SimpleNamespace(pid=4202)]
        child_process = Mock()
        proc = Mock(pid=4201)
        proc.poll.return_value = None
        def process(pid):
            if pid == owner.pid:
                return root
            current_child[0] = replace(child, created=11)
            return child_process
        with patch.object(host, "process_identity", side_effect=lambda pid:
                          owner if pid == owner.pid else current_child[0]), \
                patch("psutil.Process", side_effect=process):
            host.stop_owned_preview(proc, owner)
            child_process.terminate.assert_not_called()
            root.terminate.assert_called_once()

    def test_preview_child_exits_during_snapshot_without_signalling_unknown_pid(self):
        import psutil
        owner = host.ProcessIdentity(4201, 10, 100, "node", ("node", "preview"))
        for error in (psutil.NoSuchProcess(4202), psutil.ZombieProcess(4202), psutil.AccessDenied(4202)):
            with self.subTest(error=type(error).__name__):
                root = Mock();root.children.return_value = [SimpleNamespace(pid=4202)]
                proc = Mock(pid=4201);proc.poll.return_value = None
                def identity(pid):
                    if pid == owner.pid:
                        return owner
                    raise error
                with patch.object(host, "process_identity", side_effect=identity), patch("psutil.Process", return_value=root) as process:
                    if isinstance(error, psutil.NoSuchProcess):
                        host.stop_owned_preview(proc, owner)
                        root.terminate.assert_called_once()
                    else:
                        with self.assertRaises(psutil.AccessDenied):
                            host.stop_owned_preview(proc, owner)
                        root.terminate.assert_not_called()
                    self.assertTrue(all(call.args == (owner.pid,) for call in process.call_args_list))

    def test_server_worker_check_excludes_only_verified_controller_and_preview_trees(self):
        owner = host.ProcessIdentity(4201, 123.0, 100, str(Path(sys.executable).resolve()),
                                     (sys.executable, str(control.APP)))
        controller = replace(owner, pid=4202, parent=4201, command=(
            sys.executable, str(ROOT / "app" / "control.py"), "--action", "restart", "--port", "8770",
            "--python", sys.executable, "--expected-pid", "4201", "--expected-created", "123.0",
            "--issued-at", str(int(time.time()))))
        controller_child = replace(owner, pid=4203, parent=4202, command=("worker",))
        preview = replace(owner, pid=4204, parent=4201, command=("node", "hyperframes@0.8.101", "preview"))
        preview_child = replace(owner, pid=4205, parent=4204, command=("browser",))
        export = replace(owner, pid=4210, parent=4201, command=("ffmpeg", "-i", "clip.mp4", "out.mp4"))
        identities = {item.pid: item for item in
                      (owner, controller, controller_child, preview, preview_child, export)}
        proc = Mock()
        proc.children.return_value = [SimpleNamespace(pid=pid) for pid in (4202, 4203, 4204, 4205)]
        with patch.object(host, "process_identity", side_effect=lambda pid: identities[pid]), \
                patch("psutil.Process", return_value=proc):
            self.assertFalse(host.server_has_active_helpers(owner))
            proc.children.return_value.append(SimpleNamespace(pid=4210))
            self.assertTrue(host.server_has_active_helpers(owner))
            proc.children.return_value = [SimpleNamespace(pid=4202)]
            identities[4202] = replace(controller, command=controller.command[:-3] + (
                "999.0", "--issued-at", str(int(time.time()))))
            self.assertTrue(host.server_has_active_helpers(owner))

    def test_mac_studio_stop_never_runs_taskkill_or_global_sweep(self):
        proc = Mock()
        proc.poll.return_value = None
        previous = dict(kit_settings._hf_preview)
        try:
            kit_settings._hf_preview.clear()
            kit_settings._hf_preview.update(proc=proc, identity="recorded")
            with patch.object(host, "is_macos", return_value=True), \
                    patch.object(host, "stop_owned_preview") as stop, \
                    patch.object(kit_settings.subprocess, "run") as run:
                kit_settings._stop_studio()
                stop.assert_called_once_with(proc, "recorded")
                run.assert_not_called()
        finally:
            kit_settings._hf_preview.clear()
            kit_settings._hf_preview.update(previous)

    def test_both_mac_preview_entry_points_record_owned_foreground_identity(self):
        previous = dict(kit_settings._hf_preview)
        try:
            with tempfile.TemporaryDirectory() as folder, patch.object(host, "is_macos", return_value=True), \
                    patch.object(host, "process_identity", return_value="owned-snapshot") as identity, \
                    patch.object(host, "hyperframes_command", return_value=["node", "preview", "--foreground"]) as safe_command, \
                    patch("shutil.which", return_value="npx"), \
                    patch.object(kit_settings, "_stop_studio"), \
                    patch.object(kit_settings.subprocess, "Popen", return_value=Mock(pid=4321)) as popen, \
                    patch("urllib.request.urlopen"), \
                    patch.object(kit_settings, "_read_config", return_value={"output_dir": folder}):
                base = Path(folder)
                source = base / "talk.mp4"
                source.touch()
                project = base / "hyperframes" / "talk"
                project.mkdir(parents=True)
                (project / "index.html").touch()
                kit_settings.hyperframes_project(kit_settings.HyperframesRequest(file=str(source), name="talk"))
                self.assertIn("--foreground", popen.call_args.args[0])
                self.assertEqual(kit_settings._hf_preview["identity"], "owned-snapshot")
                kit_settings._hf_preview.pop("identity")
                kit_settings._open_hf_studio(project)
                self.assertIn("--foreground", popen.call_args.args[0])
                self.assertEqual(kit_settings._hf_preview["identity"], "owned-snapshot")
                self.assertEqual(identity.call_count, 2)
                self.assertEqual(safe_command.call_count, 2)
        finally:
            kit_settings._hf_preview.clear()
            kit_settings._hf_preview.update(previous)

    def test_mac_private_cache_guards_preview_and_render_without_touching_other_code(self):
        source = b'function killOrphanedProcesses() {\n  return sweep();\n}\nexport { killOrphanedProcesses };\n'
        with tempfile.TemporaryDirectory(prefix="preview ' ") as folder:
            root = Path(folder).resolve()
            original = root / "state/hyperframes/node_modules/hyperframes/dist/chunk-LAOCA3MQ.js"
            original.parent.mkdir(parents=True)
            original.write_bytes(source)
            cli = original.parent.parent / "bin/hyperframes.mjs"
            cli.parent.mkdir()
            cli.touch()
            env = {}
            with patch.object(host, "__file__", str(root / "app/host_platform.py")), \
                    patch.object(host, "is_macos", return_value=True), \
                    patch.object(host, "HF_CLEANUP_SHA", hashlib.sha256(source).hexdigest()), \
                    patch("shutil.which", return_value="node"), \
                    patch.object(host.subprocess, "run") as install, \
                    patch.object(host.atomic, "write_text", side_effect=lambda p, t, **kw: p.write_bytes(t.encode("utf-8"))):
                # Model macOS LF writes while this fixture also runs on Windows.
                command = host.hyperframes_command(
                    ["npx", "--yes", "hyperframes@0.8.101", "preview", "--port", "3002", "--no-open"], root, env)
                render = host.hyperframes_command(
                    ["npx", "--yes", "hyperframes@0.8.101", "render", "--format", "mp4"], root, env)
                check = host.hyperframes_command(
                    ["npx", "--yes", "hyperframes@0.8.101", "check", "--json"], root, env)
                install.assert_not_called()
            self.assertEqual(original.read_bytes(), source.replace(b'function killOrphanedProcesses() {',
                             b'function killOrphanedProcesses() {\n  return 0;'))
            self.assertEqual(env["npm_config_cache"], str(root / "state/hyperframes-npm"))
            self.assertEqual(env["PWD"], str(root.resolve()))
            self.assertEqual(command, ["node", str(cli), "preview", "--port", "3002", "--no-open", "--foreground"])
            self.assertEqual(render, ["node", str(cli), "render", "--format", "mp4"])
            self.assertEqual(check, ["node", str(cli), "check", "--json"])

    def test_mac_preview_changed_module_fails_before_writing_or_launching(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            module = root / "state/hyperframes/node_modules/hyperframes/dist/chunk-LAOCA3MQ.js"
            module.parent.mkdir(parents=True)
            module.write_text("unexpected module")
            with patch.object(host, "__file__", str(root / "app/host_platform.py")), \
                    patch.object(host, "is_macos", return_value=True), patch("shutil.which", return_value="node"):
                with self.assertRaisesRegex(RuntimeError, "module changed"):
                    host.hyperframes_command(
                        ["npx", "--yes", "hyperframes@0.8.101", "preview", "--port", "3002"], root, {})
            self.assertEqual(module.read_text(), "unexpected module")

    def test_mac_preview_future_version_is_rejected_before_install(self):
        with patch.object(host, "is_macos", return_value=True), \
                patch.object(host.subprocess, "run") as install:
            with self.assertRaisesRegex(RuntimeError, "needs review"):
                host.hyperframes_command(["npx", "--yes", "hyperframes@0.9.0", "preview"], ROOT, {})
            install.assert_not_called()

    def test_mac_hyperframes_installs_privately_even_with_a_global_cli(self):
        source = b'function killOrphanedProcesses() {\n  return sweep();\n}\n'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            runtime = root / "state/hyperframes"
            def install(command, **options):
                self.assertEqual(command[:4], ["npm", "install", "--prefix", str(runtime)])
                self.assertEqual(command[-1], "hyperframes@0.8.101")
                module = runtime / "node_modules/hyperframes/dist/chunk-LAOCA3MQ.js"
                module.parent.mkdir(parents=True)
                module.write_bytes(source)
                cli = module.parent.parent / "bin/hyperframes.mjs"
                cli.parent.mkdir()
                cli.touch()
                return SimpleNamespace(returncode=0)
            with patch.object(host, "__file__", str(root / "app/host_platform.py")), \
                    patch.object(host, "is_macos", return_value=True), \
                    patch.object(host, "HF_CLEANUP_SHA", hashlib.sha256(source).hexdigest()), \
                    patch("shutil.which", side_effect=lambda tool, **kw: tool), \
                    patch.object(host.subprocess, "run", side_effect=install), \
                    patch.object(host.atomic, "write_text", side_effect=lambda p, t, **kw: p.write_bytes(t.encode("utf-8"))):
                command = host.hyperframes_command(["/global/npx", "--yes", "hyperframes@0.8.101", "render"], root, {})
                self.assertEqual(Path(command[1]), runtime / "node_modules/hyperframes/bin/hyperframes.mjs")

    def test_windows_hyperframes_command_is_unchanged(self):
        command = ["npx", "--yes", "hyperframes@0.8.101", "render"]
        env = {}
        with patch.object(host, "is_macos", return_value=False), patch.object(host.subprocess, "run") as run:
            self.assertIs(host.hyperframes_command(command, ROOT, env), command)
            self.assertEqual(env, {})
            run.assert_not_called()

    def test_hyperframes_cli_forwards_flags_and_exit_code_through_host_guard(self):
        import hf
        with patch.object(sys, "argv", ["hf.py", "init"]):
            with self.assertRaises(SystemExit):
                hf.main()
        with patch.object(sys, "argv", ["hf.py", "render", "--format", "mp4"]), \
                patch.dict(sys.modules, kitconfig=SimpleNamespace()), \
                patch.object(hf.shutil, "which", return_value="npx"), \
                patch.object(host, "hyperframes_command", return_value=["guarded"]) as guard, \
                patch.object(hf.subprocess, "call", return_value=7) as call:
            self.assertEqual(hf.main(), 7)
            self.assertEqual(guard.call_args.args[0], ["npx", "--yes", "hyperframes@0.8.101", "render", "--format", "mp4"])
            self.assertEqual(call.call_args.args[0], ["guarded"])

    def test_generated_collection_instructions_use_the_host_guard(self):
        import motion_collect
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            motion_collect.write_index(out, [])
            text = (out / "index.md").read_text(encoding="utf-8")
            self.assertIn("scripts", text)
            self.assertIn("hf.py", text)
            self.assertNotIn("npx", text)

    def test_mac_setup_uses_bash_with_workspace_as_one_argument(self):
        with patch.object(host, "is_macos", return_value=True), \
                patch.object(kit_settings, "_read_config", return_value={"workspace": "space ' $"}), \
                patch.object(kit_settings, "_start", return_value={}) as start:
            kit_settings.run_setup()
            self.assertEqual(start.call_args.args[1],
                             ["/bin/bash", str(ROOT / "scripts" / "setup.sh"), "space ' $"])

    def test_mac_runtime_environment_preserves_path_and_rejects_bad_config(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"PATH": "original-path"}):
            result = host.runtime_environment({"runtime_bin_dirs": [folder]})
            self.assertEqual(result["PATH"], str(Path(folder).resolve()) + os.pathsep + "original-path")
            self.assertEqual(os.environ["PATH"], "original-path")
            for invalid in ("not-list", ["relative"], [123], [str(Path(folder) / "missing")]):
                with self.assertRaises(ValueError):
                    host.runtime_environment({"runtime_bin_dirs": invalid})

    def test_kitconfig_import_restores_cli_runtime_path_without_running_tools(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            binary = base / "bin"
            binary.mkdir()
            tool = binary / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            tool.touch()
            tool.chmod(0o755)
            config = base / "config.json"
            config.write_text(json.dumps({"runtime_bin_dirs": [str(binary)], "capcut_drafts": folder}),
                              encoding="utf-8")
            code = ("import sys,shutil;sys.path.insert(0,sys.argv[1]);"
                    "import kitconfig;print(shutil.which('ffmpeg'))")
            result = subprocess.run([sys.executable, "-c", code, str(ROOT / "scripts" / "capcut")],
                                    env={**os.environ, "CLIP_KIT_CONFIG": str(config), "PATH": ""},
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()).resolve(), tool.resolve())

    def test_non_windows_cli_never_queries_scheduled_tasks(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            config.write_text(json.dumps({"capcut_drafts": folder, "output_dir": folder}))
            with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
                cli = importlib.import_module("run_clip")
            with patch.object(cli.sys, "platform", "darwin"), patch("subprocess.run") as run:
                cli._app_icon()
                run.assert_not_called()


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.owner = host.ProcessIdentity(4201, 123.0, 100, str(Path(sys.executable).resolve()),
                                          (sys.executable, str(control.APP)))

    def test_spawned_framework_python_freezes_full_identity_only_after_its_listener(self):
        launcher = replace(self.owner, executable=sys.executable + ".bootstrap",
                           command=(sys.executable + ".bootstrap", str(control.APP)))
        with patch.object(control, "process_identity", side_effect=[launcher, self.owner, self.owner]), \
                patch.object(control, "port_owner", side_effect=[None, self.owner]), \
                patch.object(control.time, "sleep"), patch.object(control, "_request") as request:
            stable = control._capture_started_owner(8770, launcher, 5)
            self.assertEqual(stable, self.owner)
            request.assert_not_called()
        # Once frozen, even the same PID/create time may not silently re-exec again.
        with patch.object(control, "process_identity", return_value=launcher):
            with self.assertRaisesRegex(RuntimeError, r"identity changed \(executable, command\)"):
                control.assert_same(stable)

    def test_bootstrap_does_not_accept_reused_pid_changed_birth_or_changed_parent(self):
        for field, value in (("pid", 4202), ("created", 124.0), ("parent", 200)):
            replacement = replace(self.owner, **{field: value})
            with patch.object(control, "process_identity", return_value=replacement), \
                    patch.object(control, "port_owner") as port:
                with self.assertRaisesRegex(RuntimeError, field):
                    control._capture_started_owner(8770, self.owner, 5)
                port.assert_not_called()

    def test_bootstrap_never_adopts_another_process_that_takes_the_listener(self):
        with patch.object(control, "process_identity", return_value=self.owner), \
                patch.object(control, "port_owner", return_value=replace(self.owner, pid=4202)), \
                patch.object(control, "_request") as request:
            with self.assertRaisesRegex(RuntimeError, "pid"):
                control._capture_started_owner(8770, self.owner, 5)
            request.assert_not_called()

    def test_framework_controller_uses_current_venv_wrapper_and_same_runtime_identity(self):
        runtime = replace(self.owner, executable=str(ROOT / "Python.app" / "Contents" / "MacOS" / "Python"))
        controller = replace(runtime, pid=4202, parent=4201, command=(
            sys.executable, str(ROOT / "app" / "control.py"), "--action", "restart", "--port", "8770",
            "--python", sys.executable, "--expected-pid", "4201", "--expected-created", "123.0",
            "--issued-at", str(int(time.time()))))
        self.assertTrue(host._is_controller(controller, runtime))
        self.assertFalse(host._is_controller(replace(controller, executable=sys.executable + ".foreign"), runtime))
        altered = list(controller.command)
        altered[7] = str(ROOT / "other-python")
        self.assertFalse(host._is_controller(replace(controller, command=tuple(altered)), runtime))

    def test_foreign_or_relative_command_never_receives_exit(self):
        for command in ((sys.executable, "app.py"), (sys.executable, str(ROOT / "other" / "app.py"))):
            with patch.object(control, "port_owner", return_value=replace(self.owner, command=command)), \
                    patch.object(control, "_request") as request, patch.object(control, "start_server") as start:
                with self.assertRaisesRegex(RuntimeError, "not owned"):
                    control.invoke("restart", 8770, sys.executable)
                request.assert_not_called()
                start.assert_not_called()

    def test_reused_expected_pid_is_rejected_before_health_or_exit(self):
        with patch.object(control, "port_owner", return_value=replace(self.owner, created=124)), \
                patch.object(control, "health") as health, patch.object(control, "_request") as request:
            with self.assertRaisesRegex(RuntimeError, "instance changed"):
                control.invoke("shutdown", 8770, sys.executable, 4201, expected_created=123)
            health.assert_not_called()
            request.assert_not_called()

    def test_busy_job_is_preserved_on_restart_and_shutdown(self):
        for action in ("restart", "shutdown"):
            with patch.object(control, "port_owner", return_value=self.owner), \
                    patch.object(control, "health", return_value={"busy": True}), \
                    patch.object(control, "_request") as request, patch.object(control, "_children") as children:
                with self.assertRaisesRegex(RuntimeError, "active job"):
                    control.invoke(action, 8770, sys.executable)
                request.assert_not_called()
                children.assert_not_called()

    def test_existing_start_reuses_even_a_busy_owned_server(self):
        with patch.object(control, "port_owner", return_value=self.owner), \
                patch.object(control, "health", return_value={"busy": True}), \
                patch.object(control, "_request") as request, \
                patch.object(control, "wait_ready", return_value=4201), \
                patch.object(control, "start_server") as start:
            self.assertEqual(control.invoke("start", 8770, sys.executable)["instance"], 4201)
            request.assert_called_once_with(8770, "/api/window/cancel-shutdown?control=v2", "POST")
            start.assert_not_called()

    def test_restart_orders_graceful_exit_cleanup_and_readiness(self):
        events = []
        preview = replace(self.owner, pid=4202, command=("node", "hyperframes@0.8.101", "preview"))
        with patch.object(control, "port_owner", side_effect=[self.owner, self.owner, None]), \
                patch.object(control, "health", return_value={"busy": False}), \
                patch.object(control, "_children", return_value=[preview]), \
                patch.object(host, "server_has_active_helpers", return_value=False), \
                patch.object(control, "assert_same"), \
                patch.object(control, "_request", side_effect=lambda *args: events.append("exit")), \
                patch.object(control, "_wait_stopped", side_effect=lambda owner: events.append("wait")), \
                patch.object(control, "_stop_children", side_effect=lambda kids: events.append("children")), \
                patch.object(control, "port_free", return_value=True), \
                patch.object(control, "start_server", side_effect=lambda *args: events.append("start") or 6001):
            result = control.invoke("restart", 8770, sys.executable)
            self.assertEqual(result["instance"], 6001)
            self.assertEqual(events, ["exit", "wait", "children", "start"])

    def test_synchronous_export_child_blocks_exit_even_when_queue_reports_idle(self):
        export = replace(self.owner, pid=4202, command=("ffmpeg", "-i", "clip.mp4", "out.mp4"))
        with patch.object(control, "port_owner", return_value=self.owner), \
                patch.object(control, "health", return_value={"busy": False}), \
                patch.object(control, "_children", return_value=[export]), \
                patch.object(host, "server_has_active_helpers", return_value=True), \
                patch.object(control, "_request") as request, patch.object(control, "start_server") as start:
            with self.assertRaisesRegex(RuntimeError, "active helper"):
                control.invoke("restart", 8770, sys.executable)
            request.assert_not_called()
            start.assert_not_called()

    def test_changed_port_owner_is_never_stopped(self):
        with patch.object(control, "port_owner", side_effect=[self.owner, replace(self.owner, pid=4202)]), \
                patch.object(control, "health", return_value={"busy": False}), \
                patch.object(control, "_children", return_value=[]), \
                patch.object(host, "server_has_active_helpers", return_value=False), \
                patch.object(control, "_request") as request:
            with self.assertRaisesRegex(RuntimeError, "port owner changed"):
                control.invoke("shutdown", 8770, sys.executable)
            request.assert_not_called()

    def test_health_creation_time_must_match_the_process_snapshot(self):
        with patch.object(control, "assert_same"), patch.object(control, "_request", return_value={
                "instance": 4201, "created_at": 124, "ready": True, "busy": False}):
            with self.assertRaisesRegex(RuntimeError, "identity changed"):
                control.health(8770, self.owner)

    def test_stale_child_pid_is_skipped_without_signal(self):
        with patch.object(control, "process_identity", return_value=replace(self.owner, created=124)), \
                patch("psutil.Process") as process:
            control._stop_children([self.owner])
            process.assert_not_called()

    def test_lsof_failure_or_multiple_owners_does_not_guess_a_pid(self):
        for response in (SimpleNamespace(returncode=2, stdout="", stderr="failed"),
                         SimpleNamespace(returncode=0, stdout="4201\n4202\n", stderr=""),
                         SimpleNamespace(returncode=0, stdout="invalid", stderr="")):
            with patch.object(control.subprocess, "run", return_value=response), \
                    patch.object(control, "process_identity") as identity:
                with self.assertRaises(RuntimeError):
                    control.port_owner(8770)
                identity.assert_not_called()

    def test_expired_and_invalid_controls_never_look_up_or_launch_processes(self):
        with patch.object(control, "port_owner") as owner, patch.object(control, "start_server") as start:
            for action, port, pid, stamp, created in (
                    ("delete", 8770, 0, 0, None), ("start", 0, 0, 0, None),
                    ("start", True, 0, 0, None), ("shutdown", 8770, 4201, 0, None),
                    ("restart", 8770, 0, 1, None)):
                with self.assertRaises((ValueError, RuntimeError)):
                    control.invoke(action, port, sys.executable, pid, stamp, created)
            owner.assert_not_called()
            start.assert_not_called()

    def test_mac_window_controller_passes_creation_identity_in_argv(self):
        with patch.object(host, "is_macos", return_value=True), \
                patch.object(host, "process_identity", return_value=self.owner), \
                patch.object(window_lifecycle.subprocess, "Popen") as popen:
            window_lifecycle.launch_control("restart", 8770)
            argv = popen.call_args.args[0]
            self.assertEqual(argv[:2], [sys.executable, str(ROOT / "app" / "control.py")])
            self.assertIn("--expected-created", argv)
            self.assertEqual(argv[argv.index("--expected-created") + 1], "123.0")
            self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_mac_exit_rechecks_creation_time_and_busy_job_before_graceful_exit(self):
        app = FastAPI()
        busy = Mock(return_value=False)
        exit_server = Mock()
        app.include_router(window_lifecycle.window_router(8770, exit_server, busy, lambda: True))
        with patch.object(host, "is_macos", return_value=True), \
                patch.object(host, "process_identity", return_value=self.owner), \
                patch.object(host, "server_has_active_helpers", return_value=False) as helpers, \
                TestClient(app) as client:
            self.assertEqual(client.get("/api/window/health").json()["created_at"], 123)
            self.assertEqual(client.post(f"/api/window/exit?instance={os.getpid()}").status_code, 409)
            self.assertEqual(client.post(
                f"/api/window/exit?instance={os.getpid()}&created_at=124").status_code, 409)
            busy.return_value = True
            self.assertEqual(client.post(
                f"/api/window/exit?instance={os.getpid()}&created_at=123").status_code, 409)
            self.assertEqual(client.post("/api/window/restart?control=v2").status_code, 409)
            exit_server.assert_not_called()
            busy.return_value = False
            helpers.return_value = True  # a synchronous export started after the controller snapshot
            self.assertEqual(client.post(
                f"/api/window/exit?instance={os.getpid()}&created_at=123").status_code, 409)
            exit_server.assert_not_called()
            helpers.return_value = False
            self.assertEqual(client.post(
                f"/api/window/exit?instance={os.getpid()}&created_at=123").status_code, 202)
            exit_server.assert_called_once()


if __name__ == "__main__":
    unittest.main()
