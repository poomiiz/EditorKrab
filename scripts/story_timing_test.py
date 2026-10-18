"""Semantic story edges retain complete words across ASR transport rows."""
import json
import hashlib
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
        import capcut_edit
import video_edit
import web_ai_json as web
import web_ai_identity


class StoryTiming(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = str(Path(self.temp.name) / "talk.mp4")
        Path(self.raw).write_bytes(b"source")
        self.phrases = [{"start": 0, "end": 6, "text": "พูดดีครับอันต่อไป", "words": [
            [0, 1, "พูด"], [1, 2, "ดี"], [2, 2.1, "คร"], [2.1, 2.2, "ั"], [2.2, 2.4, "บ"],
            [4, 5, "อัน"], [5, 6, "ต่อไป"]]}]
        video_edit.transcript_file(self.raw).write_text(json.dumps(self.phrases), encoding="utf-8")
        video_edit.transcript_file(self.raw).with_suffix(".meta.json").write_text(json.dumps({
            "schema_version": 2, "sha256": hashlib.sha256(Path(self.raw).read_bytes()).hexdigest()}), encoding="utf-8")

    def answer(self, stories):
        return {"clipkit_web_ai": 2, **web_ai_identity.issue("stories", self.raw),
                "step": "stories", "video": "talk.mp4", "stories": stories}

    def test_word_end_inside_transport_row_survives_import_and_consumers(self):
        web.import_stories(self.raw, self.answer([{"title": "complete", "start": 0, "end": 2.4}]))
        for split in (False, True):
            story = video_edit.stories_from_agent(self.raw, self.phrases, split_long=split)[0]
            self.assertEqual((story["start"], story["end"]), (0, 2.4))
        phrases = capcut_edit._from_transcript(self.raw, 0, 2.4)
        self.assertIsNotNone(phrases)
        self.assertEqual(phrases[0]["text"], "พูดดีครับ")
        self.assertEqual(phrases[0]["words"][-1], [2.2, 2.4, "บ"])

    def test_word_start_inside_row_preserves_relative_clock(self):
        web.import_stories(self.raw, self.answer([{"title": "next", "start": 4, "end": 6}]))
        story = video_edit.stories_from_agent(self.raw, self.phrases, split_long=False)[0]
        self.assertEqual((story["start"], story["end"]), (4, 6))
        self.assertEqual(capcut_edit._from_transcript(self.raw, 4, 6)[0]["words"],
                         [[0, 1, "อัน"], [1, 2, "ต่อไป"]])

    def test_invalid_answers_leave_existing_selection_unchanged(self):
        good = {"title": "all", "start": 0, "end": 6}
        web.import_stories(self.raw, self.answer([good]))
        before = video_edit.stories_file(self.raw).read_bytes()
        for items in ([good, good], [{"title": "late", "start": 4, "end": 6}, {"title": "early", "start": 0, "end": 2}],
                      [{"title": "too far", "start": 0, "end": 60}],
                      [{"title": "infinite", "start": 0, "end": float("inf")}],
                      [{"title": "syllable cut", "start": 0, "end": 2.2}],
                      [{"title": "inside token", "start": .2, "end": 2}]):
            with self.subTest(items=items):
                with self.assertRaises(video_edit.VideoEditError):
                    web.import_stories(self.raw, self.answer(items))
                self.assertEqual(video_edit.stories_file(self.raw).read_bytes(), before)

    def test_persisted_local_agent_selection_uses_same_validation(self):
        video_edit.stories_file(self.raw).write_text(json.dumps([{"title": "bad", "start": 0, "end": 2.2}]))
        with self.assertRaises(video_edit.VideoEditError):
            video_edit.stories_from_agent(self.raw, self.phrases, split_long=False)


if __name__ == "__main__":
    unittest.main()
