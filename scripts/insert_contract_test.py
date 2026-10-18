"""CLI inserts reuse registered assets and individually bound v2 project transactions."""
import copy
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))
with tempfile.TemporaryDirectory() as import_folder:
    config = Path(import_folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import run_clip as cli
from cli_resume_test import accept_selection, write_project
import insert_plan
import jobstore
import web_ai_json as web
from PIL import Image


class InsertContract(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config = self.root / "config.json"
        config.write_text("{}", encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}))
        self.enterContext(patch.object(cli.video_edit.kitconfig, "_CANDIDATES", [config]))
        self.enterContext(patch.object(jobstore, "STATE", self.root / "locks"))
        self.enterContext(patch("host_platform.capcut_running", return_value=False))
        self.raw = self.root / "talk.v1.mp4"
        self.raw.write_bytes(b"synthetic source; no media decode is needed")
        self.downloads = self.root / "downloads"
        self.downloads.mkdir()
        for name, color in (("1 coffee.png", "blue"), ("office.png", "red"), ("building.png", "green")):
            Image.new("RGB", (64, 48), color).save(self.downloads / name)
        self.originals = {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in self.downloads.iterdir()}
        self.projects = [self.root / "first", self.root / "second"]
        self.stories = accept_selection(self.raw,
            [{"start": 0, "end": 10, "text": "first story"}, {"start": 10, "end": 20, "text": "second story"}],
            [{"title": "First", "start": 0, "end": 10}, {"title": "Second", "start": 10, "end": 20}])
        self.initial_placed = {"broll": [], "music": {"file": "", "volume": .27}, "sfx": {"on": False, "volume": .75}}
        for project, story in zip(self.projects, self.stories):
            write_project(project, self.raw, story)
            draft = json.loads((project / "draft_content.json").read_text(encoding="utf-8"))
            draft["materials"]["texts"] = [{"id": "text", "font_size": 15,
                "content": json.dumps({"text": "normal keyword", "styles": []})}]
            draft["tracks"].append({"type": "text", "segments": [{"id": "spoken", "material_id": "text",
                "clip": {"scale": {"x": 1, "y": 1}, "transform": {"x": 0, "y": 0}},
                "target_timerange": {"start": 0, "duration": 3_000_000}}]})
            (project / "draft_content.json").write_text(json.dumps(draft), encoding="utf-8")
            (project / "clipkit_words.json").write_text(json.dumps({"cue:spoken": [[0, .8, "normal "], [1, 1.8, "keyword"]]}), encoding="utf-8")
            (project / "clipkit_punch.json").write_text(json.dumps({"cue:spoken": [["normal", "keyword", "coffee"]]}), encoding="utf-8")
            (project / "clipkit_placed.json").write_text(json.dumps(self.initial_placed), encoding="utf-8")
        self.context = cli._resume_context(str(self.raw), shape="portrait", look="pair", preset="default")
        self.state_file = Path(str(self.raw) + ".clipkit_run.json")
        self.state = cli._load_resume(self.state_file)
        for project, story in zip(self.projects, self.stories):
            cli._save_project(self.state_file, self.state, cli._manifest(self.context, story), str(project))
        self.answer_file = self.root / "answer.json"

    def assert_originals(self):
        self.assertEqual({file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in self.downloads.iterdir()}, self.originals)

    def placed_bytes(self):
        return [(project / "clipkit_placed.json").read_bytes() for project in self.projects]

    def receipts(self):
        return [file for project in self.projects for file in (project / ".clipkit-web-ai" / "receipts").glob("*.json")]

    def export(self):
        md, pictures = cli.sheet(str(self.raw), self.stories, [1, 2], self.state, str(self.downloads), context=self.context)
        data = json.loads(re.findall(r"```json\s*(.*?)```", md.read_text(encoding="utf-8"), re.S)[-1])
        return md, pictures, data

    def answer(self):
        _, _, data = self.export()
        answers = []
        for packet in data["projects"]:
            header = {key: packet[key] for key in ("clipkit_web_ai", "step", "operation", "project", "packet_id", "revision", "source_id")}
            answers.append({**header, "place": [{"asset_id": packet["assets"][0]["asset_id"],
                "anchor": packet["opportunities"][0]["anchor"], "opportunity_id": packet["opportunities"][0]["opportunity_id"],
                "reason": "The image supports the spoken keyword.", "source_start": 0, "dur": 1,
                "focus_x": .5, "focus_y": .5}]})
        return {"clipkit_web_ai": 2, "step": "clip", "operation": "inserts", "projects": answers}

    def place(self, answer):
        self.answer_file.write_text(json.dumps(answer), encoding="utf-8")
        return cli.place(str(self.raw), self.stories, [1, 2], self.state, str(self.downloads), str(self.answer_file), context=self.context)

    def test_sheet_reuses_shared_export_and_contains_ids_clocks_images_without_private_paths(self):
        with patch.object(web, "export_inserts", wraps=web.export_inserts) as shared:
            md, pictures, data = self.export()
        self.assertEqual(shared.call_count, 2)
        self.assertEqual(len(pictures), 2)
        self.assertTrue(all(picture.is_file() for picture in pictures))
        self.assertIn("talk.v1 - inserts", md.name)
        self.assertNotIn(str(self.root), md.read_text(encoding="utf-8"))
        self.assertEqual(len(data["projects"]), 2)
        for packet in data["projects"]:
            self.assertEqual((packet["clipkit_web_ai"], packet["step"], packet["operation"]), (2, "clip", "inserts"))
            self.assertEqual(len(packet["assets"]), 3)
            self.assertTrue(all(asset["still"] for asset in packet["assets"]))
            self.assertEqual(packet["opportunities"][0]["anchor"], {"cue_id": "spoken", "kind": "keyword", "phrase": 0})
            self.assertEqual(packet["opportunities"][0]["hit"], 1)
        self.assert_originals()
        self.assertEqual(self.placed_bytes(), [json.dumps(self.initial_placed).encode()] * 2)

    def test_bound_batch_preserves_originals_music_sfx_words_and_replays_receipts_once(self):
        answer = self.answer()
        words = [(project / "clipkit_words.json").read_bytes() for project in self.projects]
        result = self.place(answer)
        self.assertEqual(len(result), 2)
        self.assertEqual(len(self.receipts()), 2)
        for project in self.projects:
            placed = json.loads((project / "clipkit_placed.json").read_text(encoding="utf-8"))
            self.assertEqual(placed["music"], self.initial_placed["music"])
            self.assertEqual(placed["sfx"], self.initial_placed["sfx"])
            self.assertEqual((placed["broll"][0]["start"], placed["broll"][0]["source_start"]), (1, 0))
            self.assertEqual(cli.insert(str(project)), 1)
        saved = self.placed_bytes()
        self.assertEqual(self.place(answer), result)
        self.assertEqual(self.placed_bytes(), saved)
        self.assertEqual([(project / "clipkit_words.json").read_bytes() for project in self.projects], words)
        self.assertEqual(len(self.receipts()), 2)
        self.assert_originals()

    def test_no_plan_never_guesses_from_numbered_names_or_moves_originals(self):
        before = self.placed_bytes()
        with self.assertRaisesRegex(cli.video_edit.VideoEditError, "insert-sheet"):
            cli.place(str(self.raw), self.stories, [1, 2], self.state, str(self.downloads), context=self.context)
        self.assertEqual(self.placed_bytes(), before)
        self.assertFalse(any((project / insert_plan.CATALOG).exists() for project in self.projects))
        self.assert_originals()

    def test_legacy_unbound_answer_requests_fresh_export_without_writes(self):
        before = self.placed_bytes()
        with self.assertRaisesRegex(cli.video_edit.VideoEditError, "fresh --insert-sheet"):
            self.place({"clipkit_web_ai": 1, "step": "inserts", "video": self.raw.name,
                        "place": [{"file": "1 coffee.png", "link": 1}]})
        self.assertEqual(self.placed_bytes(), before)
        self.assert_originals()

    def test_invalid_second_project_is_preflighted_before_first_project_applies(self):
        answer = self.answer()
        answer["projects"][1]["place"][0]["asset_id"] = "0" * 64
        before = self.placed_bytes()
        with self.assertRaises(cli.video_edit.VideoEditError):
            self.place(answer)
        self.assertEqual(self.placed_bytes(), before)
        self.assertEqual(self.receipts(), [])
        self.assert_originals()

    def test_missing_duplicate_or_wrong_operation_envelopes_change_no_placements(self):
        answer = self.answer()
        bad = [dict(answer, projects=answer["projects"][:1]), dict(answer, projects=[answer["projects"][0]] * 2)]
        wrong_operation = copy.deepcopy(answer)
        wrong_operation["projects"][1]["operation"] = "clip"
        bad.append(wrong_operation)
        before = self.placed_bytes()
        for candidate in bad:
            with self.subTest(candidate=candidate), self.assertRaises(cli.video_edit.VideoEditError):
                self.place(candidate)
            self.assertEqual(self.placed_bytes(), before)
            self.assertEqual(self.receipts(), [])
        self.assert_originals()

    def test_changed_registered_asset_is_rejected_before_any_project_applies(self):
        answer = self.answer()
        asset = next(iter(insert_plan.catalog(self.projects[1])["assets"].values()))
        cached = Path(asset["file"])
        cached.write_bytes(b"X" * cached.stat().st_size)
        before = self.placed_bytes()
        with self.assertRaises(cli.video_edit.VideoEditError):
            self.place(answer)
        self.assertEqual(self.placed_bytes(), before)
        self.assertEqual(self.receipts(), [])
        self.assert_originals()

    def test_partial_batch_failure_reports_receipts_and_retry_finishes_remaining_project(self):
        answer = self.answer()
        original = web.import_clip
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second project failure")
            return original(*args, **kwargs)
        with patch.object(web, "import_clip", side_effect=fail_second), self.assertRaisesRegex(cli.video_edit.VideoEditError, "completed receipts"):
            self.place(answer)
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(json.loads((self.projects[1] / "clipkit_placed.json").read_text(encoding="utf-8")), self.initial_placed)
        first = (self.projects[0] / "clipkit_placed.json").read_bytes()
        self.assertEqual(len(self.place(answer)), 2)
        self.assertEqual((self.projects[0] / "clipkit_placed.json").read_bytes(), first)
        self.assertEqual(len(self.receipts()), 2)
        self.assert_originals()

    def test_insert_from_without_plan_exports_then_waits_before_any_render_or_match(self):
        args = cli._parser().parse_args([str(self.raw), "--all", "--preset", "default", "--insert-from", str(self.downloads)])
        before = self.placed_bytes()
        with patch.object(cli, "stories", return_value=self.stories), patch.object(cli, "say") as say, \
                patch.object(cli, "make_project", side_effect=AssertionError("must reuse current projects")), \
                patch.object(cli.ve, "broll_fill", side_effect=AssertionError("must wait for explicit match")):
            self.assertEqual(cli._pipeline(args, str(self.raw)), 2)
        self.assertTrue(any(call.kwargs.get("WAIT") and len(call.kwargs.get("upload", [])) == 3 for call in say.call_args_list))
        self.assertEqual(self.placed_bytes(), before)
        self.assertEqual(self.receipts(), [])
        self.assert_originals()


if __name__ == "__main__":
    unittest.main()
