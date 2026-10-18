"""Required captions block final delivery, while repair previews preserve source wording and files."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import motion_cut_test as fixture
import capcut_build
import draft_quality
import hf_build
import kit_settings
from fastapi import HTTPException

render, api = fixture.render, fixture.video_editor


class CaptionQualityTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.MotionCutTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.folder, self.path = self.f.folder, self.f.path
        self.f.f.write("clipkit_style.json", {"anim": "pair", "preset": "with-caption", "music": "", "sfx": False})

    def test_missing_caption_is_reported_by_cue_in_preview_at_all_ratios(self):
        draft = self.f.f.read("draft_content.json")
        for width, height in ((1080, 1920), (1920, 1080), (1080, 1080)):
            with self.subTest(canvas=(width, height)):
                draft["canvas_config"].update(width=width, height=height)
                self.f.f.write("draft_content.json", draft)
                before = (self.folder / "draft_content.json").read_bytes()
                timeline = render.render_draft(self.path, "", dry=True)
                issues = timeline["quality"]["caption_errors"]
                self.assertEqual([i["cue_id"] for i in issues], ["0", "1"])
                self.assertEqual({i["language"] for i in issues}, {"en"})
                self.assertTrue(all(i["severity"] == "error" for i in issues))
                self.assertNotIn("caption", {e["role"] for e in timeline["subtitle_events"]})
                self.assertEqual((self.folder / "draft_content.json").read_bytes(), before)

    def test_blank_invalid_and_wrong_occurrence_captions_do_not_satisfy_requirement(self):
        for invalid in (None, "", " \t\n", 12, ["translation"]):
            with self.subTest(caption=invalid):
                self.f.f.write("clipkit_caption.json", {"cue:0": invalid, "cue:1": "next translation"})
                timeline = render.render_draft(self.path, "", dry=True)
                self.assertEqual([i["cue_id"] for i in timeline["quality"]["caption_errors"]], ["0"])
                with self.assertRaisesRegex(render.VideoEditError, "required en caption.*0"):
                    render.render_draft(self.path, "", dry=True, final=True)
        phrases = [{"cue_id": "a", "line": "same", "start": 0, "end": 1},
                   {"cue_id": "a", "line": "same", "start": 1, "end": 2},
                   {"cue_id": "b", "line": "same", "start": 3, "end": 4}]
        look = {"caption": (8,), "caption_lang": "en"}
        issues = draft_quality.caption_errors(phrases, look, {"cue:b": "second occurrence"})
        self.assertEqual([(i["cue_id"], i["start"], i["end"]) for i in issues], [("a", 0, 2)])
        self.assertEqual(draft_quality.caption_errors(phrases, {**look, "caption": None}, {}), [])
        self.assertEqual(draft_quality.caption_errors(phrases, {**look, "caption_lang": "th"}, {}), [])

    def test_final_adapters_preserve_previous_outputs_before_encoding(self):
        exports = self.folder.parent / "exports"
        exports.mkdir()
        target = exports / (self.folder.name + ".mp4")
        target.write_bytes(b"previous accepted output")
        page = self.folder / "clipkit_hf" / "index.html"
        page.parent.mkdir()
        page.write_text("previous preview", encoding="utf-8")
        before = {p.relative_to(self.folder): p.read_bytes() for p in self.folder.rglob("*") if p.is_file()}
        actions = {
            "cpu": lambda: render.render_draft(self.path, str(exports)),
            "final dry": lambda: render.render_draft(self.path, "", dry=True, final=True),
            "hyperframes final": lambda: hf_build._build(self.path, final=True),
            "native final": lambda: capcut_build.build(self.path),
        }
        with patch.object(render.subprocess, "run") as process, \
                patch.object(kit_settings, "_read_config", return_value={"output_dir": str(self.folder.parent)}), \
                patch("shutil.which", return_value="npx"):
            for name, action in actions.items():
                with self.subTest(adapter=name), self.assertRaisesRegex(render.VideoEditError, "required en caption.*0, 1"):
                    action()
            with self.assertRaises(HTTPException) as error:
                api.draft_hf_export(api.ExportRequest(path=self.path))
            self.assertEqual(error.exception.status_code, 400)
            self.assertIn("required en caption", error.exception.detail)
            process.assert_not_called()
        self.assertEqual(target.read_bytes(), b"previous accepted output")
        self.assertEqual(before, {p.relative_to(self.folder): p.read_bytes() for p in self.folder.rglob("*") if p.is_file()})
        self.assertFalse(list(self.folder.parent.glob(self.folder.name + " \u00b7 CapCut*")))
        self.assertEqual(list(exports.iterdir()), [target])

    def test_repaired_translations_are_used_without_modifying_source_or_word_clock(self):
        source = self.folder / "draft_content.json"
        words = self.folder / "clipkit_words.json"
        before = (source.read_bytes(), words.read_bytes())
        self.f.f.write("clipkit_caption.json", {"cue:0": "first translation", "cue:1": "second translation"})
        timeline = render.render_draft(self.path, "", dry=True, final=True)
        self.assertEqual(timeline["quality"]["caption_errors"], [])
        self.assertEqual([e["text"] for e in timeline["subtitle_events"] if e["role"] == "caption"],
                         ["first translation", "second translation"])
        self.assertEqual((source.read_bytes(), words.read_bytes()), before)


if __name__ == "__main__":
    unittest.main()
