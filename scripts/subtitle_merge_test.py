"""Consecutive ASR fragments share a semantic subtitle and retain the actual spoken clock."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
with tempfile.TemporaryDirectory() as import_folder:
    config = Path(import_folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import capcut_edit
import render
import web_ai_json as web_ai
import web_ai_identity
import video_editor
from fastapi import FastAPI
from fastapi.testclient import TestClient


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("host_platform.capcut_running", return_value=False))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "project"
        self.folder.mkdir()
        source = self.folder / "unused.mp4"
        source.touch()
        clip = {"scale": {"x": 1, "y": 1}, "transform": {"x": 0, "y": 0}}
        self.draft = {"id": "draft", "duration": 4_000_000, "canvas_config": {"width": 1920, "height": 1080},
            "materials": {"videos": [{"id": "v", "path": str(source)}], "texts": [
                {"id": f"m{n}", "font_size": 20, "content": json.dumps({"text": text, "styles": [{"range": [0, len(text)]}]})}
                for n, text in enumerate(("in", "the", "office"), 1)]},
            "tracks": [{"type": "video", "segments": [{"id": "video", "material_id": "v", "extra_material_refs": [], "clip": clip,
                "source_timerange": {"start": 0, "duration": 4_000_000}, "target_timerange": {"start": 0, "duration": 4_000_000}}]},
                {"type": "text", "segments": [{"id": f"s{n}", "material_id": f"m{n}", "clip": copy.deepcopy(clip),
                    "target_timerange": {"start": start, "duration": duration}}
                    for n, start, duration in ((1, 0, 500_000), (2, 700_000, 500_000), (3, 1_800_000, 2_000_000))]}]}
        self.content = self.folder / "draft_content.json"
        self.content.write_text(json.dumps(self.draft))
        self.words = {"in": [[0, .5, "in"]], "the": [[0, .5, "the"]], "office": [[0, 2, "office"]]}
        (self.folder / "clipkit_words.json").write_text(json.dumps(self.words))
        self.answer = {"clipkit_web_ai": 2, "step": "clip", "project": "project", "hook": [], "motion": [], "caption": None,
                       "lines": [{"n": 1, "cue_id": "s1", "through": 3, "items": [["in the", "office"]]},
                                 {"n": 2, "cue_id": "s2"}, {"n": 3, "cue_id": "s3"}]}
        self.patch = patch.object(capcut_edit, "_log_change")
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def import_answer(self, answer):
        return web_ai.import_clip(str(self.folder), {**answer, **web_ai_identity.issue("clip", self.folder)})

    def test_import_preserves_actual_clock_style_backup_and_seal(self):
        before = self.content.read_bytes()
        result = self.import_answer(self.answer)
        self.assertIn("merge_subs", result["written"])
        rows = capcut_edit.read_draft(str(self.folder))["subtitles"]
        self.assertEqual([(s["text"], s["start"], s["end"], s["size"]) for s in rows], [("in the office", 0, 3.8, 20)])
        clock = json.loads((self.folder / "clipkit_words.json").read_text())["cue:s1"]
        self.assertEqual(clock, [[0, .5, "in"], [.7, 1.2, "the"], [1.8, 3.8, "office"]])
        marks = []
        render._pair(rows[0]["text"], clock, 0, 3.8, 1920, 1080, "Kanit", render.DEFAULT_FONT,
                     render.pair_look({}), picked=[["in the", "office"]], marks=marks)
        self.assertEqual(next(p for p in marks if p[0] == "phrase")[9], 1.8)
        self.assertEqual(self.content.with_suffix(".json.bak_merge_subs").read_bytes(), before)
        self.assertTrue((self.folder / capcut_edit.SEAL).is_file())

    def test_borrowed_subtitle_style_does_not_reference_foreign_helpers(self):
        donor = self.folder / "donor"
        donor.mkdir()
        borrowed = copy.deepcopy(self.draft)
        segment = borrowed["tracks"][1]["segments"][0]
        segment.update(extra_material_refs=["donor-animation"], keyframe_refs=["donor-keyframe"],
                       common_keyframes=[{"id": "donor-keyframe"}])
        (donor / "draft_content.json").write_text(json.dumps(borrowed))
        self.draft["tracks"][1]["segments"] = []
        self.content.write_text(json.dumps(self.draft))
        with patch.object(capcut_edit, "_drafts_root", return_value=self.folder):
            capcut_edit.set_subtitles(str(self.folder), [{"start": 0, "end": 2, "text": "new subtitle"}])
        generated = capcut_edit._load(self.folder)
        made = generated["tracks"][1]["segments"][0]
        self.assertEqual(made["extra_material_refs"], [])
        self.assertEqual(made["keyframe_refs"], [])
        self.assertEqual(made["common_keyframes"], [])
        self.assertIn(made["material_id"], capcut_edit._index(generated))

    def test_invalid_or_missing_clock_answers_change_no_files(self):
        variants = []
        for change in (lambda a: a["lines"][0].update(through=4),
                       lambda a: a["lines"][1].update(items=[["the", ""]]),
                       lambda a: a.update(cut=[2]),
                       lambda a: a.update(motion=[{"n": 2, "template": "sentence-pair"}]),
                       lambda a: a["lines"][0].update(items=[["in the", "invented"]])):
            answer = copy.deepcopy(self.answer)
            change(answer)
            variants.append(answer)
        before = {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()}
        for answer in variants:
            with self.subTest(answer=answer), self.assertRaises(render.VideoEditError):
                self.import_answer(answer)
            self.assertEqual({p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()}, before)
        (self.folder / "clipkit_words.json").write_text(json.dumps({"in": self.words["in"]}))
        before = {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()}
        with self.assertRaisesRegex(render.VideoEditError, "เวลาระดับคำ"):
            self.import_answer(self.answer)
        self.assertEqual({p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()}, before)

    def test_manual_endpoint_uses_same_merge(self):
        app = FastAPI()
        app.include_router(video_editor.router)
        with TestClient(app) as client:
            response = client.post("/api/video/draft/subs/merge", json={"path": str(self.folder), "first": 1, "last": 3})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["subtitles"], 1)
        self.assertEqual(capcut_edit.read_draft(str(self.folder))["subtitles"][0]["text"], "in the office")

    def test_cut_before_group_preserves_original_line_numbers_and_shifts_clock(self):
        answer = {**self.answer, "cut": [1], "lines": [
            {"n": 1, "cue_id": "s1"}, {"n": 2, "cue_id": "s2", "through": 3, "items": [["the", "office"]]},
            {"n": 3, "cue_id": "s3"}]}
        self.import_answer(answer)
        row = capcut_edit.read_draft(str(self.folder))["subtitles"][0]
        self.assertEqual((row["text"], row["start"], row["end"]), ("the office", 0, 3.1))
        self.assertEqual(json.loads((self.folder / "clipkit_words.json").read_text())["cue:s2"],
                         [[0, .5, "the"], [1.1, 3.1, "office"]])

    def test_plain_group_without_clock_does_not_invent_word_times(self):
        (self.folder / "clipkit_words.json").unlink()
        self.answer["lines"][0]["items"] = [["in the office", ""]]
        self.import_answer(self.answer)
        self.assertEqual(json.loads((self.folder / "clipkit_words.json").read_text())["cue:s1"], [])


if __name__ == "__main__":
    unittest.main()
