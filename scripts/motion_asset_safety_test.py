"""Motion revisions get distinct files, and AI transactions own their rendered assets."""
import os
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
with tempfile.TemporaryDirectory() as import_folder:
    config = Path(import_folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import kit_settings
        import video_editor
import render_process
import motion_plan


class MotionAssetSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shared = self.root / "configured-output"
        self.shared.mkdir()
        for name in ("first", "second"):
            folder = self.root / "motion" / name
            folder.mkdir(parents=True)
            (folder / "index.html").write_text(
                f'<html><head></head><body data-duration="1">{name}</body></html>', encoding="utf-8")
        self.enterContext(patch.object(kit_settings, "KIT", self.root))
        self.enterContext(patch.object(kit_settings, "_read_config", return_value={"output_dir": str(self.shared)}))
        spec = {"length": "duration", "fields": [{"key": "duration", "type": "number", "default": 2},
                                                 {"key": "revision", "type": "number", "default": 1}]}
        self.enterContext(patch.object(kit_settings, "motion_templates", return_value={"first": spec, "second": spec}))
        def prepare(template, params, **kwargs):
            page = self.root / "motion" / template / "index.html"
            return {"source_params": {**params, "subtitle_events": []}, "duration": params["duration"],
                    "canvas": {"width": 1080, "height": 1920}, "layout": None, "placement": None, "bounds": [0,0,1,1],
                    "inputs": {"template": str(page)}, "hashes": {"template": hashlib.sha256(page.read_bytes()).hexdigest()},
                    "layout_sha256": "synthetic-asset-fixture"}
        self.enterContext(patch.object(motion_plan, "prepare_template", side_effect=prepare))
        self.enterContext(patch.object(motion_plan, "verify_media", return_value={"sha256": "synthetic-media-fixture"}))
        self.enterContext(patch("shutil.which", return_value="npx"))
        self.enterContext(patch.object(kit_settings.host_platform, "hyperframes_command", side_effect=lambda cmd, cwd, env: cmd))
        self.launch = self.enterContext(patch.object(render_process, "run", side_effect=self.render))
        self.calls = []

    def render(self, command, **kwargs):
        if "check" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps({"ok": True}), "")
        target = Path(command[command.index("-o") + 1])
        work = Path(kwargs["cwd"])
        self.calls.append((target, work))
        target.write_bytes((work / "index.html").read_bytes())
        return subprocess.CompletedProcess(command, 0, "", "")

    def request(self, name="same", template="first", revision=1):
        return kit_settings.MotionRequest(template=template, name=name, params={"duration": 2, "revision": revision})

    def test_long_names_templates_and_revisions_never_share_an_output(self):
        prefix = "a" * 40
        requests = (self.request(prefix + "one"), self.request(prefix + "two"),
                    self.request(prefix + "one", template="second"), self.request(prefix + "one", revision=2))
        files = [Path(kit_settings.make_motion(request)["file"]) for request in requests]
        self.assertEqual(len(set(files)), len(requests))
        for file in files:
            self.assertEqual(file.parent, self.shared / "motion")
            self.assertRegex(file.name, r"^a{40}-[0-9a-f]{32}\.mov$")
        self.assertIn(b"first</body>", files[0].read_bytes())
        self.assertIn(b"second</body>", files[2].read_bytes())
        self.assertIn(b'"revision": 1', files[0].read_bytes())
        self.assertIn(b'"revision": 2', files[3].read_bytes())
        for call in self.launch.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 300 if "check" in call.args[0] else 900)
            self.assertEqual(call.kwargs["env"]["HYPERFRAMES_SKIP_SKILLS"], "1")
        self.assertTrue(all(not work.exists() for _, work in self.calls))

    def test_private_transaction_assets_never_touch_configured_output(self):
        private = self.root / "transaction" / "assets"
        with kit_settings.motion_destination(private):
            file = Path(kit_settings.make_motion(self.request())["file"])
        self.assertEqual(file.parent, private)
        self.assertTrue(file.is_file())
        self.assertEqual(list(self.shared.iterdir()), [])
        self.assertIsNone(kit_settings._motion_destination.get())

    def test_failed_renderer_never_overwrites_an_existing_revision(self):
        old = Path(kit_settings.make_motion(self.request())["file"])
        before = old.read_bytes()
        legacy = old.parent / "same.mov"
        legacy.write_bytes(b"existing legacy motion")

        def fail(command, **kwargs):
            if "check" in command:
                return self.render(command, **kwargs)
            self.render(command, **kwargs)
            return subprocess.CompletedProcess(command, 1, "", "fixture render failed")

        self.launch.side_effect = fail
        with self.assertRaises(kit_settings.HTTPException) as error:
            kit_settings.make_motion(self.request(revision=2))
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(legacy.read_bytes(), b"existing legacy motion")
        self.assertNotEqual(self.calls[-1][0], old)
        self.assertFalse(self.calls[-1][1].exists())
        self.assertFalse(self.calls[-1][0].exists())

    def test_renderer_exception_resets_private_destination_and_cleans_work(self):
        private = self.root / "transaction" / "assets"

        def timeout(command, **kwargs):
            if "check" in command:
                return self.render(command, **kwargs)
            self.render(command, **kwargs)
            raise subprocess.TimeoutExpired(command, 900)

        self.launch.side_effect = timeout
        with self.assertRaises(subprocess.TimeoutExpired):
            with kit_settings.motion_destination(private):
                kit_settings.make_motion(self.request())
        self.assertEqual(self.calls[-1][0].parent, private)
        self.assertFalse(self.calls[-1][1].exists())
        self.assertFalse(self.calls[-1][0].exists())
        self.assertEqual(list(self.shared.iterdir()), [])
        self.assertIsNone(kit_settings._motion_destination.get())
        self.launch.side_effect = self.render
        next_file = Path(kit_settings.make_motion(self.request())["file"])
        self.assertEqual(next_file.parent, self.shared / "motion")


if __name__ == "__main__":
    unittest.main()
