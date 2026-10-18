"""Project edits reserve their complete read/write operation across callers and processes."""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import inspect
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import trim_timing_test as fixture
import capcut_edit
import hf_build
import jobstore
import project_lock
import video_editor
import web_ai_transaction


class ProjectLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "project"
        self.path.mkdir()
        (self.path / "draft_content.json").write_text("{}", encoding="utf-8")
        self.state = patch.object(jobstore, "STATE", self.root / "state")
        self.state.start()
        self.addCleanup(self.state.stop)
        self.recover = Mock()
        recovery = patch.object(web_ai_transaction, "recover", self.recover)
        recovery.start()
        self.addCleanup(recovery.stop)

    def test_nested_alias_uses_one_reservation_and_one_recovery(self):
        with project_lock.writer(self.path):
            self.assertTrue(project_lock.busy())
            with project_lock.writer(self.path / ".." / self.path.name):
                self.assertTrue(project_lock.busy())
                self.recover.assert_called_once_with("clip", os.path.normcase(str(self.path.resolve())))
        self.assertFalse(project_lock.busy())

    def test_explicit_recovery_and_failed_recovery_release_reservation(self):
        with project_lock.writer(self.path, recover=False):
            self.recover.assert_not_called()
        self.recover.side_effect = RuntimeError("recovery conflict")
        with self.assertRaisesRegex(RuntimeError, "recovery conflict"):
            with project_lock.writer(self.path):
                self.fail("must recover before an edit can start")
        self.assertFalse(project_lock.busy())
        with project_lock.writer(self.path, recover=False):
            pass

    def test_another_thread_cannot_read_before_the_current_writer_finishes(self):
        operation = Mock()
        @project_lock.edit
        def edit(path: str):
            operation()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with project_lock.writer(self.path):
                with self.assertRaises(hf_build.BuildBusy):
                    pool.submit(edit, str(self.path)).result(timeout=5)
                operation.assert_not_called()
                pool.submit(edit, str(self.root / "other")).result(timeout=5)
                self.assertTrue(project_lock.busy())
            pool.submit(edit, path=str(self.path)).result(timeout=5)
        self.assertFalse(project_lock.busy())
        self.assertEqual(operation.call_count, 2)

    def test_writer_and_hyperframes_build_share_the_same_reservation(self):
        with patch.object(hf_build, "_build") as build:
            with project_lock.writer(self.path), self.assertRaises(hf_build.BuildBusy):
                hf_build.build(str(self.path))
            build.assert_not_called()
        with hf_build.build_lock(str(self.path)), self.assertRaises(hf_build.BuildBusy):
            with project_lock.writer(self.path):
                self.fail("export holds the same reservation")

    def test_another_process_uses_the_same_canonical_reservation(self):
        config = self.root / "config.json"
        config.write_text("{}", encoding="utf-8")
        code = ("import sys;from pathlib import Path;"
                f"sys.path.insert(0,{str(ROOT / 'app')!r});import project_lock,hf_build,jobstore;"
                f"jobstore.STATE=Path({str(self.root / 'state')!r});"
                f"\ntry:\n with project_lock.writer({str(self.path)!r},recover=False): pass"
                "\nexcept hf_build.BuildBusy: sys.exit(23)")
        with project_lock.writer(self.path):
            child = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                   env={**os.environ, "CLIP_KIT_CONFIG": str(config)}, timeout=30)
        self.assertEqual(child.returncode, 23, child.stderr)

    def test_cli_mutator_and_grade_destination_are_reserved_before_draft_read(self):
        with hf_build.build_lock(str(self.path)), patch.object(capcut_edit, "_load") as load:
            with self.assertRaises(hf_build.BuildBusy):
                capcut_edit.set_line_time(str(self.path), "line", 1, 2)
            with self.assertRaises(hf_build.BuildBusy):
                capcut_edit.copy_grade("source", target_path=str(self.path))
            load.assert_not_called()

    def test_request_signature_and_api_busy_response_are_preserved(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(video_editor.router)
        signature = inspect.signature(video_editor.draft_caption)
        self.assertIs(signature.parameters["req"].annotation, video_editor.CaptionEdit)
        with TestClient(app) as client, hf_build.build_lock(str(self.path)), \
                patch.object(capcut_edit, "read_draft") as read:
            response = client.post("/api/video/draft/caption", json={"path": str(self.path),
                                    "line": "line", "cue_id": "1", "text": "caption"})
            self.assertEqual(response.status_code, 409, response.text)
            grade = client.post("/api/video/draft/grade", json={"source": "source", "target": str(self.path)})
            self.assertEqual(grade.status_code, 409, grade.text)
            read.assert_not_called()

    def test_nested_api_cut_and_cli_trim_recover_once_and_keep_word_clock(self):
        f = fixture.TrimTiming()
        f.setUp()
        self.addCleanup(f.doCleanups)
        result = video_editor.draft_cut(video_editor.CutRange(path=str(f.folder), start=3, end=3.5))
        self.assertEqual(result["length"], 7.5)
        self.recover.assert_called_once()
        self.assertEqual(f.read("clipkit_words.json")["cue:0"][1][:2], [1.5, 2.5])
        self.assertFalse(project_lock.busy())

    def test_actual_app_lifecycle_sees_the_outer_writer(self):
        source = ast.parse((ROOT / "app" / "app.py").read_text(encoding="utf-8"))
        function = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "jobs_running")
        scope = {"project_lock": project_lock, "kit_settings": SimpleNamespace(_jobs={}),
                 "video_editor": SimpleNamespace(_transcribe_jobs={}, _story_jobs={})}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "app/app.py", "exec"), scope)
        self.assertFalse(scope["jobs_running"]())
        with project_lock.writer(self.path):
            self.assertTrue(scope["jobs_running"]())
        self.assertFalse(scope["jobs_running"]())

    def test_watcher_retries_busy_project_without_recording_it_as_seen(self):
        class Stop(Exception):
            pass
        attempts = Mock(side_effect=[hf_build.BuildBusy("busy"), None])
        sleeps = Mock(side_effect=[None, Stop()])
        with patch.object(capcut_edit, "record_outside", attempts), \
                patch.object(capcut_edit.time, "time", return_value=10**12), \
                patch.object(capcut_edit.time, "sleep", sleeps), self.assertRaises(Stop):
            capcut_edit.watch(str(self.root), every=0)
        self.assertEqual(attempts.call_count, 2)


if __name__ == "__main__":
    unittest.main()
