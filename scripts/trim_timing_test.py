"""Cuts preserve one shared clock for words, held subtitles, motion and placed b-roll."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
with tempfile.TemporaryDirectory() as folder:
    config = Path(folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import capcut_edit as edit
import draft_quality
import render
import video_editor


class TrimTiming(unittest.TestCase):
    def test_same_words_twice_keep_independent_keyword_clock_after_cut_and_undo(self):
        self.draft["duration"] = 9_000_000
        for key in ("source_timerange", "target_timerange"):
            self.draft["tracks"][0]["segments"][0][key]["duration"] = 9_000_000
        self.draft["tracks"][1]["segments"][1]["target_timerange"]["duration"] = 3_000_000
        self.draft["materials"]["texts"][1]["content"] = json.dumps({"text": "normal keyword", "styles": []})
        self.write("draft_content.json", self.draft)
        clocks = {"cue:0": [[0, 1, "normal "], [2, 3, "keyword"]],
                  "cue:1": [[0, 1, "normal "], [2, 3, "keyword"]]}
        self.write("clipkit_words.json", clocks)
        self.write("clipkit_punch.json", {"cue:0": [["normal", "keyword"]], "cue:1": [["normal keyword", ""]]})
        video_editor.draft_cut(video_editor.CutRange(path=str(self.folder), start=3, end=3.5))
        actual = self.read("clipkit_words.json")
        self.assertEqual(actual["cue:0"][1][0], 1.5)
        self.assertEqual(actual["cue:1"][1][0], 2)
        metadata = {"width": 1920, "height": 1080, "duration": 9, "fps": 30}
        with patch.object(render, "probe", return_value=metadata), patch.object(draft_quality, "probe", return_value=metadata):
            timeline = render.render_draft(str(self.folder), "", dry=True)
        self.assertEqual([p["cue_id"] for p in timeline["phrases"]], ["0", "1"])
        self.assertEqual([p["punch"] for p in timeline["phrases"]], ["keyword", ""])
        self.assertEqual(timeline["phrases"][0]["hit"], 3.5)
        video_editor.draft_uncut(video_editor.DraftPath(path=str(self.folder)))
        self.assertEqual(self.read("clipkit_words.json"), clocks)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "project"
        self.folder.mkdir()
        self.source = self.folder / "source.mp4"
        self.source.touch()
        config=self.folder.parent/'config.json';config.write_text('{"render_encoder":"libx264"}',encoding='utf-8')
        self.enterContext(patch.object(render.video_edit.kitconfig,'_CANDIDATES',[config]))
        import kit_settings
        self.enterContext(patch.object(kit_settings,'CONFIG',config))
        clip = {"scale": {"x": 1, "y": 1}, "transform": {"x": 0, "y": 0}}
        self.draft = {"id": "test", "duration": 8_000_000, "canvas_config": {"width": 1920, "height": 1080},
            "materials": {"videos": [{"id": "v", "path": str(self.source)}], "texts": [
                {"id": str(n), "font_size": 15, "content": json.dumps({"text": text, "styles": []})}
                for n, text in enumerate(("normal keyword", "next"))]},
            "tracks": [{"type": "video", "segments": [{"id": "v", "material_id": "v", "clip": copy.deepcopy(clip),
                "extra_material_refs": [], "source_timerange": {"start": 0, "duration": 8_000_000},
                "target_timerange": {"start": 0, "duration": 8_000_000}}]},
                {"type": "text", "segments": [{"id": str(n), "material_id": str(n), "clip": copy.deepcopy(clip),
                    "target_timerange": {"start": a, "duration": b - a}}
                    for n, a, b in ((0, 2_000_000, 5_000_000), (1, 6_000_000, 8_000_000))]}]}
        self.write("draft_content.json", self.draft)
        self.write("clipkit_words.json", {"normal keyword": [[0, 1, "normal "], [2, 3, "keyword"]], "next": [[0, 2, "next"]]})
        self.write("clipkit_style.json", {"anim": "pair", "music": "", "sfx": False})
        self.write("clipkit_punch.json", {"normal keyword": [["normal", "keyword"]], "next": [["next", ""]]})
        self.log = patch.object(edit, "_log_change")
        self.log.start()
        self.addCleanup(self.log.stop)

    def write(self, name, value):
        (self.folder / name).write_text(json.dumps(value), encoding="utf-8")

    def read(self, name):
        return json.loads((self.folder / name).read_text(encoding="utf-8"))

    def trim(self, ranges):
        return edit.trim_pauses(str(self.folder), ranges=ranges)

    def test_cut_moves_word_onset_and_holds_until_next_normal(self):
        result = self.trim([(3, 3.5), (0, 1)])
        self.assertEqual(result["result_length"], 6.5)
        self.assertEqual(self.read("clipkit_words.json")["cue:0"], [[0, 1, "normal "], [1.5, 2.5, "keyword"]])
        metadata = {"width": 1920, "height": 1080, "duration": 8, "fps": 30}
        with patch.object(render, "probe", return_value=metadata), patch.object(draft_quality, "probe", return_value=metadata):
            timeline = render.render_draft(str(self.folder), "", dry=True)
        normal, next_line = timeline["phrases"]
        self.assertEqual((normal["start"], normal["hit"], normal["end"]), (1, 2.5, 4.5))
        self.assertEqual(next_line["start"], 4.5)

    def test_ranges_merge_before_mapping(self):
        result = self.trim([(2, 4), (1, 3)])
        self.assertEqual(result["cuts"], [{"start": 1, "end": 4, "gain": 3}])
        self.assertEqual(result["result_length"], 5)

    def test_automatic_silence_cut_cannot_remove_a_timed_word(self):
        quiet = [{"start": 3.9, "end": 4.6, "length": .7}]
        with patch.object(edit, "quiet_spans", return_value=quiet):
            self.assertEqual(edit.trim_pauses(str(self.folder), apply=False)["cuts"], [])

    def test_automatic_cut_keeps_only_silence_outside_word_padding(self):
        quiet = [{"start": 3.5, "end": 4.7, "length": 1.2}]
        with patch.object(edit, "quiet_spans", return_value=quiet):
            result = edit.trim_pauses(str(self.folder), apply=False)
        self.assertEqual(len(result["cuts"]), 1)
        self.assertAlmostEqual(result["cuts"][0]["start"], 3.6)
        self.assertAlmostEqual(result["cuts"][0]["end"], 3.9)

    def test_short_subtitle_does_not_gain_time_or_overlap(self):
        self.draft["tracks"][1]["segments"][0]["target_timerange"] = {"start": 0, "duration": 200_000}
        self.write("draft_content.json", self.draft)
        self.trim([(0.1, 0.2)])
        first = self.read("draft_content.json")["tracks"][1]["segments"][0]["target_timerange"]
        self.assertEqual(first, {"start": 0, "duration": 100_000})

    def test_motion_and_broll_move_once_and_undo_restores_both(self):
        (self.folder/"motion.mov").write_bytes(b"synthetic motion")
        (self.folder/"broll.mp4").write_bytes(b"synthetic broll")
        self.write("clipkit_overlays.json", [{"start": 6, "duration": 1, "file": str(self.folder/"motion.mov")}])
        self.write("clipkit_placed.json", {"broll": [{"start": 6.5, "dur": .5, "file": str(self.folder/"broll.mp4")}]})
        video_editor.draft_cut(video_editor.CutRange(path=str(self.folder), start=0, end=1))
        self.assertEqual(self.read("clipkit_overlays.json")[0]["start"], 5)
        self.assertEqual(self.read("clipkit_placed.json")["broll"][0]["start"], 5.5)
        video_editor.draft_uncut(video_editor.DraftPath(path=str(self.folder)))
        self.assertEqual(self.read("clipkit_overlays.json")[0]["start"], 6)
        self.assertEqual(self.read("clipkit_placed.json")["broll"][0]["start"], 6.5)
        self.assertEqual(edit.read_draft(str(self.folder))["duration"], 8)

    def test_undo_restores_absence_of_sidecars_created_after_cut(self):
        video_editor.draft_cut(video_editor.CutRange(path=str(self.folder), start=0, end=1))
        self.write("clipkit_placed.json", {"broll": [{"start": 4, "dur": 1}]})
        self.write("clipkit_overlays.json", [{"start": 4, "duration": 1}])
        video_editor.draft_uncut(video_editor.DraftPath(path=str(self.folder)))
        self.assertFalse((self.folder / "clipkit_placed.json").exists())
        self.assertFalse((self.folder / "clipkit_overlays.json").exists())
        self.assertEqual(edit.read_draft(str(self.folder))["duration"], 8)

    def test_undo_write_failure_restores_current_state_and_keeps_snapshot(self):
        video_editor.draft_cut(video_editor.CutRange(path=str(self.folder), start=0, end=1))
        self.write("clipkit_placed.json", {"broll": []})
        before = {name: file.read_bytes() for name, file in __import__('web_ai_transaction')._files(self.folder).items()}
        original = video_editor.atomic.write_bytes
        failed = False

        def fail_once(file, data):
            nonlocal failed
            if Path(file).name == "clipkit_words.json" and not failed:
                failed = True
                raise OSError("injected disk error")
            return original(file, data)

        with patch.object(video_editor.atomic, "write_bytes", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "injected disk error"):
                video_editor.draft_uncut(video_editor.DraftPath(path=str(self.folder)))
        after = {name: file.read_bytes() for name, file in __import__('web_ai_transaction')._files(self.folder).items()}
        self.assertEqual(after, before)
        self.assertEqual(len(list((self.folder / "clipkit_undo").iterdir())), 1)

    def test_cut_crossing_rendered_media_refuses_before_any_write(self):
        for name, data in (("clipkit_overlays.json", [{"start": 3, "duration": 2}]),
                           ("clipkit_placed.json", {"broll": [{"start": 3, "dur": 2}]})):
            with self.subTest(name=name):
                self.write(name, data)
                before = {f: f.read_bytes() for f in self.folder.iterdir() if f.is_file()}
                with self.assertRaisesRegex(edit.VideoEditError, "สร้างใหม่หลังตัด"):
                    self.trim([(3.5, 4)])
                self.assertEqual(before, {f: f.read_bytes() for f in self.folder.iterdir() if f.is_file()})
                (self.folder / name).unlink()

    def test_nonfinite_ranges_refused_and_empty_means_no_cut(self):
        before = (self.folder / "draft_content.json").read_bytes()
        for ranges in ([(float("nan"), 2)], [(0, float("inf"))], [(3, 2)]):
            with self.assertRaises(edit.VideoEditError):
                self.trim(ranges)
        with patch.object(edit, "quiet_spans", side_effect=AssertionError("explicit empty list")):
            self.assertEqual(self.trim([])["removed"], 0)
        self.assertEqual((self.folder / "draft_content.json").read_bytes(), before)

    def test_native_extra_tracks_refuse_before_draft_or_word_writes(self):
        for kind in ("video", "audio"):
            with self.subTest(kind=kind):
                draft = copy.deepcopy(self.draft)
                draft["tracks"].append({"type": kind, "segments": [{"material_id": "extra"}]})
                self.write("draft_content.json", draft)
                before = {f: f.read_bytes() for f in self.folder.iterdir() if f.is_file()}
                with self.assertRaisesRegex(edit.VideoEditError, "ตัดเนื้อหาใน EditorKrab ก่อน"):
                    self.trim([(0, 1)])
                self.assertEqual(before, {f: f.read_bytes() for f in self.folder.iterdir() if f.is_file()})


if __name__ == "__main__":
    unittest.main()
