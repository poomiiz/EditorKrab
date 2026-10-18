"""Explicit layout repair keeps unsafe geometry inspectable, never exportable."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import caption_quality_test as fixture
import subtitle_export_test
import web_ai_json

render, api = fixture.render, fixture.api


class SubtitleRepairTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.CaptionQualityTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.folder, self.path = self.f.folder, self.f.path
        preset = subtitle_export_test.SubtitleExportTests().preset(fixed=True)
        preset.pop("caption")
        (self.folder / "repair.json").write_text(json.dumps(preset))
        self.enterContext(patch.object(render, "PRESETS", self.folder))
        self.f.f.f.write("clipkit_style.json", {"anim": "pair", "preset": "repair", "music": "", "sfx": False})
        self.f.f.f.write("clipkit_punch.json", {
            "cue:0": [["normal", "keyword", {"show": ["wide " * 100, "key"]}]],
            "cue:1": [["next", "", {"show": ["wide " * 100, ""]}]],
        })

    def save(self, cue, normal):
        lead, punch = ("normal", "keyword") if cue == "0" else ("next", "")
        return api.draft_phrases(api.PhraseEdit(path=self.path, line=lead + (" " + punch if punch else ""),
            cue_id=cue, pairs=[[lead, punch, {"show": [normal, "key" if punch else ""]}]]))

    def test_explicit_preview_reports_errors_without_shrinking_or_touching_source(self):
        before = {name: (self.folder / name).read_bytes() for name in ("draft_content.json", "clipkit_words.json", "clipkit_punch.json")}
        with self.assertRaises(render.VideoEditError):
            render.render_draft(self.path, "", dry=True)
        timeline = api.draft_timeline(self.path)
        self.assertEqual({x["cue_id"] for x in timeline["quality"]["layout_errors"]}, {"0", "1"})
        self.assertTrue(timeline["subtitle_events"])
        self.assertFalse(timeline["shrunk"])
        with self.assertRaises(render.VideoEditError):
            render.render_draft(self.path, "", dry=True, final=True, repair=True)
        self.assertEqual(before, {name: (self.folder / name).read_bytes() for name in before})

    def test_manual_repairs_one_cue_at_a_time_but_imports_remain_strict(self):
        before = [(self.folder / name).read_bytes() for name in ("draft_content.json", "clipkit_words.json")]
        with self.assertRaises(render.VideoEditError):
            web_ai_json.validate_subtitles(self.path)
        self.save("0", "normal")
        self.assertEqual({x["cue_id"] for x in api.draft_timeline(self.path)["quality"]["layout_errors"]}, {"1"})
        with self.assertRaises(fixture.HTTPException):
            self.save("0", "wide " * 110)
        self.save("1", "next")
        self.assertEqual(api.draft_timeline(self.path)["quality"]["layout_errors"], [])
        web_ai_json.validate_subtitles(self.path)
        self.assertEqual(before, [(self.folder / name).read_bytes() for name in ("draft_content.json", "clipkit_words.json")])

    def test_all_final_adapters_refuse_layout_errors_before_subprocess_or_publication(self):
        import capcut_build
        import hf_build
        before = {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()}
        with tempfile.TemporaryDirectory() as directory, patch.object(render.subprocess, "run") as process:
            for action in (
                lambda: render.render_draft(self.path, directory),
                lambda: render.render_draft(self.path, directory, preview=True, repair=True),
                lambda: hf_build._build(self.path, final=True, repair=True),
                lambda: capcut_build.build(self.path),
            ):
                with self.subTest(adapter=action), self.assertRaises(render.VideoEditError):
                    action()
            process.assert_not_called()
            self.assertFalse(list(Path(directory).iterdir()))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()})

    def test_repair_does_not_invent_word_clocks_or_allow_new_collisions(self):
        self.save("0", "normal")
        self.save("1", "next")
        timeline = api.draft_timeline(self.path)
        first = timeline["subtitle_events"][0]
        overlapping = dict(first, role="emphasis", cue_id="other")
        issues = []
        render.check_pair_events([first, overlapping], timeline["width"], timeline["height"], layout_errors=issues)
        self.assertEqual(issues[0]["code"], "subtitle_overlap")
        self.assertEqual({issues[0]["cue_id"], issues[0]["other_cue_id"]}, {"0", "other"})
        with self.assertRaises(render.VideoEditError):
            render.check_pair_events([first, overlapping], timeline["width"], timeline["height"])
        self.f.f.f.write("clipkit_words.json", {"cue:0": [[0, 20, "normal keyword"]]})
        with self.assertRaises(render.VideoEditError):
            render.render_draft(self.path, "", dry=True, repair=True)


if __name__ == "__main__":
    unittest.main()
