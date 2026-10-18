"""Encoder selection and failed hardware exports never overwrite an existing MP4; no GPU needed."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
with tempfile.TemporaryDirectory() as import_folder:
    config = Path(import_folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import render
import draft_quality
import delivery_manifest
import video_edit


class EncoderTests(unittest.TestCase):
    def test_default_profile_keeps_existing_cpu_quality(self):
        with patch.object(video_edit.kitconfig, "_load", return_value={}):
            self.assertEqual(render._video_encoder(),
                             ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"])
            self.assertIn("ultrafast", render._video_encoder(preview=True))

    def test_hardware_profiles_do_not_use_cpu_options_or_allow_fallback(self):
        for name in ("h264_nvenc", "h264_videotoolbox"):
            with self.subTest(name=name), patch.object(video_edit.kitconfig, "_load", return_value={"render_encoder": name}):
                args = render._video_encoder()
                self.assertEqual(args[:2], ["-c:v", name])
                self.assertNotIn("-crf", args)
                self.assertNotIn("libx264", args)
                if name == "h264_videotoolbox":
                    self.assertEqual(args[args.index("-allow_sw") + 1], "0")

    def test_invalid_profile_raises_before_reading_a_project(self):
        for invalid in ("auto", "", None, ["h264_nvenc"], "h264_nvenc -y"):
            with self.subTest(value=invalid), patch.object(video_edit.kitconfig, "_load", return_value={"render_encoder": invalid}):
                with self.assertRaisesRegex(video_edit.VideoEditError, "render_encoder"):
                    render.render_draft("missing project", "unused output")

    def test_failed_hardware_export_retains_previous_file_and_reports_encoder(self):
        self._export(success=False)

    def test_success_replaces_only_after_encode_and_returns_timing(self):
        self._export(success=True)

    def test_manifest_failure_keeps_previous_mp4(self):
        with patch.object(delivery_manifest, "prepare", side_effect=OSError("manifest disk full")):
            self._export(success=True, change="evidence")

    def test_input_change_during_encode_preserves_previous_export(self):
        for change in ('source', 'style', 'target'):
            with self.subTest(change=change):
                self._export(success=True, change=change)

    def test_invalid_outputs_never_replace_an_accepted_file(self):
        for failure in ("audio", "video", "truncated", "longer", "zero_duration", "nan_duration",
                        "zero_width", "nan_height", "bool_width", "huge_duration", "probe", "decode", "timeout"):
            with self.subTest(failure=failure):
                self._export(success=True, validation=failure)

    def test_validation_time_input_changes_preserve_the_previous_export(self):
        for change in ("decode_source", "decode_style", "decode_target"):
            with self.subTest(change=change):
                self._export(success=True, change=change)

    def test_preview_requires_valid_audio_video_duration_and_aspect_ratio(self):
        self._export(success=True, preview=True)
        self._export(success=True, preview=True, validation="ratio")

    def _export(self, success, change=None, validation=None, preview=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "project"
            folder.mkdir()
            source = root / "source.mp4"
            source.touch()
            clip = {"scale": {"x": 1}, "transform": {"x": 0, "y": 0}}
            segment = {"material_id": "video", "clip": clip,
                       "source_timerange": {"start": 0, "duration": 2_000_000},
                       "target_timerange": {"start": 0, "duration": 2_000_000}}
            draft = {"canvas_config": {"width": 320, "height": 180},
                     "tracks": [{"type": "video", "segments": [segment]}],
                     "materials": {"videos": [{"id": "video", "path": str(source)}]}}
            (folder / "draft_content.json").write_text(json.dumps(draft))
            (folder / "clipkit_style.json").write_text('{"music":"","sfx":false}')
            out = root / "exports"
            out.mkdir()
            target = out / ("project - ตัวอย่าง.mp4" if preview else "project.mp4")
            target.write_bytes(b"previous accepted export")
            encoder = "h264_nvenc"

            def mutate(kind):
                if kind == "source":
                    source.write_bytes(b"new source edited externally")
                elif kind == "style":
                    (folder / 'clipkit_style.json').write_text('{"music":"","sfx":false,"zoomcut":true}')
                elif kind == "target":
                    target.write_bytes(b'new externally approved export')

            def process(command, **kwargs):
                self.assertEqual(target.read_bytes(), b"previous accepted export")
                if "-xerror" in command:
                    self.assertIn("0:v:0", command)
                    self.assertIn("0:a:0", command)
                    self.assertNotEqual(Path(command[command.index("-i") + 1]), target)
                    if change and change.startswith("decode_"):
                        mutate(change.removeprefix("decode_"))
                    if validation == "timeout":
                        raise subprocess.TimeoutExpired(command, 3600)
                    return SimpleNamespace(returncode=int(validation == "decode"), stdout="", stderr="decode diagnostic")
                self.assertEqual(command[command.index("-c:v") + 1], encoder)
                partial = Path(command[-1])
                self.assertNotEqual(partial, target)
                self.assertEqual(partial.parent.parent, out)
                partial.write_bytes(b"new export" if success else b"incomplete")
                mutate(change)
                return SimpleNamespace(returncode=0 if success else 1, stderr="hardware unavailable")

            def probe_output(path):
                self.assertEqual(target.read_bytes(), b"previous accepted export")
                self.assertNotEqual(Path(path), target)
                if validation == "probe":
                    raise video_edit.VideoEditError("probe failed")
                info = {"width": 160 if preview else 320, "height": 90 if preview else 180,
                        "duration": 2, "video_codec": "h264", "audio_codec": "aac"}
                faults = {"audio": {"audio_codec": None}, "video": {"video_codec": None},
                          "truncated": {"duration": 1}, "longer": {"duration": 3},
                          "zero_duration": {"duration": 0}, "nan_duration": {"duration": float("nan")},
                          "zero_width": {"width": 0}, "nan_height": {"height": float("nan")},
                          "bool_width": {"width": True}, "huge_duration": {"duration": 10**400},
                          "ratio": {"height": 100}}
                return {**info, **faults.get(validation, {})}

            with patch.object(video_edit.kitconfig, "_load", return_value={"render_encoder": "h264_nvenc"}), \
                    patch.object(video_edit.kitconfig, "simple", return_value=False), \
                    patch.object(render, "probe", return_value={"width": 320, "height": 180, "fps": 30, "duration": 2}), \
                    patch.object(draft_quality, "probe", return_value={"width": 320, "height": 180, "fps": 30, "duration": 2}), \
                    patch.object(video_edit, "probe", side_effect=probe_output) as output_probe, \
                    patch.object(delivery_manifest, "commit", return_value="a" * 40), \
                    patch.object(render.subprocess, "run", side_effect=process) as run:
                if validation:
                    expected = subprocess.TimeoutExpired if validation == "timeout" else video_edit.VideoEditError
                    with self.assertRaises(expected):
                        render.render_draft(str(folder), str(out), preview=preview)
                elif change == "evidence":
                    with self.assertRaisesRegex(OSError, "manifest disk full"):
                        render.render_draft(str(folder), str(out), preview=preview)
                elif change:
                    with self.assertRaisesRegex(video_edit.VideoEditError, 'เปลี่ยนระหว่างส่งออก'):
                        render.render_draft(str(folder), str(out), preview=preview)
                elif success:
                    result = render.render_draft(str(folder), str(out), preview=preview)
                    self.assertEqual(result["encoder"], encoder)
                    self.assertEqual(result["duration"], 2)
                    self.assertGreaterEqual(result["encode_seconds"], 0)
                    output_probe.assert_called_once()
                else:
                    with self.assertRaisesRegex(video_edit.VideoEditError, r"export failed \(h264_nvenc\)"):
                        render.render_draft(str(folder), str(out), preview=preview)
                decoded = success and change not in {"source", "style", "target"} and validation in {None, "decode", "timeout"}
                self.assertEqual(run.call_count, 2 if decoded else 1)
            self.assertEqual(target.read_bytes(), b'new externally approved export' if change in {"target", "decode_target"} else
                             b"new export" if success and not change and not validation else b"previous accepted export")
            self.assertEqual([p for p in out.iterdir() if p.name != ".clipkit-deliveries"], [target])
            if success and not change and not validation:
                record = json.loads(Path(result["delivery_manifest"]).read_text(encoding="utf-8"))
                self.assertEqual(record["checks"]["full_av_decode"], "passed")
                self.assertEqual(record["human_audio_sync"], "pending")


if __name__ == "__main__":
    unittest.main()
