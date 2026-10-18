"""Verified insert identities, real anchors and read-only preview share one transaction contract."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subtitle_merge_test as fixture
import insert_plan as plan
import web_ai_json as web
import web_ai_identity as identity
import web_ai_transaction as transaction
import jobstore
from PIL import Image


class InsertPlan(unittest.TestCase):
    def setUp(self):
        self.f = fixture.MergeTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.folder = self.f.folder
        self.enterContext(patch.object(jobstore, "STATE", self.folder / "test-state"))
        self.source = self.folder.parent / "chosen.png"
        Image.new("RGB", (200, 100), "red").save(self.source)
        self.cat = plan.register_assets(self.folder, [self.source])
        self.asset_id = self.cat["candidates"][0]
        self.item = {"asset_id": self.asset_id, "anchor": {"kind": "word", "cue_id": "s2", "word": 1},
                     "source_start": 0, "dur": .4, "focus_x": .5, "focus_y": .5, "reason": "Shows the described subject"}

    def answer(self):
        packet, sheet = web.export_inserts(str(self.folder))
        import re
        data = json.loads(re.findall(r"```json\s*(.*?)```", packet.read_text(encoding="utf-8"), re.S)[-1])
        return {key: data[key] for key in ("clipkit_web_ai", "step", "operation", "project", "packet_id", "revision", "source_id")} | {"place": [copy.deepcopy(self.item)]}

    def files(self):
        return {name: file.read_bytes() for name, file in transaction._files(self.folder).items()}

    def test_registration_copies_and_keeps_original_with_private_public_projection(self):
        asset = self.cat["assets"][self.asset_id]
        self.assertEqual(self.source.read_bytes(), Path(asset["file"]).read_bytes())
        self.assertNotEqual(self.source.resolve(), Path(asset["file"]))
        self.assertEqual(asset["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        public = json.dumps(plan.public_assets(self.cat))
        self.assertNotIn(str(self.folder), public)
        self.assertNotIn(str(self.source), public)
        self.assertEqual(asset["source_ref"]["license_status"], "unverified")

    def test_same_name_same_size_different_bytes_do_not_alias_and_history_is_not_public(self):
        other = self.folder.parent / "other" / self.source.name
        other.parent.mkdir()
        Image.new("RGB", (200, 100), "blue").save(other)
        cat = plan.register_assets(self.folder, [other])
        self.assertEqual(len(cat["assets"]), 2)
        self.assertNotEqual(cat["candidates"][0], self.asset_id)
        self.assertEqual(len(plan.public_assets(cat)), 1)
        self.assertTrue(self.source.exists())

    def test_digest_sees_changed_content_even_when_stat_is_restored(self):
        file = self.folder / "bytes.mp4"
        file.write_bytes(b"AAAA")
        before, stat = plan.file_digest(file), file.stat()
        file.write_bytes(b"BBBB")
        os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertNotEqual(plan.file_digest(file), before)

    def test_failed_registration_does_not_publish_partial_catalog_or_consume_originals(self):
        old = (self.folder / plan.CATALOG).read_bytes()
        other = self.folder.parent / "new.mp4"
        other.write_bytes(b"not decodable")
        with self.assertRaises(plan.VideoEditError):
            plan.register_assets(self.folder, [other])
        self.assertEqual((self.folder / plan.CATALOG).read_bytes(), old)
        self.assertEqual(other.read_bytes(), b"not decodable")
        self.assertFalse(list((self.folder / plan.CACHE).glob("register-*")))

    def test_opportunities_keep_repeated_query_occurrences_and_actual_word_times(self):
        (self.folder / "clipkit_punch.json").write_text(json.dumps({"cue:s1": [["", "in", "same search"]],
                                                                    "cue:s2": [["", "the", "same search"]]}))
        points = [point for point in plan.opportunities(self.folder) if point["query"]]
        self.assertEqual([point["hit"] for point in points], [0, .7])
        self.assertNotEqual(points[0]["opportunity_id"], points[1]["opportunity_id"])
        self.assertEqual(points[1]["words"][0]["word"], 1)
        self.assertEqual(plan.normalize(self.folder, [self.item])[0]["start"], .7)

    def test_whole_word_anchor_and_bounds_are_strict_before_writes(self):
        before = self.files()
        variants = [{"dur": 10}, {"dur": float("nan")}, {"source_start": .1}, {"focus_x": 2},
                    {"asset_id": []}, {"reason": ""}, {"start": .71},
                    {"anchor": {"kind": "word", "cue_id": "s2", "word": 0}},
                    {"anchor": {"kind": "timeline", "start": float("inf")}}]
        for change in variants:
            with self.subTest(change=change), self.assertRaises(plan.VideoEditError):
                plan.normalize(self.folder, [{**self.item, **change}])
        self.assertEqual(self.files(), before)

    def test_english_source_spaces_identify_existing_bare_word_labels(self):
        draft = json.loads(self.f.content.read_text())
        draft["materials"]["texts"][0]["content"] = json.dumps({"text": "in the"})
        self.f.content.write_text(json.dumps(draft))
        (self.folder / "clipkit_words.json").write_text(json.dumps(
            {**self.f.words, "cue:s1": [[0, .2, "in"], [.3, .5, "the"]]}))
        point = next(point for point in plan.opportunities(self.folder) if point["cue_id"] == "s1")
        self.assertEqual([(word["word"], word["start"]) for word in point["words"]], [(1, 0), (2, .3)])

    def test_exact_source_offset_and_ramp_consumption_not_rounded_or_clamped(self):
        video = self.folder.parent / "actual.mp4"
        video.write_bytes(b"synthetic probe-only asset")
        with patch.object(plan, "_probe", return_value={"duration": 1.234, "width": 100, "height": 200, "still": False}):
            cat = plan.register_assets(self.folder, [video])
        item = {**self.item, "asset_id": cat["candidates"][0], "source_start": 1, "dur": .234}
        self.assertEqual(plan.normalize(self.folder, [item])[0]["source_start"], 1)
        with self.assertRaises(plan.VideoEditError):
            plan.normalize(self.folder, [{**item, "dur": .235}])
        (self.folder / "clipkit_style.json").write_text(json.dumps({"ramp": True}))
        with self.assertRaises(plan.VideoEditError):
            plan.normalize(self.folder, [item])

    def test_video_end_uses_video_stream_not_longer_audio_or_container(self):
        video = self.folder / "probe.mp4"
        first = {"streams": [{"codec_type": "video", "width": 10, "height": 20, "duration": "1.234"}],
                 "format": {"duration": "40"}}
        completed = lambda value: subprocess.CompletedProcess([], 0, stdout=json.dumps(value))
        with patch.object(plan.subprocess, "run", return_value=completed(first)):
            self.assertEqual(plan._probe(video)["duration"], 1.234)
        first["streams"][0].pop("duration")
        first["streams"][0].update(duration_ts=1234, time_base="1/1000")
        with patch.object(plan.subprocess, "run", return_value=completed(first)):
            self.assertEqual(plan._probe(video)["duration"], 1.234)
        first["streams"][0].pop("duration_ts")
        packets = {"packets": [{"pts_time": "0", "duration_time": ".5"}, {"pts_time": ".5", "duration_time": ".5"}]}
        with patch.object(plan.subprocess, "run", side_effect=[completed(first), completed(packets)]):
            self.assertEqual(plan._probe(video)["duration"], 1)

    def test_preview_is_read_only_and_apply_preserves_music_sfx_and_originals(self):
        music = self.folder / "music.mp3"
        music.write_bytes(b"synthetic music")
        existing = {"broll": [], "music": {"file": str(music), "volume": .07}, "sfx": {"on": False, "volume": .2}}
        (self.folder / "clipkit_placed.json").write_text(json.dumps(existing))
        answer = self.answer()
        before = self.files()
        preview = web.preview_inserts(str(self.folder), json.dumps(answer))
        self.assertEqual(self.files(), before)
        self.assertEqual(preview["broll"][0]["start"], .7)
        first = web.import_clip(str(self.folder), answer)
        saved = json.loads((self.folder / "clipkit_placed.json").read_text())
        self.assertEqual((saved["music"], saved["sfx"]), (existing["music"], existing["sfx"]))
        self.assertEqual(saved["broll"], preview["broll"])
        self.assertTrue(self.source.exists())
        after = self.files()
        identity.issue("clip", str(self.folder))
        self.assertEqual(web.import_clip(str(self.folder), answer), first)
        self.assertEqual(self.files(), after)

    def test_operation_repurposing_and_changed_source_asset_are_rejected(self):
        answer = self.answer()
        with self.assertRaisesRegex(plan.VideoEditError, "operation"):
            identity.verify("clip", self.folder, {**answer, "operation": None})
        ordinary = {"clipkit_web_ai": 2, "step": "clip", **identity.issue("clip", self.folder)}
        with self.assertRaisesRegex(plan.VideoEditError, "operation"):
            identity.verify("clip", self.folder, {**ordinary, "operation": "inserts"})
        answer = self.answer()
        file = Path(self.cat["assets"][self.asset_id]["file"])
        stat = file.stat()
        file.write_bytes(file.read_bytes().replace(b"IDAT", b"Idat", 1))
        os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaises(plan.VideoEditError):
            web.import_clip(str(self.folder), answer)

    def test_local_answer_waits_for_review_and_same_packet_changed_answer_rejects(self):
        answer = self.answer()
        web.answer_file("clip", str(self.folder)).write_text(json.dumps(answer))
        before = self.files()
        result = web.import_answer("clip", str(self.folder), "")
        self.assertTrue(result["review_required"])
        self.assertEqual(self.files(), before)
        committed = web.import_clip(str(self.folder), answer)
        identity.issue("clip", self.folder)
        self.assertEqual(web.import_answer("clip", str(self.folder), ""), committed)
        self.assertFalse(web.answer_file("clip", str(self.folder)).exists())
        with self.assertRaises(plan.VideoEditError):
            web.import_clip(str(self.folder), {**answer, "place": []})

    def test_apply_write_failure_rolls_back_placed_and_leaves_source(self):
        (self.folder / "clipkit_placed.json").write_text(json.dumps({"broll": [], "music": {"file": ""}}))
        answer = self.answer()
        before = self.files()
        original = transaction._copy
        failed = False

        def fail_once(source, target):
            nonlocal failed
            if Path(target) == self.folder / "clipkit_placed.json" and not failed:
                failed = True
                raise OSError("synthetic write failure")
            return original(source, target)

        with patch.object(transaction, "_copy", side_effect=fail_once), self.assertRaisesRegex(OSError, "synthetic"):
            web.import_clip(str(self.folder), answer)
        self.assertEqual(self.files(), before)
        self.assertIsNone(transaction.receipt("clip", self.folder, answer))
        self.assertTrue(self.source.exists())

    def test_removed_old_reference_is_still_rehashed_before_receipt(self):
        old = self.folder.parent / "legacy.png"
        Image.new("RGB", (80, 60), "green").save(old)
        placed = self.folder / "clipkit_placed.json"
        placed.write_text(json.dumps({"broll": [{"start": 0, "dur": .2, "file": str(old)}]}))
        answer = self.answer()
        before = self.files()
        original = transaction._copy
        changed = False

        def replace_old(source, target):
            nonlocal changed
            original(source, target)
            if Path(target) == placed and not changed:
                changed = True
                stat = old.stat()
                old.write_bytes(old.read_bytes().replace(b"IDAT", b"Idat", 1))
                os.utime(old, ns=(stat.st_atime_ns, stat.st_mtime_ns))

        with patch.object(transaction, "_copy", side_effect=replace_old), self.assertRaises(plan.VideoEditError):
            web.import_clip(str(self.folder), answer)
        self.assertTrue(changed)
        self.assertEqual(self.files(), before)
        self.assertIsNone(transaction.receipt("clip", self.folder, answer))


if __name__ == "__main__":
    unittest.main()
