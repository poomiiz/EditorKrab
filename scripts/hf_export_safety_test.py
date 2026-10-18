"""Failed or concurrent HyperFrames jobs must preserve the last completed export."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
with tempfile.TemporaryDirectory() as imports:
    cfg = Path(imports) / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(cfg)}):
        import hf_build
        import delivery_manifest
        import jobstore
        import kit_settings
        import render
        import render_process
        import video_edit
        import video_editor as editor


class ExportSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "งานทดสอบ"
        self.project.mkdir()
        self.page = self.project / "clipkit_hf" / "index.html"
        self.page.parent.mkdir()
        self.page.write_text("fixture", encoding="utf-8")
        (self.project / "draft_content.json").write_text(json.dumps({"duration": 1_000_000,
            "canvas_config": {"width": 1080, "height": 1920}}), encoding="utf-8")
        self.target = self.root / "exports" / (self.project.name + ".mp4")
        self.target.parent.mkdir()
        self.target.write_bytes(b"previous approved video")
        self.request = editor.ExportRequest(path=str(self.project))
        self.failure = ""
        self.stages = []
        self.real_run = subprocess.run
        self.real_probe = video_edit.probe
        self.real_normalize = render.normalize_loudness
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(delivery_manifest, "commit", return_value="a" * 40))
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(jobstore, "STATE", self.root / "state"))
        self.stack.enter_context(patch.object(kit_settings, "_read_config", return_value={"output_dir": str(self.root)}))
        self.stack.enter_context(patch("shutil.which", return_value="npx"))
        self.build = self.stack.enter_context(patch.object(hf_build, "_build", return_value=self.page))
        self.stack.enter_context(patch.object(editor.host_platform, "is_macos", return_value=False))
        self.process = self.stack.enter_context(patch.object(subprocess, "run", side_effect=self.run_process))
        self.launch = self.stack.enter_context(patch.object(render_process, "run", side_effect=self.run_process))
        self.normalize = self.stack.enter_context(patch.object(render, "normalize_loudness", side_effect=self.normalize_file))
        self.probe = self.stack.enter_context(patch.object(video_edit, "probe", side_effect=self.probe_file))

    def run_process(self, args, **kwargs):
        if "-o" in args:
            staged = Path(args[args.index("-o") + 1])
            self.stages.append(staged.parent)
            self.assertNotEqual(staged, self.target)
            self.assertEqual(Path(kwargs["env"]["TEMP"]).parent, staged.parent)
            staged.write_bytes(b"new video")
            if self.failure == "timeout":
                raise subprocess.TimeoutExpired(args, 3600, output="render output", stderr="timeout diagnostic")
            return SimpleNamespace(returncode=int(self.failure == "render"), stdout="render output", stderr="render diagnostic")
        self.assertIn("-xerror", args)
        self.assertNotEqual(Path(args[args.index("-i") + 1]), self.target)
        return SimpleNamespace(returncode=int(self.failure == "decode"), stdout="", stderr="decode diagnostic")

    def normalize_file(self, path):
        self.assertNotEqual(path, self.target)
        if self.failure == "normalize":
            raise video_edit.VideoEditError("normalization failed")

    def probe_file(self, path):
        if self.failure == "probe":
            raise video_edit.VideoEditError("probe failed")
        return {"duration": .5 if self.failure == "truncated" else 1,
                "width": 1920 if self.failure == "ratio" else 1080, "height": 1920,
                "video_codec": None if self.failure == "video" else "h264", "audio_codec": None if self.failure == "audio" else "aac"}

    def test_each_failed_stage_preserves_old_mp4_and_diagnostics(self):
        for failure in ("render", "timeout", "normalize", "probe", "decode", "audio", "video", "truncated", "ratio"):
            with self.subTest(failure=failure):
                self.failure = failure
                with self.assertRaises(editor.HTTPException) as error:
                    editor.draft_hf_export(self.request)
                self.assertEqual(error.exception.status_code, 500)
                self.assertEqual(self.target.read_bytes(), b"previous approved video")
                self.assertTrue((self.stages[-1] / "error.log").is_file())
                self.assertTrue((self.stages[-1] / "output.mp4").is_file())
        self.assertEqual(len(set(self.stages)), 9)
        self.assertTrue(any("timeout diagnostic" in (stage / "error.log").read_text(encoding="utf-8")
                            for stage in self.stages))

    def test_success_publishes_only_after_validation_and_cleans_own_stage(self):
        other = self.target.parent / ".render-another-job"
        other.mkdir()
        marker = other / "keep.txt"
        marker.write_text("another job", encoding="utf-8")
        result = editor.draft_hf_export(self.request)
        self.assertEqual(result["file"], str(self.target))
        self.assertEqual(self.target.read_bytes(), b"new video")
        self.assertEqual(self.process.call_count, 1)
        self.launch.assert_called_once()
        self.normalize.assert_called_once()
        self.probe.assert_called_once()
        self.assertFalse(self.stages[-1].exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "another job")

    def test_preview_and_final_exports_use_the_matching_caption_gate(self):
        for preview in (False, True):
            with self.subTest(preview=preview):
                self.build.reset_mock()
                self.request.preview = preview
                editor.draft_hf_export(self.request)
                self.build.assert_called_once()
                args, kwargs = self.build.call_args
                self.assertEqual(args, (str(self.project),))
                self.assertEqual(kwargs["final"], not preview)
                self.assertTrue(callable(kwargs["audio_check"]))

    def test_resolved_audio_changed_during_render_preserves_previous_output(self):
        audio = self.root / "chosen-audio.wav"
        audio.write_bytes(b"original chosen audio")
        def built(path, *, final, audio_check):
            audio_check({str(audio.resolve()): audio})
            return self.page
        normalize = self.normalize_file
        def changed(path):
            normalize(path)
            audio.write_bytes(b"replaced chosen audio")
        with patch.object(hf_build, "_build", side_effect=built), \
                patch.object(render, "normalize_loudness", side_effect=changed):
            with self.assertRaises(editor.HTTPException) as error:
                editor.draft_hf_export(self.request)
        self.assertEqual(error.exception.status_code, 500)
        self.assertIn("เปลี่ยนระหว่างส่งออก", error.exception.detail)
        self.assertEqual(self.target.read_bytes(), b"previous approved video")
        self.assertTrue((self.stages[-1] / "error.log").is_file())

    def test_manifest_failure_preserves_previous_output_and_diagnostics(self):
        with patch.object(delivery_manifest, "prepare", side_effect=OSError("manifest disk full")):
            with self.assertRaises(editor.HTTPException) as error:
                editor.draft_hf_export(self.request)
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(self.target.read_bytes(), b"previous approved video")
        self.assertTrue((self.stages[-1] / "error.log").is_file())

    def test_publish_failure_preserves_final_and_retains_staging(self):
        original = Path.replace
        def replace(source, destination):
            if Path(destination) == self.target:
                raise PermissionError("file in use")
            return original(source, destination)
        with patch.object(Path, "replace", replace), self.assertRaises(editor.HTTPException):
            editor.draft_hf_export(self.request)
        self.assertEqual(self.target.read_bytes(), b"previous approved video")
        self.assertTrue((self.stages[-1] / "error.log").is_file())

    def test_metadata_change_during_encode_preserves_previous_mp4(self):
        normalize = self.normalize_file
        def changed(path):
            normalize(path)
            (self.project/'clipkit_style.json').write_text('{"anim":"pair"}')
        with patch.object(render, 'normalize_loudness', side_effect=changed), self.assertRaises(editor.HTTPException) as error:
            editor.draft_hf_export(self.request)
        self.assertIn('เปลี่ยนระหว่างส่งออก', str(error.exception.detail))
        self.assertEqual(self.target.read_bytes(), b'previous approved video')
        self.assertEqual((self.project/'clipkit_style.json').read_text(), '{"anim":"pair"}')
        self.assertTrue((self.stages[-1]/'error.log').is_file())

    def test_external_target_change_during_encode_is_not_overwritten(self):
        def changed(path):
            self.target.write_bytes(b'external newly approved video')
        with patch.object(render, 'normalize_loudness', side_effect=changed), self.assertRaises(editor.HTTPException):
            editor.draft_hf_export(self.request)
        self.assertEqual(self.target.read_bytes(), b'external newly approved video')

    def test_cleanup_error_is_a_warning_after_successful_publish(self):
        with patch("shutil.rmtree", side_effect=PermissionError("temporary file still open")):
            result = editor.draft_hf_export(self.request)
        self.assertIn("warning", result)
        self.assertEqual(self.target.read_bytes(), b"new video")

    def test_mac_launcher_failure_is_logged_without_overwriting_final(self):
        with patch.object(editor.host_platform, "is_macos", return_value=True), \
                patch.object(editor.host_platform, "hyperframes_command", side_effect=RuntimeError("launcher failed")), \
                self.assertRaises(editor.HTTPException):
            editor.draft_hf_export(self.request)
        self.assertEqual(self.target.read_bytes(), b"previous approved video")
        logs = list(self.target.parent.glob(".render-*/error.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("launcher failed", logs[0].read_text(encoding="utf-8"))

    def test_full_disk_while_logging_does_not_hide_render_failure(self):
        self.failure = "render"
        with patch.object(editor.atomic, "write_text", side_effect=OSError("disk full")), \
                self.assertRaises(editor.HTTPException) as error:
            editor.draft_hf_export(self.request)
        self.assertIn("disk full", str(error.exception.detail))
        self.assertEqual(self.target.read_bytes(), b"previous approved video")

    def test_missing_npx_fails_before_build_or_export(self):
        with patch("shutil.which", return_value=None), self.assertRaises(editor.HTTPException) as error:
            editor.draft_hf_export(self.request)
        self.assertEqual(error.exception.status_code, 400)
        self.build.assert_not_called()
        self.process.assert_not_called()

    def test_build_and_export_reservations_protect_same_project_or_target(self):
        for locked in (str(self.project), str(self.target)):
            with self.subTest(locked=locked), hf_build.build_lock(locked):
                with self.assertRaises(editor.HTTPException) as error:
                    editor.draft_hf_export(self.request)
                self.assertEqual(error.exception.status_code, 409)
                self.build.assert_not_called()
                self.assertEqual(self.target.read_bytes(), b"previous approved video")
        with hf_build.build_lock(str(self.project)):
            with self.assertRaises(editor.HTTPException) as error:
                editor.draft_hf_build(editor.DraftPath(path=str(self.project)))
            self.assertEqual(error.exception.status_code, 409)
        editor.draft_hf_export(self.request)  # reservation released after refusal

    def test_file_reservation_also_blocks_another_process(self):
        cfg = self.root / "config.json"
        cfg.write_text("{}", encoding="utf-8")
        code = ("import sys; from pathlib import Path; "
                f"sys.path.insert(0,{str(ROOT / 'app')!r}); import hf_build,jobstore; "
                f"jobstore.STATE=Path({str(self.root / 'state')!r}); "
                f"\ntry:\n with hf_build.build_lock({str(self.project)!r}): pass"
                "\nexcept hf_build.BuildBusy: sys.exit(23)")
        with hf_build.build_lock(str(self.project)):
            child = self.real_run([sys.executable, "-c", code], capture_output=True, text=True,
                                  env={**os.environ, "CLIP_KIT_CONFIG": str(cfg)}, timeout=30)
        self.assertEqual(child.returncode, 23, child.stderr)

    def test_preview_build_uses_same_public_guard(self):
        with hf_build.build_lock(str(self.project)), self.assertRaises(hf_build.BuildBusy):
            hf_build.build(str(self.project))
        self.build.assert_not_called()

    def test_real_audio_normalization_probe_and_full_decode_before_publish(self):
        import shutil
        fixture = self.root / "generated.mp4"
        self.real_run([render.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x96:r=30:d=1",
                       "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-pix_fmt",
                       "yuv420p", "-c:a", "aac", "-shortest", str(fixture)], check=True, capture_output=True)
        (self.project / "draft_content.json").write_text(json.dumps({"duration": 1_000_000,
            "canvas_config": {"width": 64, "height": 96}}), encoding="utf-8")
        def process(args, **kwargs):
            if "-o" in args:
                shutil.copyfile(fixture, args[args.index("-o") + 1])
                return SimpleNamespace(returncode=0, stdout="fixture render", stderr="")
            return self.real_run(args, **kwargs)
        with patch.object(subprocess, "run", side_effect=process), \
                patch.object(render_process, "run", side_effect=process), \
                patch.object(render, "normalize_loudness", side_effect=self.real_normalize), \
                patch.object(video_edit, "probe", side_effect=self.real_probe):
            result = editor.draft_hf_export(self.request)
            actual = self.real_probe(result["file"])
        self.assertEqual((actual["width"], actual["height"], actual["audio_codec"]), (64, 96, "aac"))
        self.assertAlmostEqual(actual["duration"], 1, delta=.1)
        self.assertFalse(list(self.target.parent.glob(".render-*")))

    def test_proxy_failure_never_poison_cache_or_removes_previous_proxy(self):
        source = self.root / "source.mp4"
        source.write_bytes(b"source")
        out = self.project / "clipkit_hf"
        media = out / "media"
        media.mkdir()
        old = media / "footage_previous.mp4"
        old.write_bytes(b"previous proxy")
        def failed(args, **kwargs):
            Path(args[-1]).write_bytes(b"partial")
            return SimpleNamespace(returncode=1, stderr="failed proxy")
        with patch.object(subprocess, "run", side_effect=failed), self.assertRaises(video_edit.VideoEditError):
            hf_build._proxy(out, str(source), [{"media_start": 0, "dur": 1}])
        self.assertEqual(list(media.iterdir()), [old])
        self.assertEqual(old.read_bytes(), b"previous proxy")

    def test_changed_source_prepares_new_proxy_and_keeps_previous_preview(self):
        source = self.root / "source.mp4"
        source.write_bytes(b"first source")
        def prepared(args, **kwargs):
            Path(args[-1]).write_bytes(b"proxy for " + source.read_bytes())
            return SimpleNamespace(returncode=0, stderr="")
        out = self.project / "clipkit_hf"
        with patch.object(subprocess, "run", side_effect=prepared):
            first = hf_build._proxy(out, str(source), [{"media_start": 0, "dur": 1}])
            stamp = source.stat().st_mtime_ns
            source.write_bytes(b"other source")
            os.utime(source, ns=(stamp + 1_000_000, stamp + 1_000_000))
            second = hf_build._proxy(out, str(source), [{"media_start": 0, "dur": 1}])
        self.assertNotEqual(first, second)
        self.assertEqual((out / first).read_bytes(), b"proxy for first source")
        self.assertEqual((out / second).read_bytes(), b"proxy for other source")


if __name__ == "__main__":
    unittest.main()
