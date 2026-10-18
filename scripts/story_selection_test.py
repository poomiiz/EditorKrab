"""Word-anchored stories, immutable input binding and two-file rollback use one contract."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
with tempfile.TemporaryDirectory() as temporary:
    config = Path(temporary) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import web_ai_json as web
import story_selection as selection
import video_edit
import web_ai_identity as identity
import web_ai_transaction as transaction


class StorySelection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = Path(self.temp.name) / "config.json"
        config.write_text("{}", encoding="utf-8")
        candidates = patch.object(video_edit.kitconfig, "_CANDIDATES", [config])
        candidates.start()
        self.addCleanup(candidates.stop)
        self.raw = str(Path(self.temp.name) / "talk.mp4")
        Path(self.raw).write_bytes(b"source media")
        self.phrases = [{"start": 0, "end": 6, "text": "พูดดีครับอันต่อไป", "words": [
            [0, 1, "พูด"], [1, 2, "ดี"], [2, 2.1, "คร"], [2.1, 2.2, "ั"], [2.2, 2.4, "บ"],
            [4, 5, "อัน"], [5, 6, "ต่อไป"]]}]
        video_edit.transcript_file(self.raw).write_text(json.dumps(self.phrases), encoding="utf-8")
        self.story = {"title": "Complete point", "reason": "Opening and ending are complete",
                      "duration_reason": "One complete thought", "start_anchor": {"cue_id": "source-1", "word": 1},
                      "end_anchor": {"cue_id": "source-1", "word": 3}}

    def answer(self, stories=None):
        return {"clipkit_web_ai": 2, **identity.issue("stories", self.raw), "step": "stories", "video": "talk.mp4",
                "stories": stories if stories is not None else [self.story]}

    def test_packet_has_exact_complete_word_clock_and_no_local_path(self):
        packet = web.export_stories(self.raw).read_text(encoding="utf-8")
        data = json.loads(packet.rsplit("```json\n", 1)[1].split("\n```", 1)[0])
        row = data["lines"][0]
        self.assertEqual(row["cue_id"], "source-1")
        self.assertEqual(next(w for w in row["words"] if w["n"] == 3),
                         {"n": 3, "end_n": 5, "start": 2, "end": 2.4, "text": "ครับ"})
        self.assertEqual([w["n"] for w in row["words"]], [1, 2, 3, 6, 7])
        self.assertNotIn(self.temp.name, packet)

    def test_anchors_resolve_actual_word_edges_and_preserve_reasons(self):
        result = web.import_stories(self.raw, self.answer())[0]
        self.assertEqual((result["start"], result["end"]), (0, 2.4))
        self.assertEqual(result["reason"], self.story["reason"])
        self.assertEqual(result["duration_reason"], self.story["duration_reason"])
        self.assertEqual(result["end_anchor"], self.story["end_anchor"])
        self.assertEqual(selection.read_selection(self.raw, self.phrases), [result])

    def test_contradictory_or_partial_anchors_refused_before_files(self):
        for extra in ({"end": 2.2}, {"end_anchor": {"cue_id": "source-1", "word": 4}},
                      {"end_anchor": {"cue_id": "source-9"}}, {"start": float("inf")}):
            with self.subTest(extra=extra), self.assertRaises(video_edit.VideoEditError):
                web.import_stories(self.raw, self.answer([{**self.story, **extra}]))
        self.assertFalse(video_edit.stories_file(self.raw).exists())
        self.assertFalse(selection.selection_file(self.raw).exists())

    def test_two_stories_can_share_one_asr_row_without_splitting_a_word(self):
        second = {"title": "Next point", "reason": "Another complete point",
                  "start_anchor": {"cue_id": "source-1", "word": 6},
                  "end_anchor": {"cue_id": "source-1", "word": 7}}
        result = web.import_stories(self.raw, self.answer([self.story, second]))
        self.assertEqual([(s["start"], s["end"]) for s in result], [(0, 2.4), (4, 6)])
        self.assertEqual([video_edit.slice_transcript(self.phrases, s["start"], s["end"])[0]["text"]
                          for s in result], ["พูดดีครับ", "อันต่อไป"])

    def test_numeric_silence_guess_rejected_and_no_word_row_edges_supported(self):
        with self.assertRaises(video_edit.VideoEditError):
            selection.validate([{"title": "guess", "start": 0, "end": 3}], self.phrases)
        row = [{"start": 1, "end": 5, "text": "Complete thought"}]
        story = {"title": "row", "start_anchor": {"cue_id": "source-1"}, "end_anchor": {"cue_id": "source-1"}}
        normalized = selection.validate([story], row)[0]
        self.assertEqual((normalized["start"], normalized["end"]), (1, 5))

    def test_natural_and_all_requested_durations_roundtrip_without_hard_split(self):
        phrases = [{"start": 0, "end": 360, "text": "One complete explanation"}]
        video_edit.transcript_file(self.raw).write_text(json.dumps(phrases), encoding="utf-8")
        for target in (None, .5, 1, 2, 3, 5):
            with self.subTest(target=target):
                web.save_story_target(self.raw, target)
                accepted = web.import_stories(self.raw, self.answer([{"title": "Complete", "start": 0, "end": 360,
                                                                      "reason": "Complete", "duration_reason": "Complete"}]))[0]
                self.assertEqual(accepted["duration_s"], 360)
                self.assertEqual(accepted["target_s"], None if target is None else target * 60)
                self.assertEqual(accepted["review_required"], target is not None and abs(360 - target * 60) > max(15, target * 12))
                with patch.object(video_edit, "_split_long", side_effect=AssertionError("accepted plan split")):
                    self.assertEqual(video_edit.stories_from_agent(self.raw, phrases)[0]["end"], 360)

    def test_deviation_threshold_is_review_information(self):
        phrases = [{"start": 0, "end": 70, "text": "Shorter"}, {"start": 100, "end": 180, "text": "Longer"}]
        out = selection.validate([{"title": "near", "start": 0, "end": 70},
                                  {"title": "review", "start": 100, "end": 180}], phrases, max_minutes=1)
        self.assertFalse(out[0]["review_required"])
        self.assertTrue(out[1]["review_required"])
        self.assertEqual(out[1]["delta_s"], 20)

    def test_revision_is_readonly_cached_and_all_input_changes_invalidate(self):
        initial = selection.source_revision(self.raw)
        with patch.object(identity, "_file", wraps=identity._file) as hashed:
            self.assertEqual(selection.source_revision(self.raw), initial)
            self.assertFalse(any(call.args[0] == identity._canonical(self.raw) for call in hashed.call_args_list))
        self.assertFalse(identity.state_dir("stories", self.raw).exists())
        for suffix, content in ((".brief.txt", "new brief"), (".transcript.meta.json", '{"model":"new"}'),
                                (".story-preferences.json", '{"max_minutes":2}')):
            with self.subTest(suffix=suffix):
                file = Path(self.raw + suffix)
                file.write_text(content, encoding="utf-8")
                self.assertNotEqual(selection.source_revision(self.raw), initial)
                file.unlink()
        original = identity._file
        with patch.object(identity, "_file", side_effect=lambda path, *a, **kw: "f" * 64 if path.name == "story_selection.py" else original(path, *a, **kw)):
            self.assertNotEqual(selection.source_revision(self.raw), initial)

    def test_pre_asr_revision_allows_only_missing_transcript(self):
        video_edit.transcript_file(self.raw).unlink()
        before = selection.source_revision(self.raw, require_transcript=False)
        with self.assertRaises(video_edit.VideoEditError):
            selection.source_revision(self.raw)
        video_edit.transcript_file(self.raw).write_text(json.dumps(self.phrases), encoding="utf-8")
        self.assertNotEqual(before, selection.source_revision(self.raw))
        Path(self.raw).unlink()
        with self.assertRaises(video_edit.VideoEditError):
            selection.source_revision(self.raw, require_transcript=False)

    def test_bound_list_is_stale_after_brief_transcript_source_or_external_selection_change(self):
        for suffix, content in ((".brief.txt", "Different brief"), (".transcript.json", '[]'),
                                ("", "changed media"), (".stories.json", '[{"title":"external","start":0,"end":6}]')):
            with self.subTest(suffix=suffix):
                answer = self.answer()
                web.import_stories(self.raw, answer)
                file = Path(self.raw + suffix)
                before = file.read_bytes() if file.exists() else None
                file.write_text(content, encoding="utf-8")
                self.assertIsNone(selection.read_selection(self.raw, self.phrases))
                if before is None:
                    file.unlink()
                else:
                    file.write_bytes(before)

    def test_unbound_legacy_is_not_current_and_corruption_raises(self):
        video_edit.stories_file(self.raw).write_text('[{"title":"legacy","start":0,"end":6}]', encoding="utf-8")
        self.assertIsNone(selection.read_selection(self.raw, self.phrases))
        video_edit.stories_file(self.raw).write_text("broken", encoding="utf-8")
        with self.assertRaises(video_edit.VideoEditError):
            selection.read_selection(self.raw, self.phrases)

    def test_copied_binding_same_basename_is_not_current_for_another_source(self):
        web.import_stories(self.raw, self.answer())
        other = Path(self.temp.name) / "other" / "talk.mp4"
        other.parent.mkdir()
        other.write_bytes(Path(self.raw).read_bytes())
        for suffix in (".transcript.json", ".stories.json", ".story-selection.json"):
            Path(str(other) + suffix).write_bytes(Path(self.raw + suffix).read_bytes())
        self.assertIsNone(selection.read_selection(other, self.phrases))

    def test_selection_hash_changes_plan_cas_but_not_input_revision(self):
        before = selection.source_revision(self.raw)
        plan_before = selection.source_revision(self.raw, include_stories=True)
        web.import_stories(self.raw, self.answer())
        self.assertEqual(selection.source_revision(self.raw), before)
        self.assertNotEqual(selection.source_revision(self.raw, include_stories=True), plan_before)

    def test_receipt_replay_is_historical_and_does_not_restore_old_selection(self):
        answer = self.answer()
        first = web.import_answer("stories", self.raw, json.dumps(answer))
        later = [{**self.story, "title": "Changed choice", "end_anchor": {"cue_id": "source-1", "word": 7}}]
        web.import_stories(self.raw, self.answer(later))
        files = [video_edit.stories_file(self.raw), selection.selection_file(self.raw)]
        before = [file.read_bytes() for file in files]
        self.assertEqual(web.import_answer("stories", self.raw, json.dumps(answer)), first)
        self.assertEqual([file.read_bytes() for file in files], before)

    def test_two_file_failure_and_crash_restore_both_versions(self):
        web.import_stories(self.raw, self.answer())
        files = [video_edit.stories_file(self.raw), selection.selection_file(self.raw)]
        before = [file.read_bytes() for file in files]
        original_copy = transaction._copy
        for error in (OSError("write failed"), KeyboardInterrupt("process stopped")):
            with self.subTest(error=type(error).__name__):
                answer = self.answer([{**self.story, "title": "Replacement"}])
                def fail(source, target):
                    if target == files[0] and source.parent.name == "after":
                        raise error
                    return original_copy(source, target)
                with patch.object(transaction, "_copy", side_effect=fail), self.assertRaises(type(error)):
                    web.import_stories(self.raw, answer)
                if isinstance(error, KeyboardInterrupt):
                    self.assertNotEqual(files[1].read_bytes(), before[1])
                    transaction.recover("stories", self.raw)
                self.assertEqual([file.read_bytes() for file in files], before)
                self.assertIsNone(transaction.receipt("stories", self.raw, answer))


if __name__ == "__main__":
    unittest.main()
