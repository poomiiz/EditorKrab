"""Corrections stay project-local; cached words must belong to the current source."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trim_timing_test as fixture
import capcut_edit as edit
import kitconfig
import spelling
import video_edit


class SpellingScope(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("host_platform.capcut_running", return_value=False))
        self.f = fixture.TrimTiming()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.folder = self.f.folder
        self.path = str(self.folder)
        self.config = self.folder.parent / "config.json"
        self.config.write_text("{}", encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(self.config)}))
        self.enterContext(patch.object(kitconfig, "_CANDIDATES", [self.config]))
        self.machine = self.folder.parent / "machine-corrections.json"
        self.machine.write_text('{"unrelated":{"right":"retained","n":1}}', encoding="utf-8")
        self.enterContext(patch.object(spelling, "FILE", self.machine))
        self.f.write("clipkit_words.json", {"cue:0": [[0, 1, "normal "], [2, 3, "keyword"]], "cue:1": [[0, 2, "next"]]})

    def change(self, **kw):
        return edit.set_line_text(self.path, "normal keyword", "normal different", "0", **kw)

    def test_edit_learns_only_project_and_preserves_raw_clock(self):
        machine = self.machine.read_bytes(), self.machine.stat().st_mtime_ns
        words = (self.folder / "clipkit_words.json").read_bytes()
        self.change()
        self.assertEqual((self.machine.read_bytes(), self.machine.stat().st_mtime_ns), machine)
        self.assertEqual((self.folder / "clipkit_words.json").read_bytes(), words)
        self.assertEqual(spelling.fix("normal keyword", spelling.load(self.folder)), "normal different")
        other = self.folder.parent / "other"
        other.mkdir()
        self.assertEqual(spelling.fix("normal keyword", spelling.load(other)), "normal keyword")

    def test_explicit_global_learning_is_opt_in(self):
        self.change(learn_global=True)
        self.assertEqual(spelling.fix("normal keyword"), "normal different")

    def test_local_overrides_retained_machine_rules(self):
        spelling.learn("keyword", "machine")
        spelling.learn("keyword", "project", self.folder)
        self.assertEqual(spelling.fix("keyword", spelling.load(self.folder)), "project")
        self.assertEqual(spelling.fix("keyword"), "machine")

    def test_retranscription_uses_only_selected_projects_corrections(self):
        self.change()
        heard = [{"start": 0, "end": 3, "text": "normal keyword",
                  "words": [[0, 1, "normal "], [2, 3, "keyword"]]}]
        with patch.object(edit, "_from_transcript", return_value=heard):
            rows = edit.transcribe_draft(self.path)["subtitles"]
        self.assertEqual(rows[0]["text"], "normal different")
        self.assertEqual(rows[0]["words"], heard[0]["words"])

    def test_bad_rules_reject_edit_before_any_write(self):
        for file in (self.machine, spelling._file(self.folder)):
            with self.subTest(file=file.name):
                before = file.read_bytes() if file.exists() else None
                file.write_text('{"word":{"right":3,"n":0}}', encoding="utf-8")
                files = {f.name: f.read_bytes() for f in self.folder.iterdir() if f.is_file()}
                with self.assertRaises(ValueError):
                    self.change()
                self.assertEqual({f.name: f.read_bytes() for f in self.folder.iterdir() if f.is_file()}, files)
                if before is None:
                    file.unlink()
                else:
                    file.write_bytes(before)

    def test_ambiguous_emphasis_rejects_before_any_write(self):
        files = {f.name: f.read_bytes() for f in self.folder.iterdir() if f.is_file()}
        with self.assertRaises(video_edit.VideoEditError):
            self.change(picks_override=[["nor", "mal different"]])
        self.assertEqual({f.name: f.read_bytes() for f in self.folder.iterdir() if f.is_file()}, files)

    def test_supplied_choices_replace_old_choices_with_original_words_untouched(self):
        clock = self.f.read("clipkit_words.json")
        self.change(picks_override=[["normal different", ""]])
        self.assertEqual(self.f.read("clipkit_punch.json")["cue:0"], [["normal different", ""]])
        self.assertEqual(self.f.read("clipkit_words.json"), clock)

    def test_undo_restores_project_rules_alongside_text(self):
        self.f.log.stop()
        with patch.object(edit, "_rows", side_effect=lambda draft: {"cards": [json.dumps(draft["materials"]["texts"])]}):
            self.change()
            initial = edit.history(self.path)[0]
            self.assertEqual(spelling.fix("keyword", spelling.load(self.folder)), "different")
            edit.restore(self.path, initial)
        self.assertEqual(edit.read_draft(self.path)["subtitles"][0]["text"], "normal keyword")
        self.assertEqual(spelling.fix("keyword", spelling.load(self.folder)), "keyword")

    def test_draft_write_failure_restores_rules(self):
        draft = (self.folder / "draft_content.json").read_bytes()
        original = edit.atomic.write_text
        failed = False
        def fail_once(path, *args, **kwargs):
            nonlocal failed
            if Path(path) == self.folder / "draft_content.json" and not failed:
                failed = True
                raise OSError("injected draft write failure")
            return original(path, *args, **kwargs)
        with patch.object(edit.atomic, "write_text", side_effect=fail_once), self.assertRaises(OSError):
            self.change()
        self.assertEqual((self.folder / "draft_content.json").read_bytes(), draft)
        self.assertFalse(spelling._file(self.folder).exists())

    def test_failed_history_rules_never_publish_a_visible_version(self):
        text = (self.folder / "draft_content.json").read_text(encoding="utf-8")
        original = edit.atomic.write_text
        def fail_rules(path, *args, **kwargs):
            if str(path).endswith(".json.gz.corrections.json"):
                raise OSError("injected history rules failure")
            return original(path, *args, **kwargs)
        with patch.object(edit.atomic, "write_text", side_effect=fail_rules), self.assertRaises(OSError):
            edit._keep_version(self.folder, text, "probe")
        self.assertEqual(edit.history(self.path), [])
        self.assertEqual(list((self.folder / edit.HISTORY).iterdir()), [])

    def test_failed_atomic_history_publish_removes_rules_and_temporary_file(self):
        text = (self.folder / "draft_content.json").read_text(encoding="utf-8")
        original = edit.atomic.os.replace
        def fail_gzip(source, destination):
            if str(destination).endswith(".json.gz"):
                raise OSError("injected gzip publish failure")
            return original(source, destination)
        with patch.object(edit.atomic.os, "replace", side_effect=fail_gzip), self.assertRaises(OSError):
            edit._keep_version(self.folder, text, "probe")
        self.assertEqual(edit.history(self.path), [])
        self.assertEqual(list((self.folder / edit.HISTORY).iterdir()), [])

    def _assert_failed_save_restores_metadata(self, failure_name):
        import web_ai_transaction
        self.f.log.stop()
        self.enterContext(patch.object(edit, "_rows", side_effect=lambda draft:
            {"cards": [json.dumps(draft["materials"]["texts"])]}))
        self.f.write("clipkit_corrections.json", {"held": {"right": "kept", "n": 2}})
        self.f.write("draft_meta_info.json", {"tm_duration": 8_000_000, "tm_draft_modified": 1})
        timeline = self.folder / "Timelines" / "main"
        timeline.mkdir(parents=True)
        (timeline.parent / "project.json").write_text('{"main_timeline_id":"main"}', encoding="utf-8")
        original_draft = (self.folder / "draft_content.json").read_bytes()
        (timeline / "draft_content.json").write_bytes(original_draft)
        edit._keep_version(self.folder, original_draft.decode("utf-8"), "start")
        before = {name: file.read_bytes() for name, file in web_ai_transaction._files(self.folder).items()}
        machine = self.machine.read_bytes()
        original = edit.atomic.write_text
        failed = False
        def fail_once(path, *args, **kwargs):
            nonlocal failed
            if Path(path) == self.folder / failure_name and not failed:
                failed = True
                raise OSError("injected metadata write failure")
            return original(path, *args, **kwargs)
        with patch.object(edit.atomic, "write_text", side_effect=fail_once), self.assertRaises(OSError):
            self.change()
        self.assertTrue(failed)
        self.assertEqual({name: file.read_bytes() for name, file in web_ai_transaction._files(self.folder).items()}, before)
        self.assertEqual(edit.read_draft(self.path)["subtitles"][0]["text"], "normal keyword")
        self.assertEqual(self.machine.read_bytes(), machine)

    def test_sidecar_failure_restores_draft_rules_history_and_native_timeline(self):
        self._assert_failed_save_restores_metadata("clipkit_punch.json")

    def test_meta_failure_restores_draft_rules_history_and_native_timeline(self):
        self._assert_failed_save_restores_metadata("draft_meta_info.json")

    def _assert_retranscription_matches_cache_and_fresh(self, machine_rules, local_rules, heard, displayed, expected):
        self.machine.write_text(json.dumps(machine_rules), encoding="utf-8")
        self.f.write("clipkit_corrections.json", local_rules)
        words = [[0, 1, heard]]
        phrases = [{"start": 0, "end": 1, "text": displayed, "words": words}]
        tf = video_edit.transcript_file(str(self.f.source))
        tf.write_text(json.dumps(phrases), encoding="utf-8")
        tf.with_suffix(".meta.json").write_text(json.dumps({"schema_version": 2,
            "sha256": hashlib.sha256(self.f.source.read_bytes()).hexdigest()}), encoding="utf-8")
        machine = self.machine.read_bytes()
        for cached in (True, False):
            with self.subTest(cached=cached):
                with patch.object(video_edit, "transcribe", return_value=phrases) as fresh:
                    if cached:
                        rows = edit.transcribe_draft(self.path)["subtitles"]
                        fresh.assert_not_called()
                    else:
                        with patch.object(edit, "_from_transcript", return_value=None):
                            rows = edit.transcribe_draft(self.path)["subtitles"]
                        fresh.assert_called_once()
                self.assertEqual(rows[0]["text"], expected)
                self.assertEqual(rows[0]["words"], words)
                self.assertEqual((rows[0]["start"], rows[0]["end"]), (0, 1))
                self.assertEqual(self.machine.read_bytes(), machine)
                self.assertEqual(json.loads(tf.read_text(encoding="utf-8")), phrases)

    def test_local_correction_chain_applies_once_for_cache_and_fresh_asr(self):
        self._assert_retranscription_matches_cache_and_fresh({},
            {"cat": {"right": "dog", "n": 1}, "dog": {"right": "fish", "n": 1}},
            "cat", "cat", "dog")

    def test_project_override_uses_heard_words_for_cache_and_fresh_asr(self):
        self._assert_retranscription_matches_cache_and_fresh(
            {"cat": {"right": "dog", "n": 1}}, {"cat": {"right": "bird", "n": 1}},
            "cat", "dog", "bird")


class TranscriptProvenance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = Path(self.temp.name) / "source.mp4"
        self.raw.write_bytes(b"first")
        self.tf = video_edit.transcript_file(str(self.raw))
        self.meta = self.tf.with_suffix(".meta.json")
        self.tf.write_text(json.dumps([{"start": 0, "end": 3, "text": "normal keyword",
            "words": [[0, 1, "normal "], [2, 3, "keyword"]]}]), encoding="utf-8")
        self.meta.write_text(json.dumps({"schema_version": 2,
            "sha256": hashlib.sha256(self.raw.read_bytes()).hexdigest()}), encoding="utf-8")
        self.enterContext(patch.object(spelling, "FILE", Path(self.temp.name) / "machine.json"))

    def read(self):
        return edit._from_transcript(str(self.raw), 0, 3)

    def test_verified_current_source_reuses_exact_words(self):
        self.assertEqual(self.read()[0]["words"], [[0, 1, "normal "], [2, 3, "keyword"]])

    def test_same_size_changed_source_rejects_even_when_transcript_is_newer(self):
        self.read()
        self.raw.write_bytes(b"other")
        os.utime(self.tf, (self.raw.stat().st_atime + 5, self.raw.stat().st_mtime + 5))
        self.assertIsNone(self.read())

    def test_pending_or_unproven_cache_is_not_reused(self):
        pending = self.tf.with_suffix(".part.json")
        pending.write_text("{}", encoding="utf-8")
        self.assertIsNone(self.read())
        pending.unlink()
        self.meta.unlink()
        self.assertIsNone(self.read())

    def test_corrupt_metadata_fails_clearly(self):
        self.meta.write_text("{", encoding="utf-8")
        with self.assertRaises(video_edit.VideoEditError):
            self.read()

    def test_source_change_during_read_is_refused(self):
        original = video_edit.slice_transcript
        def changed(*args):
            result = original(*args)
            self.raw.write_bytes(b"other")
            return result
        with patch.object(video_edit, "slice_transcript", side_effect=changed), self.assertRaises(video_edit.VideoEditError):
            self.read()


if __name__ == "__main__":
    unittest.main()
