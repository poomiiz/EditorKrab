"""Bad timeline/media references fail before export; no GPU, network or user draft changes."""
import copy
import json
import os
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
        import draft_quality as quality
from video_edit import VideoEditError


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "source.mp4"
        self.source.touch()
        self.draft = {"duration": 10_000_000, "canvas_config": {"width": 1080, "height": 1920},
                      "materials": {"videos": [{"id": "v", "path": str(self.source)}],
                                    "texts": [{"id": "t", "content": '{"text":"ทดสอบ"}'}]},
                      "tracks": [{"type": "video", "segments": [{"material_id": "v",
                          "target_timerange": {"start": 0, "duration": 10_000_000},
                          "source_timerange": {"start": 1_000_000, "duration": 10_000_000}}]},
                                 {"type": "text", "segments": [{"material_id": "t",
                                     "target_timerange": {"start": 1_000_000, "duration": 2_000_000}}]}]}

    def check(self):
        content = self.folder / "draft_content.json"
        content.write_text(json.dumps(self.draft, ensure_ascii=False), encoding="utf-8")
        before = content.read_bytes()
        try:
            with patch.object(quality, "probe", return_value={"duration": 12}) as probe:
                result = quality.check(str(self.folder))
        finally:
            self.assertEqual(content.read_bytes(), before)
        return result, probe

    def test_valid_draft_leaves_human_and_native_checks_pending(self):
        result, probe = self.check()
        self.assertTrue(result["structural_pass"])
        self.assertEqual(result["human_audio_sync"], "pending")
        self.assertEqual(result["capcut_open"], "pending")
        self.assertEqual(result["text_coverage_gaps"], [
            {"start": 0.0, "end": 1.0, "duration": 1.0}, {"start": 3.0, "end": 10.0, "duration": 7.0}])
        probe.assert_called_once()

    def test_invalid_times_canvas_and_duration_fail(self):
        original = copy.deepcopy(self.draft)
        for value in (-1, 0, float("nan"), float("inf"), True, "10", 10**400):
            with self.subTest(value=str(value)[:20]):
                self.draft = copy.deepcopy(original)
                self.draft["tracks"][0]["segments"][0]["target_timerange"]["duration"] = value
                with self.assertRaises(VideoEditError):
                    self.check()
                self.draft = copy.deepcopy(original)
                self.draft["canvas_config"]["width"] = value
                with self.assertRaises(VideoEditError):
                    self.check()
        self.draft = copy.deepcopy(original)
        self.draft["duration"] = float("nan")
        with self.assertRaises(VideoEditError):
            self.check()

    def test_declared_duration_matches_timeline_with_microsecond_tolerance(self):
        for duration in (1, 9_000_000, 9_999_998, 10_000_002, 11_000_000):
            with self.subTest(duration=duration):
                self.draft["duration"] = duration
                with self.assertRaisesRegex(VideoEditError, "declared duration"):
                    self.check()
        for duration in (9_999_999, 10_000_000, 10_000_001):
            with self.subTest(duration=duration):
                self.draft["duration"] = duration
                self.assertTrue(self.check()[0]["structural_pass"])
        del self.draft["duration"]
        self.assertTrue(self.check()[0]["structural_pass"])

    def test_mismatched_duration_stops_all_renderers_without_changing_outputs(self):
        import render
        import hf_build
        import capcut_build
        self.draft["duration"] = 11_000_000
        content = self.folder / "draft_content.json"
        content.write_text(json.dumps(self.draft), encoding="utf-8")
        output = self.folder / "exports"
        output.mkdir()
        (output / (self.folder.name + ".mp4")).write_bytes(b"previous accepted export")
        page = self.folder / "clipkit_hf" / "index.html"
        page.parent.mkdir()
        page.write_text("previous preview", encoding="utf-8")
        before = {p.relative_to(self.folder): p.read_bytes() for p in self.folder.rglob("*") if p.is_file()}
        actions = {
            "cpu": lambda: render.render_draft(str(self.folder), str(output)),
            "dry": lambda: render.render_draft(str(self.folder), "", dry=True),
            "hyperframes": lambda: hf_build.build(str(self.folder)),
            "native": lambda: capcut_build.build(str(self.folder)),
        }
        with patch.object(render.subprocess, "run") as process, \
                patch.object(capcut_build.host_platform, "capcut_running", return_value=False), \
                patch.object(render, "_video_encoder", return_value=["-c:v", "libx264"]):
            for name, action in actions.items():
                with self.subTest(renderer=name):
                    with self.assertRaisesRegex(VideoEditError, "declared duration"):
                        action()
                    self.assertEqual(before, {p.relative_to(self.folder): p.read_bytes()
                                             for p in self.folder.rglob("*") if p.is_file()})
                    self.assertFalse(list(self.folder.parent.glob(self.folder.name + " \u00b7 CapCut*")))
            process.assert_not_called()

    def test_missing_segment_and_extra_material_references_fail(self):
        segment = self.draft["tracks"][1]["segments"][0]
        segment["material_id"] = "missing"
        with self.assertRaisesRegex(VideoEditError, "missing material"):
            self.check()
        segment["material_id"] = "t"
        segment["extra_material_refs"] = ["missing"]
        with self.assertRaisesRegex(VideoEditError, "missing material"):
            self.check()

    def test_missing_media_and_source_overrun_fail(self):
        self.draft["materials"]["videos"][0]["path"] = str(self.folder / "missing.mp4")
        with self.assertRaisesRegex(VideoEditError, "media is missing"):
            self.check()
        self.draft["materials"]["videos"][0]["path"] = str(self.source)
        self.draft["tracks"][0]["segments"][0]["source_timerange"]["duration"] = 20_000_000
        self.draft["tracks"][0]["segments"][0]["target_timerange"]["duration"] = 20_000_000
        self.draft["duration"] = 20_000_000
        with self.assertRaisesRegex(VideoEditError, "source range exceeds"):
            self.check()

    def test_main_video_cannot_reference_a_text_material(self):
        self.draft["tracks"][0]["segments"][0]["material_id"] = "t"
        with self.assertRaisesRegex(VideoEditError, "wrong material type"):
            self.check()

    def test_text_outside_video_and_duplicate_material_id_fail(self):
        segment = self.draft["tracks"][1]["segments"][0]
        segment["target_timerange"] = {"start": 9_000_000, "duration": 2_000_000}
        with self.assertRaisesRegex(VideoEditError, "beyond the main video"):
            self.check()
        segment["target_timerange"]["duration"] = 1_000_000
        self.draft["materials"]["texts"].append(copy.deepcopy(self.draft["materials"]["texts"][0]))
        with self.assertRaisesRegex(VideoEditError, "duplicate material"):
            self.check()

    def test_repeated_source_is_probed_once(self):
        second = copy.deepcopy(self.draft["tracks"][0]["segments"][0])
        second["target_timerange"]["start"] = 10_000_000
        second["source_timerange"]["start"] = 0
        self.draft["tracks"][0]["segments"].append(second)
        self.draft["duration"] = 20_000_000
        _, probe = self.check()
        probe.assert_called_once()

    def test_main_video_gaps_and_overlaps_are_not_silently_concatenated(self):
        original = copy.deepcopy(self.draft)
        for start in (9_000_000, 11_000_000):
            with self.subTest(start=start):
                self.draft = copy.deepcopy(original)
                second = copy.deepcopy(self.draft["tracks"][0]["segments"][0])
                second["target_timerange"]["start"] = start
                self.draft["tracks"][0]["segments"].append(second)
                with self.assertRaisesRegex(VideoEditError, "gaps or overlaps"):
                    self.check()
        self.draft = copy.deepcopy(original)
        self.draft["tracks"][0]["segments"][0]["target_timerange"]["start"] = 1
        result, _ = self.check()  # one microsecond of numerical tolerance
        self.assertTrue(result["structural_pass"])
        self.draft["tracks"][0]["segments"][0]["target_timerange"]["start"] = 1_000_000
        with self.assertRaisesRegex(VideoEditError, "contiguous from zero"):
            self.check()

    def test_source_duration_and_speed_must_match_what_exporter_supports(self):
        segment = self.draft["tracks"][0]["segments"][0]
        segment["source_timerange"]["duration"] = 5_000_000
        with self.assertRaisesRegex(VideoEditError, "durations must match"):
            self.check()
        segment["source_timerange"]["duration"] = 10_000_000
        segment["speed"] = 2
        with self.assertRaisesRegex(VideoEditError, "speed must be 1"):
            self.check()
        segment["speed"] = 1
        segment["extra_material_refs"] = ["speed"]
        self.draft["materials"]["speeds"] = [{"id": "speed", "speed": 2, "curve_speed": None}]
        with self.assertRaisesRegex(VideoEditError, "speed must be 1"):
            self.check()
        self.draft["materials"]["speeds"][0].update(speed=1, curve_speed={"points": []})
        with self.assertRaisesRegex(VideoEditError, "without a speed curve"):
            self.check()

    def test_still_image_is_required_but_not_limited_to_one_frame(self):
        image = self.folder / "still.png"
        image.touch()
        self.draft["materials"]["videos"][0]["path"] = str(image)
        _, probe = self.check()
        probe.assert_not_called()

    def test_audio_range_is_checked_without_using_video_probe(self):
        audio = self.folder / "sound.wav"
        audio.touch()
        self.draft["materials"]["audios"] = [{"id": "a", "path": str(audio)}]
        segment = {"material_id": "a", "target_timerange": {"start": 0, "duration": 2_000_000},
                   "source_timerange": {"start": 1_000_000, "duration": 2_000_000}}
        self.draft["tracks"].append({"type": "audio", "segments": [segment]})
        with patch.object(quality.capcut_edit, "_music_meta", return_value={"duration": 3.0}) as probe_audio:
            result, probe_video = self.check()
        self.assertEqual(result["media_files_checked"], 2)
        probe_audio.assert_called_once_with(audio)
        probe_video.assert_called_once()
        segment["source_timerange"]["duration"] = 3_000_000
        with patch.object(quality.capcut_edit, "_music_meta", return_value={"duration": 3.0}):
            with self.assertRaisesRegex(VideoEditError, "source range exceeds"):
                self.check()

    def test_malformed_json_and_changed_seal_are_not_accepted(self):
        content = self.folder / "draft_content.json"
        content.write_text("{broken", encoding="utf-8")
        with self.assertRaises(VideoEditError):
            quality.check(str(self.folder))
        content.write_text(json.dumps(self.draft), encoding="utf-8")
        seal = self.folder / quality.capcut_edit.SEAL
        seal.write_text('{"draft_content":"other version"}', encoding="utf-8")
        with self.assertRaises(VideoEditError):
            quality.check(str(self.folder))
        self.assertEqual(seal.read_text(encoding="utf-8"), '{"draft_content":"other version"}')


if __name__ == "__main__":
    unittest.main()
