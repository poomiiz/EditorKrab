"""Repeated spoken text remains independent through AI import, manual edits and captions."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trim_timing_test as fixture
import capcut_edit
import cue_data
import draft_quality
import hf_build
import render
import video_editor
import web_ai_json
import web_ai_identity
import capcut_font_test


class OccurrenceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("host_platform.capcut_running", return_value=False))
        self.f = fixture.TrimTiming()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.path = str(self.f.folder)
        self.f.draft["materials"]["texts"][1]["content"] = json.dumps({"text": "normal keyword", "styles": []})
        self.f.write("draft_content.json", self.f.draft)
        self.f.write("clipkit_words.json", {"cue:0": [[0, 1, "normal "], [2, 3, "keyword"]],
                                            "cue:1": [[0, .5, "normal "], [1, 2, "keyword"]]})
        self.f.write("clipkit_punch.json", {"cue:0": [["normal", "keyword"]], "cue:1": [["normal keyword", ""]]})

    def test_ai_import_stores_distinct_picks_captions_and_packet_clocks(self):
        self.f.write("clipkit_style.json", {"anim": "pair", "preset": "with-caption"})
        answer = {"clipkit_web_ai": 2, **web_ai_identity.issue("clip", self.path),
                  "step": "clip", "project": "project", "hook": [],
                  "caption": {"1": "first occurrence", "2": "second occurrence"},
                  "lines": [{"n": 1, "cue_id": "0", "items": [["normal", "keyword"]]},
                            {"n": 2, "cue_id": "1", "items": [["normal keyword", ""]]}]}
        web_ai_json.import_clip(self.path, answer)
        self.assertNotEqual(self.f.read("clipkit_punch.json")["cue:0"], self.f.read("clipkit_punch.json")["cue:1"])
        self.assertEqual(self.f.read("clipkit_caption.json"), {"cue:0": "first occurrence", "cue:1": "second occurrence"})
        packet = web_ai_json.export_clip(self.path).read_text(encoding="utf-8")
        self.assertIn('"cue_id": "0"', packet)
        self.assertIn('"cue_id": "1"', packet)
        self.assertIn('"start": 4.0', packet)
        self.assertIn('"start": 7.0', packet)

    def test_manual_edit_changes_only_selected_occurrence(self):
        import spelling
        original_clock = self.f.read("clipkit_words.json")
        with patch.object(spelling, "learn"):
            capcut_edit.set_line_text(self.path, "normal keyword", "normal different", "1")
        rows = capcut_edit.read_draft(self.path)["subtitles"]
        self.assertEqual([r["text"] for r in rows], ["normal keyword", "normal different"])
        self.assertEqual([r["id"] for r in rows], ["0", "1"])
        self.assertEqual(self.f.read("clipkit_words.json"), original_clock)
        self.assertEqual(self.f.read("clipkit_punch.json")["cue:0"], [["normal", "keyword"]])
        self.assertEqual(self.f.read("clipkit_punch.json")["cue:1"], [["normaldifferent", ""]])

    def test_legacy_ambiguous_clock_refused_before_any_cut_write(self):
        self.f.write("clipkit_words.json", {"normal keyword": [[0, 1, "normal "], [2, 3, "keyword"]]})
        before = {f: f.read_bytes() for f in self.f.folder.iterdir() if f.is_file()}
        with self.assertRaises(cue_data.VideoEditError):
            capcut_edit.trim_pauses(self.path, ranges=[(0, 1)])
        self.assertEqual(before, {f: f.read_bytes() for f in self.f.folder.iterdir() if f.is_file()})

    def test_manual_caption_and_phrase_api_select_by_cue(self):
        video_editor.draft_caption(video_editor.CaptionEdit(path=self.path, line="normal keyword", cue_id="1", text="second only"))
        video_editor.draft_phrases(video_editor.PhraseEdit(path=self.path, line="normal keyword", cue_id="1", pairs=[["", "normal keyword"]]))
        self.assertEqual(self.f.read("clipkit_caption.json"), {"cue:1": "second only"})
        self.assertEqual(self.f.read("clipkit_punch.json")["cue:0"], [["normal", "keyword"]])
        self.assertEqual(self.f.read("clipkit_punch.json")["cue:1"], [["", "normal keyword"]])
        before = self.f.read("draft_content.json")
        with self.assertRaises(cue_data.VideoEditError):
            capcut_edit.set_line_time(self.path, "normal keyword", 1, 2)
        self.assertEqual(self.f.read("draft_content.json"), before)

    def test_cut_first_occurrence_places_motion_on_second_cue_at_its_new_time(self):
        answer = {"clipkit_web_ai": 2, **web_ai_identity.issue("clip", self.path),
                  "step": "clip", "project": "project", "hook": [],
                  "cut": [1], "lines": [{"n": 1, "cue_id": "0"},
                                         {"n": 2, "cue_id": "1", "items": [["normal", "keyword"]]}],
                  "motion": [{"n": 2, "template": "hook-title"}]}
        with patch.object(video_editor, "prepare_overlay", return_value={"candidate":{"start":0,"duration":1}}), \
                patch.object(video_editor, "overlay_plan", return_value={}), \
                patch.object(video_editor, "render_overlay", return_value={"start":0}) as overlay:
            web_ai_json.import_clip(self.path, answer)
        request = overlay.call_args.args[0]
        self.assertEqual((request.cue_id, request.start, request.end), ("1", 2, 4))
        self.assertEqual(self.f.read("clipkit_words.json")["cue:1"], [[0, .5, "normal "], [1, 2, "keyword"]])

    def test_transcription_assigns_distinct_ids_to_repeated_lines(self):
        original = self.f.read("clipkit_words.json")
        heard = [{"start": 0, "end": 1, "text": "same", "words": [[0, 1, "same"]]},
                 {"start": 3, "end": 5, "text": "same", "words": [[3, 5, "same"]]}]
        with patch.object(capcut_edit, "_from_transcript", return_value=heard):
            result = capcut_edit.transcribe_draft(self.path)
        rows = result["subtitles"]
        self.assertEqual(self.f.read("clipkit_words.json"), original)
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])
        capcut_edit.set_subtitles(self.path, rows)
        actual = capcut_edit.read_draft(self.path)["subtitles"]
        self.assertEqual([r["id"] for r in actual], [r["id"] for r in rows])
        clocks = self.f.read("clipkit_words.json")
        self.assertEqual(clocks[cue_data.key(rows[0]["id"])], [[0, 1, "same"]])
        self.assertEqual(clocks[cue_data.key(rows[1]["id"])], [[0, 2, "same"]])

    def test_append_new_timed_cue_retains_existing_occurrence_clocks(self):
        old = self.f.read("clipkit_words.json")
        capcut_edit.set_subtitles(self.path, [{"id": "new", "start": .2, "end": 1.5,
            "text": "new words", "words": [[0, 1, "new words"]]}], replace=False)
        self.assertEqual(self.f.read("clipkit_words.json"), {**old, "cue:new": [[0, 1, "new words"]]})

    def test_shared_native_material_does_not_change_other_occurrence(self):
        import spelling
        self.f.draft["tracks"][1]["segments"][1]["material_id"] = "0"
        self.f.write("draft_content.json", self.f.draft)
        with patch.object(spelling, "learn"):
            capcut_edit.set_line_text(self.path, "normal keyword", "changed text", "1")
        rows = capcut_edit.read_draft(self.path)["subtitles"]
        self.assertEqual([r["text"] for r in rows], ["normal keyword", "changed text"])

    def test_invalid_words_or_duplicate_cue_ids_are_refused_before_writes(self):
        before = self.f.read("draft_content.json")
        for words in ([[float("nan"), 1, "bad"]], [[2, 1, "backward"]], [[True, 1, "boolean"]], [[0, 1]]):
            with self.subTest(words=words), self.assertRaises(cue_data.VideoEditError):
                capcut_edit.set_subtitles(self.path, [{"id": "x", "start": 0, "end": 2, "text": "bad", "words": words}])
            self.assertEqual(self.f.read("draft_content.json"), before)
        with self.assertRaises(cue_data.VideoEditError):
            capcut_edit.set_subtitles(self.path, [{"id": "0", "start": 0, "end": 1, "text": "bad"}], replace=False)
        self.assertEqual(self.f.read("draft_content.json"), before)

    def test_caption_occurrences_stay_separate_in_hyperframes_and_native_output(self):
        self.f.write("clipkit_style.json", {"anim": "pair", "preset": "with-caption", "music": "", "sfx": False})
        self.f.write("clipkit_caption.json", {"cue:0": "first caption", "cue:1": "second caption"})
        metadata = {"width": 1920, "height": 1080, "duration": 8, "fps": 30}
        with patch.object(render, "probe", return_value=metadata), \
                patch.object(draft_quality, "probe", return_value=metadata), \
                patch.object(render.video_edit.kitconfig, "_load", return_value={}):
            timeline = render.render_draft(self.path, "", dry=True)
        with patch.object(render, "render_draft", return_value=timeline), \
                patch.object(hf_build, "_proxy", return_value="fixture.mp4"):
            page = hf_build.build(self.path).read_text(encoding="utf-8")
        for caption, start, duration in (("first caption", "2.000", "4.000"), ("second caption", "6.000", "2.000")):
            node = next(n for n in re.findall(r'<div id="t[^>]+><div[^>]*>[^<]+</div></div>', page) if caption in n)
            self.assertEqual(float(re.search(r'data-start="([\d.]+)"', node)[1]), float(start))
            self.assertEqual(float(re.search(r'data-duration="([\d.]+)"', node)[1]), float(duration))
        harness = capcut_font_test.CapCutFontTests()
        harness.setUp()
        try:
            with tempfile.TemporaryDirectory() as base:
                _, (output, _) = harness.run_build(Path(base), timeline=timeline)
                draft = json.loads((output / "draft_content.json").read_text(encoding="utf-8"))
                texts = {m["id"]: json.loads(m["content"])["text"] for m in draft["materials"]["texts"]}
                spans = {texts[s["material_id"]]: s["target_timerange"] for t in draft["tracks"] if t["type"] == "text"
                         for s in t["segments"] if texts[s["material_id"]] in ("first caption", "second caption")}
                self.assertEqual(spans, {"first caption": {"start": 2_000_000, "duration": 4_000_000},
                                         "second caption": {"start": 6_000_000, "duration": 2_000_000}})
        finally:
            harness.doCleanups()


if __name__ == "__main__":
    unittest.main()
