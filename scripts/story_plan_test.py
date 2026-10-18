"""Story jobs and manual edits never accept an obsolete source or selection."""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
with tempfile.TemporaryDirectory() as folder:
    config = Path(folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import video_editor as api
import jobstore
import story_selection as selection
import video_edit
import web_ai_identity as identity
import web_ai_json as web
from fastapi import HTTPException
_REAL_THREAD = threading.Thread


class InlineThread:
    def __init__(self, target, args, **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class StoryPlan(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        config = self.root / "config.json"
        config.write_text("{}", encoding="utf-8")
        self.raw = str(identity._canonical(self.root / "talk.mp4"))
        Path(self.raw).write_bytes(b"original source")
        self.phrases = [{"start": 0, "end": 4, "text": "first point second point", "words": [
            [0, 1, "first"], [1, 2, " point"], [2, 3, " second"], [3, 4, " point"]]}]
        video_edit.transcript_file(self.raw).write_text(json.dumps(self.phrases), encoding="utf-8")
        self.jobs = jobstore.JobStore("ek04-test")
        self.jobs.clear()
        self.jobs.path = self.root / "jobs.json"
        for context in (patch.object(video_edit.kitconfig, "_CANDIDATES", [config]),
                        patch.object(api, "_story_jobs", self.jobs),
                        patch.object(jobstore, "STATE", self.root),
                        patch.object(video_edit, "transcribe_file", side_effect=self.asr),
                        patch.object(api, "Thread", InlineThread)):
            context.start()
            self.addCleanup(context.stop)

    def asr(self, file):
        video_edit.transcript_file(file).write_text(json.dumps(self.phrases), encoding="utf-8")
        return self.phrases

    def start(self, **kwargs):
        return api.story_plan(api.StoryPlanRequest(file=self.raw, **kwargs))

    def save(self, job, stories=None):
        return api.story_plan_save(api.StorySaveRequest(file=self.raw, plan_revision=job["plan_revision"],
            stories=stories or [{"title": "Whole thought", "start": 0, "end": 4}]))

    def test_manual_save_is_durable_and_cas_rejects_concurrent_edit(self):
        before = self.start()
        self.assertEqual(before["status"], "waiting")
        saved = self.save(before)
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["stories"][0]["duration_s"], 4)
        original = video_edit.stories_file(self.raw).read_bytes()
        with self.assertRaises(HTTPException) as error:
            self.save(before, [{"title": "Overwriting", "start": 0, "end": 2}])
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(video_edit.stories_file(self.raw).read_bytes(), original)
        self.jobs.clear()
        restored = api.story_plan_status(self.raw)
        self.assertEqual(restored["status"], "done")
        self.assertEqual(restored["stories"][0]["title"], "Whole thought")

    def test_new_brief_invalidates_plan_even_when_target_is_unchanged(self):
        accepted = self.save(self.start(max_minutes=1))
        previous = video_edit.stories_file(self.raw).read_bytes()
        changed = self.start(brief="Focus on the second point", max_minutes=1)
        self.assertEqual(changed["status"], "waiting")
        self.assertNotEqual(accepted["plan_revision"], changed["plan_revision"])
        self.assertNotIn("stories", changed)
        self.assertEqual(video_edit.stories_file(self.raw).read_bytes(), previous)
        self.assertTrue(changed["unverified_selection"])

    def test_source_transcript_and_target_edits_reject_stale_manual_save(self):
        for mutate in (lambda: Path(self.raw).write_bytes(b"replaced source"),
                       lambda: video_edit.transcript_file(self.raw).write_text("[]", encoding="utf-8"),
                       lambda: web.save_story_target(self.raw, 3)):
            job = self.start()
            mutate()
            with self.assertRaises(HTTPException) as error:
                self.save(job)
            self.assertEqual(error.exception.status_code, 409)

    def test_manual_invalid_word_edge_preserves_selection_and_active_packet(self):
        job = self.save(self.start())
        old = video_edit.stories_file(self.raw).read_bytes()
        active = (identity.state_dir("stories", self.raw) / "active.json").read_bytes()
        with self.assertRaises(HTTPException) as error:
            self.save(job, [{"title": "Half word", "start": 0.5, "end": 4}])
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(video_edit.stories_file(self.raw).read_bytes(), old)
        self.assertEqual((identity.state_dir("stories", self.raw) / "active.json").read_bytes(), active)

    def test_manual_edit_supersedes_pending_browser_answer(self):
        job = self.start()
        answer = {"clipkit_web_ai": 2, **identity.issue("stories", self.raw), "step": "stories",
                  "video": Path(self.raw).name, "stories": [{"title": "Old AI", "start": 0, "end": 4}]}
        self.save(job)
        with self.assertRaises(video_edit.VideoEditError):
            web.import_stories(self.raw, answer)

    def test_post_refreshes_new_browser_selection_with_unchanged_input(self):
        self.save(self.start())
        web.import_stories(self.raw, {"clipkit_web_ai": 2, **identity.issue("stories", self.raw),
            "step": "stories", "video": Path(self.raw).name,
            "stories": [{"title": "New selection", "start": 0, "end": 2}]})
        self.assertEqual(self.start()["stories"][0]["title"], "New selection")

    def test_external_brief_change_during_validation_cannot_be_rebound(self):
        job = self.save(self.start())
        old = video_edit.stories_file(self.raw).read_bytes()
        original = selection.validate
        def changed(*args, **kwargs):
            video_edit.brief_file(self.raw).write_text("A new request", encoding="utf-8")
            return original(*args, **kwargs)
        with patch.object(selection, "validate", side_effect=changed), self.assertRaises(HTTPException) as error:
            self.save(job)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(video_edit.stories_file(self.raw).read_bytes(), old)

    def test_snapshot_cannot_pair_old_stories_with_new_selection_revision(self):
        self.save(self.start())
        original = selection.read_selection
        def changed(*args, **kwargs):
            previous = original(*args, **kwargs)
            web.import_stories(self.raw, {"clipkit_web_ai": 2, **identity.issue("stories", self.raw),
                "step": "stories", "video": Path(self.raw).name,
                "stories": [{"title": "Newer saved story", "start": 0, "end": 2}]})
            return previous
        before = dict(self.jobs[self.raw])
        with patch.object(selection, "read_selection", side_effect=changed), self.assertRaises(HTTPException) as error:
            api.story_plan_status(self.raw)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.jobs[self.raw], before)
        self.assertEqual(api.story_plan_status(self.raw)["stories"][0]["title"], "Newer saved story")

    def test_legacy_unbound_selection_stays_unverified(self):
        video_edit.stories_file(self.raw).write_text(json.dumps([
            {"title": "Legacy", "start": 0, "end": 4}]), encoding="utf-8")
        job = api.story_plan_status(self.raw)
        self.assertEqual(job["status"], "waiting")
        self.assertTrue(job["unverified_selection"])
        self.assertNotIn("stories", job)

    def test_media_change_during_asr_cannot_publish_done(self):
        def replaced(file):
            Path(file).write_bytes(b"changed during ASR")
            return self.phrases
        with patch.object(video_edit, "transcribe_file", side_effect=replaced):
            job = self.start()
        self.assertEqual(job["status"], "error")
        self.assertNotIn("stories", job)

    def test_replaced_job_ignores_late_worker(self):
        old = {"status": "running"}
        current = {"status": "waiting", "marker": "new generation"}
        self.jobs[self.raw] = current
        api._run_story_plan(self.raw, old)
        self.assertEqual(self.jobs[self.raw], current)

    def test_running_job_is_not_duplicated(self):
        class PendingThread(InlineThread):
            calls = 0
            def start(self):
                PendingThread.calls += 1
        with patch.object(api, "Thread", PendingThread):
            self.start()
            self.start()
        self.assertEqual(PendingThread.calls, 1)

    def test_pending_asr_restarts_cached_plan_through_post_and_get(self):
        self.save(self.start())
        pending = video_edit.transcript_file(self.raw).with_suffix(".part.json")
        def resumed(file):
            pending.unlink()
            return self.asr(file)
        with patch.object(video_edit, "transcribe_file", side_effect=resumed) as recognize:
            for action in (self.start, lambda: api.story_plan_status(self.raw)):
                pending.write_text('{"end":0}', encoding="utf-8")
                result = action()
                self.assertEqual(result["status"], "done")
                self.assertFalse(pending.exists())
            self.assertEqual(recognize.call_count, 2)

    def test_asr_writer_rejects_parallel_brief_edit_without_losing_it(self):
        entered, release = threading.Event(), threading.Event()
        job = self.jobs[self.raw] = {"status": "running"}
        def slow(file):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test ASR release timeout")
            return self.phrases
        with patch.object(video_edit, "transcribe_file", side_effect=slow):
            worker = _REAL_THREAD(target=api._run_story_plan, args=(self.raw, job))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(HTTPException) as error:
                    self.start(brief="Keep this edit")
                self.assertEqual(error.exception.status_code, 409)
                self.assertFalse(video_edit.brief_file(self.raw).exists())
                self.assertEqual(api.story_plan_status(self.raw)["status"], "running")
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(self.jobs[self.raw]["status"], "waiting")

    def test_http_manual_contract_uses_real_routes_and_anchor_clocks(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(api.router)
        with TestClient(app) as client:
            waiting = client.post("/api/video/story-plan", json={"file": self.raw, "max_minutes": None})
            self.assertEqual(waiting.status_code, 200)
            response = client.post("/api/video/story-plan/save", json={"file": self.raw,
                "plan_revision": waiting.json()["plan_revision"], "stories": [{"title": "First thought",
                "start_anchor": {"cue_id": "source-1", "word": 1},
                "end_anchor": {"cue_id": "source-1", "word": 2}}]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["stories"][0]["end"], 2)
            self.assertEqual(client.get("/api/video/story-plan", params={"file": self.raw}).json()["status"], "done")
            invalid = client.post("/api/video/story-plan/save", json={"file": self.raw,
                "plan_revision": response.json()["plan_revision"], "stories": []})
            self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
