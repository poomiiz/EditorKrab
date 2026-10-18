"""All subtitle channels share exact source coverage and real whole-word clocks."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subtitle_merge_test as fixture
import subtitle_plan as plan
import web_ai_json as web
import video_editor as api
import capcut_edit
import cue_data
import render
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


class PurePlan(unittest.TestCase):
    def resolve(self, text="normal keyword", items=None, words=None, **kwargs):
        return plan.resolve(text, items if items is not None else [["normal", "keyword"]],
                            words if words is not None else [[0, 1, "normal "], [1.8, 2.2, "keyword"]],
                            cue_id="source-cue", start=5, end=9, **kwargs)

    def test_actual_onset_and_hold_are_not_letter_timed(self):
        out = self.resolve()[0]
        self.assertEqual((out["start"], out["hit"], out["end"]), (5, 6.8, 9))
        self.assertEqual(out["clock_status"], "word")

    def test_resolving_preserves_source_labels_and_input_options(self):
        words = [[0, 1, "normal "], [1.8, 2.2, "keyword"]]
        items = [["normal", "keyword", {"show": ["Short", "Display"]}]]
        original = copy.deepcopy((words, items))
        self.resolve(items=items, words=words)
        self.assertEqual((words, items), original)

    def test_plain_without_clock_is_explicitly_cue_timed(self):
        out = plan.resolve("plain text", None, None, cue_id="plain", start=2, end=4)[0]
        self.assertEqual((out["start"], out["end"], out["clock_status"]), (2, 4, "cue"))
        with self.assertRaisesRegex(plan.VideoEditError, "เวลาระดับคำ"):
            self.resolve(words=[])
        with self.assertRaisesRegex(plan.VideoEditError, "เวลาระดับคำ"):
            plan.resolve("plain text", [["plain", ""], ["text", ""]], None, cue_id="plain", start=2, end=4)
        for options in ({"look": "color"}, {"second": "reaction"}):
            with self.assertRaisesRegex(plan.VideoEditError, "เวลาระดับคำ"):
                plan.resolve("plain text", [["plain text", "", options]], None, cue_id="plain", start=2, end=4)
        with self.assertRaisesRegex(plan.VideoEditError, "punch จากคำพูดจริง"):
            plan.resolve("plain text", [["plain text", "", {"show": ["Plain", "invented emphasis"]}]], None,
                         cue_id="plain", start=2, end=4)

    def test_thai_partial_vowels_are_one_complete_word(self):
        words = [[0, 1, "พูด"], [1, 2, "ดี"], [2, 2.1, "คร"], [2.1, 2.2, "ั"], [2.2, 2.4, "บ"]]
        result = self.resolve("พูดดีครับ", [["พูดดี", "ครับ"]], words)[0]
        self.assertEqual(result["hit"], 7)
        with self.assertRaisesRegex(plan.VideoEditError, "กลางคำ"):
            self.resolve("พูดดีครับ", [["พูดดีคร", "ับ"]], words)

    def test_single_punch_boundary_inside_token_is_refused(self):
        with self.assertRaisesRegex(plan.VideoEditError, "กลางคำ"):
            self.resolve("abcd", [["ab", "cd"]], [[0, 2, "abcd"]])

    def test_corrected_whole_word_spelling_preserves_keyword_clock(self):
        words = [[0, 1, "artificial intelligence "], [1.8, 2.2, "keyword"]]
        result = self.resolve("AI keyword", [["AI", "keyword"]], words)[0]
        self.assertEqual(result["hit"], 6.8)
        self.assertEqual(result["line"], "AI keyword")
        with self.assertRaisesRegex(plan.VideoEditError, "กลางคำ"):
            self.resolve("AI keyword", [["A", "I keyword"]], words)

    def test_corrected_reorder_or_ambiguous_repeat_removal_rejects_old_clocks(self):
        cases = ((["cat", "dog"], "dog cat"), (["cat", "cat", "dog"], "cat dog"),
                 (["cat", "dog", "cat"], "cat cat dog"), (["cat", "dog"], "DOG, CAT!"))
        for heard, wanted in cases:
            words = [[n, n + .8, word + " "] for n, word in enumerate(heard)]
            with self.subTest(heard=heard, wanted=wanted), self.assertRaisesRegex(plan.VideoEditError, "จับคู่เวลาคำ"):
                plan.resolve(wanted, [["", wanted]], words, cue_id="corrected", start=10, end=14)

    def test_same_position_typo_and_expansion_keep_known_word_onset(self):
        cases = ((["cat", "dog"], "cats dog", "cats", "dog", 1),
                 (["cat", "dog"], "feline animal dog", "feline animal", "dog", 1),
                 (["cat", "cat", "dog"], "cat cap dog", "cat cap", "dog", 2))
        for heard, wanted, lead, punch, onset in cases:
            words = [[n, n + .8, word + " "] for n, word in enumerate(heard)]
            with self.subTest(wanted=wanted):
                result = plan.resolve(wanted, [[lead, punch]], words, cue_id="corrected", start=10, end=14)[0]
                self.assertEqual(result["hit"], 10 + onset)

    def test_all_look_options_preserve_source_and_display_separation(self):
        for look in sorted(plan.LOOKS):
            options = {"look": look, "show": ["Short", "Display"], "color": "#aBc123", "second": "Reaction"}
            result = self.resolve(items=[["normal", "keyword", "stock query", options]])[0]
            self.assertEqual((result["lead"], result["punch"], result["query"]), ("normal", "keyword", "stock query"))
            self.assertEqual(result["opts"], options)
            self.assertEqual(result["hit"], 6.8)

    def test_invalid_options_and_coverage_raise_domain_errors(self):
        for items in ([["invented", "keyword"]], [["normal", "keyword", {"look": []}]],
                      [["normal", "keyword", {"look": "wrong"}]], [["normal", "keyword", {"color": "red"}]],
                      [["normal", "keyword", {"show": ["one"]}]], [["normal", "keyword", {"second": 1}]],
                      [["normal", "keyword", {"unrecognized": True}]], [["normal", "keyword", {}, {}]],
                      [["normal", "keyword", "query", "ignored query"]], [["normal"]]):
            with self.subTest(items=items), self.assertRaises(plan.VideoEditError):
                self.resolve(items=items)

    def test_finite_ordered_bounded_clocks_and_nonzero_display(self):
        for words in ([[float("nan"), 1, "normal "], [1.8, 2.2, "keyword"]],
                      [[True, 1, "normal "], [1.8, 2.2, "keyword"]],
                      [[0, 2, "normal "], [1.8, 2.2, "keyword"]],
                      [[0, 1, "normal "], [1.8, 5, "keyword"]],
                      [[-1, 1, "normal "], [1.8, 2.2, "keyword"]]):
            with self.subTest(words=words), self.assertRaises(plan.VideoEditError):
                self.resolve(words=words)

    def test_repeated_wording_keeps_occurrence_clock(self):
        first = plan.resolve("same", [["", "same"]], [[.2, .4, "same"]], cue_id="one", start=1, end=3)[0]
        second = plan.resolve("same", [["", "same"]], [[.8, 1, "same"]], cue_id="two", start=4, end=6)[0]
        self.assertEqual((first["cue_id"], first["hit"]), ("one", 1.2))
        self.assertEqual((second["cue_id"], second["hit"]), ("two", 4.8))


class ChannelPreflight(unittest.TestCase):
    def setUp(self):
        self.f = fixture.MergeTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.path = str(self.f.folder)
        # These tests exercise pair roles/stacks explicitly; a missing style means native plain text.
        (self.f.folder / "clipkit_style.json").write_text('{"anim":"pair"}', encoding="utf-8")

    def files(self):
        return {p.name: p.read_bytes() for p in self.f.folder.iterdir() if p.is_file()}

    def unmerged(self):
        return {**self.f.answer, "lines": [{"n": n, "cue_id": f"s{n}", "items": [["", text]]}
                                           for n, text in enumerate(("in", "the", "office"), 1)]}

    def test_unmerged_missing_clock_fails_before_real_files_change(self):
        (self.f.folder / "clipkit_words.json").unlink()
        before = self.files()
        with self.assertRaisesRegex(plan.VideoEditError, "เวลาระดับคำ"):
            self.f.import_answer(self.unmerged())
        self.assertEqual(self.files(), before)

    def test_v2_missing_or_wrong_cue_id_fails_before_writes(self):
        for value in (None, "another-cue"):
            answer = self.unmerged()
            answer["lines"][0]["cue_id"] = value
            before = self.files()
            with self.assertRaisesRegex(plan.VideoEditError, "cue ID"):
                self.f.import_answer(answer)
            self.assertEqual(self.files(), before)

    def test_merged_and_unmerged_preflight_real_canvas_and_preset(self):
        for answer in (self.unmerged(), self.f.answer):
            with patch.object(render, "validate_pair_cues", wraps=render.validate_pair_cues) as preflight:
                self.f.import_answer(answer)
                self.assertEqual((preflight.call_args.kwargs["W"], preflight.call_args.kwargs["H"]), (1920, 1080))

    def test_sentence_pair_motion_range_covers_hold_and_cross_cue_stack(self):
        for color_stack, expected_end in ((False, .7), (True, 1.8)):
            answer = self.unmerged()
            answer["motion"] = [{"n": 1, "template": "sentence-pair"}]
            if color_stack:
                for line in answer["lines"][:2]:
                    line["items"][0].append({"look": "color"})
            with patch.object(api, "prepare_overlay", return_value={"candidate":{"start":0,"duration":1}}), \
                    patch.object(api, "overlay_plan", return_value={}), \
                    patch.object(api, "render_overlay", return_value={"start":0}) as overlay:
                self.f.import_answer(answer)
            request = overlay.call_args.args[0]
            self.assertEqual((request.cue_id, request.start, request.end), ("s1", 0, expected_end))
        answer = self.unmerged()
        answer["motion"] = [{"n": 3, "template": "sentence-pair"}]
        with patch.object(api, "prepare_overlay", return_value={"candidate":{"start":0,"duration":1}}), \
                patch.object(api, "overlay_plan", return_value={}), \
                patch.object(api, "render_overlay", return_value={"start":0}) as overlay:
            self.f.import_answer(answer)
        self.assertEqual(overlay.call_args.args[0].end, 4)

    def test_manual_invalid_options_or_implicit_source_rewrite_change_nothing(self):
        app = FastAPI()
        app.include_router(api.router)
        before = self.files()
        with TestClient(app) as client:
            for pairs in ([["in", "", {"look": "unknown"}]], [["invented", ""]]):
                response = client.post("/api/video/draft/phrases", json={"path": self.path, "line": "in", "cue_id": "s1", "pairs": pairs})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.files(), before)

    def test_explicit_manual_spelling_preflights_before_source_edit(self):
        import spelling
        with patch.object(spelling, "learn"), patch.object(capcut_edit, "set_line_text", wraps=capcut_edit.set_line_text) as changed:
            result = api.draft_phrases(api.PhraseEdit(path=self.path, line="in", cue_id="s1", source_text="IN", pairs=[["IN", ""]]))
        self.assertEqual(result["line"], "IN")
        self.assertEqual(changed.call_count, 1)
        self.assertNotEqual(changed.call_args.args[0], self.path)
        self.assertEqual(changed.call_args.args[1:], ("in", "IN", "s1"))
        self.assertEqual(changed.call_args.kwargs, {"picks_override": [["IN", ""]]})
        self.assertEqual(capcut_edit.read_draft(self.path)["subtitles"][0]["text"], "IN")

    def test_explicit_manual_source_failure_rolls_back_draft_rules_and_choices(self):
        import web_ai_transaction as transaction
        original = cue_data.save
        def fail(folder, filename, data):
            if filename == "clipkit_punch.json" and "/work/" in Path(folder).as_posix():
                raise OSError("manual choice save failed")
            return original(folder, filename, data)
        before = {name: path.read_bytes() for name, path in transaction._files(self.f.folder).items()}
        with patch.object(cue_data, "save", side_effect=fail), self.assertRaisesRegex(OSError, "choice save failed"):
            api.draft_phrases(api.PhraseEdit(path=self.path, line="in", cue_id="s1", source_text="IN", pairs=[["IN", ""]]))
        self.assertEqual({name: path.read_bytes() for name, path in transaction._files(self.f.folder).items()}, before)

    def test_combined_caption_choices_and_source_fail_without_partial_caption(self):
        import web_ai_transaction as transaction
        cue_data.save(self.path, "clipkit_caption.json", {"cue:s1": "old caption", "cue:s2": "other caption"})
        before = {name: path.read_bytes() for name, path in transaction._files(self.f.folder).items()}
        with self.assertRaises(HTTPException) as invalid:
            api.draft_phrases(api.PhraseEdit(path=self.path, line="in", cue_id="s1", pairs=[["invented", ""]], caption="new caption"))
        self.assertEqual(invalid.exception.status_code, 422)
        self.assertEqual({name: path.read_bytes() for name, path in transaction._files(self.f.folder).items()}, before)
        original = cue_data.save
        def fail(folder, filename, data):
            if filename == "clipkit_punch.json" and "/work/" in Path(folder).as_posix():
                raise OSError("combined choice save failed")
            return original(folder, filename, data)
        with patch.object(cue_data, "save", side_effect=fail), self.assertRaisesRegex(OSError, "combined choice save failed"):
            api.draft_phrases(api.PhraseEdit(path=self.path, line="in", cue_id="s1", source_text="IN", pairs=[["IN", ""]], caption="new caption"))
        self.assertEqual({name: path.read_bytes() for name, path in transaction._files(self.f.folder).items()}, before)

    def test_combined_caption_update_clear_and_none_preserve_other_cues(self):
        cue_data.save(self.path, "clipkit_caption.json", {"cue:s1": "old caption", "cue:s2": "other caption"})
        for caption, expected in (("new caption", {"cue:s1": "new caption", "cue:s2": "other caption"}),
                                  (None, {"cue:s1": "new caption", "cue:s2": "other caption"}),
                                  ("", {"cue:s2": "other caption"})):
            api.draft_phrases(api.PhraseEdit(path=self.path, line="in", cue_id="s1", pairs=[["in", ""]], caption=caption))
            rows = capcut_edit.read_draft(self.path)["subtitles"]
            self.assertEqual(cue_data.load(self.path, "clipkit_caption.json", rows), expected)
            self.assertEqual(rows[0]["text"], "in")

    def test_manual_corrected_word_reorder_rejects_before_any_mutation(self):
        words = [[0, .2, "cat "], [.3, .5, "dog"]]
        draft = self.f.draft
        draft["materials"]["texts"][0]["content"] = json.dumps({"text": "cat dog", "styles": []})
        self.f.content.write_text(json.dumps(draft), encoding="utf-8")
        cue_data.save(self.path, "clipkit_words.json", {"cue:s1": words, "cue:s2": [[0, .5, "the"]], "cue:s3": [[0, 2, "office"]]})
        before = self.files()
        with self.assertRaises(HTTPException) as error:
            api.draft_phrases(api.PhraseEdit(path=self.path, line="cat dog", cue_id="s1", source_text="dog cat",
                                             pairs=[["", "dog cat"]], caption="changed caption"))
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.files(), before)

    def test_invalid_style_does_not_save_and_export_uses_real_layout(self):
        before = self.files()
        with self.assertRaises(HTTPException) as failure:
            api.draft_look(api.LookRequest(path=self.path, style={"preset": "does-not-exist"}))
        self.assertEqual(failure.exception.status_code, 422)
        self.assertEqual(self.files(), before)
        with patch.object(web, "_motion_menu", return_value=[]):
            packet = web.export_clip(self.path).read_text(encoding="utf-8")
        data = json.loads(packet.rsplit("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(data["subtitle_layout"]["canvas"], {"width": 1920, "height": 1080})
        self.assertEqual(data["lines"][0]["words"][0]["word"],1)
        self.assertNotIn(str(self.f.folder), json.dumps(data["subtitle_layout"]))

    def test_manual_subtitle_ranges_and_existing_picks_validate_before_save(self):
        rows = capcut_edit.read_draft(self.path)["subtitles"]
        before = self.files()
        variants = ([{**rows[0], "start": -1}], [{**rows[0], "end": 5}], [rows[1], rows[0]],
                    [{**rows[0], "end": float("nan")}],
                    [{**rows[0], "words": [[0, 1, "in"]]}])
        for subs in variants:
            with self.subTest(subs=subs), self.assertRaises(plan.VideoEditError):
                capcut_edit.set_subtitles(self.path, subs)
            self.assertEqual(self.files(), before)
        cue_data.save(self.path, "clipkit_punch.json", {"cue:s1": [["in", ""]]})
        before = self.files()
        with self.assertRaisesRegex(plan.VideoEditError, "lead.*punch"):
            capcut_edit.set_subtitles(self.path, [{**rows[0], "text": "changed source"}])
        self.assertEqual(self.files(), before)

    def test_manual_subtitle_save_keeps_ids_clocks_and_exact_valid_end(self):
        rows = capcut_edit.read_draft(self.path)["subtitles"]
        original_clock = cue_data.load(self.path, "clipkit_words.json", rows)
        capcut_edit.set_subtitles(self.path, rows)
        current = capcut_edit.read_draft(self.path)["subtitles"]
        self.assertEqual([r["id"] for r in current], [r["id"] for r in rows])
        self.assertEqual(cue_data.load(self.path, "clipkit_words.json", current), original_clock)
        capcut_edit.set_subtitles(self.path, [{"id": "complete", "start": 0, "end": 4, "text": "complete",
                                             "words": [[0, 4, "complete"]]}])
        current = capcut_edit.read_draft(self.path)["subtitles"]
        self.assertEqual((current[0]["id"], current[0]["end"]), ("complete", 4))

    def test_manual_subtitle_word_write_failure_restores_original_project(self):
        rows = capcut_edit.read_draft(self.path)["subtitles"]
        # New IDs make the word sidecar change while the source remains intact.
        subs = [{"id": "complete", "start": 0, "end": 4, "text": "complete", "words": [[0, 4, "complete"]]}]
        before = self.files()
        original = cue_data.save
        def fail(folder, filename, data):
            if filename == "clipkit_words.json":
                raise OSError("word sidecar failed")
            return original(folder, filename, data)
        with patch.object(cue_data, "save", side_effect=fail), self.assertRaisesRegex(OSError, "word sidecar failed"):
            capcut_edit.set_subtitles(self.path, subs)
        self.assertEqual(self.files(), before)

    def test_precise_native_bounds_roundtrip_api_merge_and_direct_correction(self):
        self.f.draft["tracks"][1]["segments"][-1]["target_timerange"]["duration"] = 1_234_000
        self.f.content.write_text(json.dumps(self.f.draft), encoding="utf-8")
        self.f.words["office"] = [[0, 1.234, "office"]]
        (self.f.folder / "clipkit_words.json").write_text(json.dumps(self.f.words), encoding="utf-8")
        app = FastAPI()
        app.include_router(api.router)
        with TestClient(app) as client:
            response = client.post("/api/video/draft", json={"path": self.path})
            self.assertEqual(response.status_code, 200, response.text)
            rows = response.json()["subtitles"]
            self.assertEqual(rows[-1]["end"], 3.034)
            response = client.post("/api/video/draft/subs", json={"path": self.path, "subs": rows})
            self.assertEqual(response.status_code, 200, response.text)
        web.validate_subtitles(self.path)
        candidate = capcut_edit.read_draft(self.path)["subtitles"]
        candidate[-1]["end"] = 3.03  # Explicit shorter edits remain shorter; they cannot borrow the native bound.
        with self.assertRaisesRegex(plan.VideoEditError, "ช่วงซับจริง"):
            web.validate_subtitles(self.path, rows=candidate)
        capcut_edit.merge_subtitles(self.path, [(2, 3)])
        self.assertEqual(capcut_edit.read_draft(self.path)["subtitles"][-1]["end"], 3.034)
        import spelling
        with patch.object(spelling, "FILE", self.f.folder / "machine-rules.json"):
            capcut_edit.set_line_text(self.path, "the office", "the OFFICE", "s2")
        current = capcut_edit.read_draft(self.path)["subtitles"][-1]
        self.assertEqual((current["text"], current["end"]), ("the OFFICE", 3.034))

    def test_caption_overflow_is_rejected_before_sidecar_write(self):
        (self.f.folder / "clipkit_style.json").write_text('{"anim":"pair","preset":"with-caption"}', encoding="utf-8")
        before = self.files()
        # Model the fixed layout gate with its domain error while proving the API writes only after it.
        with patch.object(render, "validate_pair_cues", side_effect=plan.VideoEditError("cue s1: caption overflow")):
            with self.assertRaises(HTTPException) as error:
                api.draft_caption(api.CaptionEdit(path=self.path, line="in", cue_id="s1", text="caption"))
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.files(), before)


if __name__ == "__main__":
    unittest.main()
