"""Approximate story targets survive resume and never hard-split an AI selection."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
with tempfile.TemporaryDirectory() as import_folder:
    config = Path(import_folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import web_ai_json as web
import video_edit
import video_editor
import run_clip
import jobstore
import kitconfig
import web_ai_identity
from pydantic import ValidationError


class StoryTarget(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        config = Path(folder) / "config.json"
        config.write_text("{}", encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}))
        self.enterContext(patch.object(kitconfig, "_CANDIDATES", [config]))

    def test_preference_prompt_resume_and_complete_thought(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = str(Path(folder) / "talk.mp4")
            Path(raw).write_bytes(b"media")
            phrases = [{"start": n, "end": n + 10, "text": f"line {n}"} for n in range(0, 210, 10)]
            video_edit.transcript_file(raw).write_text(json.dumps(phrases), encoding="utf-8")
            self.assertTrue(web.save_story_target(raw, 3))
            packet = web.export_stories(raw).read_text(encoding="utf-8")
            self.assertLess(packet.index("ราว 3 นาที"), packet.index("line 0"))
            self.assertIn('"max_minutes": 3', packet)
            self.assertEqual(video_editor.brief(raw)["max_minutes"], 3)
            web.import_stories(raw, {"clipkit_web_ai": 2, **web_ai_identity.issue("stories", raw),
                                    "step": "stories", "video": "talk.mp4",
                                    "stories": [{"title": "Complete thought", "start": 0, "end": 210}]})
            with patch.object(video_edit, "transcribe_file", return_value=phrases), \
                 patch.object(video_edit, "_split_long", side_effect=AssertionError("hard split")):
                chosen = run_clip.stories(raw, None)
                self.assertEqual((len(chosen), chosen[0]["end"]), (1, 210))
            self.assertFalse(web.save_story_target(raw, 3))
            self.assertTrue(video_edit.stories_file(raw).is_file())
            self.assertTrue(web.save_story_target(raw, 2))
            self.assertFalse(video_edit.stories_file(raw).exists())
            self.assertEqual(len(list(Path(folder).glob("*.before-target-*"))), 2)
            self.assertEqual(web.story_target(raw), 2)
            self.assertTrue(web.save_story_target(raw, None))
            self.assertIsNone(web.story_target(raw))

    def test_clip_packet_inherits_source_preference(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = str(Path(folder) / "raw.mp4")
            Path(raw).write_bytes(b"media")
            web.save_story_target(raw, 3)
            project = Path(folder) / "project"
            project.mkdir()
            (project / "clipkit.json").write_text(json.dumps({"raw": raw}), encoding="utf-8")
            draft = {"id": "draft", "duration": 200_000_000, "canvas_config": {"width": 1080, "height": 1920}, "materials": {
                "videos": [{"id": "video", "path": raw}], "texts": [{"id": "text", "font_size": 15,
                    "content": json.dumps({"text": "spoken", "styles": []})}]}, "tracks": [
                {"type": "video", "segments": [{"id": "video", "material_id": "video",
                    "source_timerange": {"start": 0, "duration": 200_000_000},
                    "target_timerange": {"start": 0, "duration": 200_000_000}}]},
                {"type": "text", "segments": [{"id": "spoken-1", "material_id": "text", "clip": {"scale": {"x": 1, "y": 1}, "transform": {"x": 0, "y": 0}},
                    "target_timerange": {"start": 0, "duration": 200_000_000}}]}]}
            (project / "draft_content.json").write_text(json.dumps(draft), encoding="utf-8")
            with patch.object(web, "_motion_menu", return_value=[]):
                packet = web.export_clip(str(project)).read_text(encoding="utf-8")
                self.assertIn("ราว 3 นาที", packet)
                self.assertIn('"max_minutes": 3', packet)

    def test_positive_finite_at_every_entry(self):
        for value in (0, -1, float("inf"), float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(video_edit.VideoEditError):
                    web.save_story_target("unused", value)
                with self.assertRaises(ValidationError):
                    video_editor.StoryPlanRequest(file="unused", max_minutes=value)
                with self.assertRaises(ValidationError):
                    video_editor.WebAiRequest(step="stories", target="unused", max_minutes=value)
        self.assertIsNone(run_clip._parser().parse_args(["raw.mp4"]).max)
        self.assertEqual(run_clip._parser().parse_args(["raw.mp4", "--max", "180"]).max, 180)

    def test_no_ai_pause_fallback_uses_target_without_global_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = str(Path(folder) / "talk.mp4")
            Path(raw).write_bytes(b"media")
            phrases = [{"start": n, "end": n + 8, "text": f"line {n}"} for n in range(0, 400, 10)]
            video_edit.transcript_file(raw).write_text(json.dumps(phrases), encoding="utf-8")
            original = video_edit.STORY_MAX_S, video_edit.STORY_AIM_S
            with patch.object(video_edit, "transcribe_file", return_value=phrases):
                stories = run_clip.stories(raw, 180, no_ai=True)
            self.assertEqual(len(stories), 2)
            self.assertTrue(155 <= stories[0]["end"] <= 205, stories)
            self.assertEqual((video_edit.STORY_MAX_S, video_edit.STORY_AIM_S), original)

    def test_changed_target_invalidates_pending_answer_without_losing_history(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = str(Path(folder) / "talk.mp4")
            web.save_story_target(raw, 3)
            answer = web.answer_file("stories", raw)
            answer.write_text("{}", encoding="utf-8")
            web.save_story_target(raw, 1)
            self.assertFalse(answer.exists())
            self.assertEqual(len(list(Path(folder).glob("*.answer.json.before-target-*"))), 1)

    def test_natural_length_and_cached_ui_job_replan(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(jobstore, "STATE", Path(folder)):
            raw = str(Path(folder) / "talk.mp4")
            Path(raw).write_bytes(b"media")
            phrases = [{"start": 0, "end": 400, "text": "complete thought"}]
            transcript = video_edit.transcript_file(raw)
            transcript.write_text(json.dumps(phrases), encoding="utf-8")
            jobs = jobstore.JobStore("target-test", failed="error")
            jobs[raw] = {"status": "done", "max_minutes": 3, "transcript": str(transcript)}
            web.save_story_target(raw, None)
            with patch.object(video_editor, "_story_jobs", jobs), \
                 patch.object(video_editor, "Thread", side_effect=lambda target, args, **kw:
                              SimpleNamespace(start=lambda: target(*args))), \
                 patch.object(video_edit, "transcribe_file", return_value=phrases), \
                 patch.object(video_edit, "_split_long", side_effect=AssertionError("natural length must not split")):
                self.assertEqual(video_editor.story_plan_status(raw)["status"], "waiting")
                web.import_stories(raw, {"clipkit_web_ai": 2, **web_ai_identity.issue("stories", raw),
                                        "step": "stories", "video": "talk.mp4",
                                        "stories": [{"title": "Natural", "start": 0, "end": 400}]})
                result = video_editor.story_plan_status(raw)
                self.assertEqual(result["status"], "done")
                self.assertEqual(result["stories"][0]["end"], 400)
            packet = web.export_stories(raw).read_text(encoding="utf-8")
            self.assertIn("ตามเนื้อหา ผู้ใช้เลือกแล้ว ไม่ต้องถามเพิ่ม", packet)


if __name__ == "__main__":
    unittest.main()
