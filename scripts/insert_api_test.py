"""Manual/API insert boundaries share the real catalog, clock and placement validator."""
import json
import math
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import trim_timing_test as fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import insert_plan
import kitconfig
import kit_settings
import video_editor as api
from fastapi import HTTPException
from pydantic import ValidationError


class InsertAPI(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TrimTiming()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.folder = self.fixture.folder
        self.config = self.folder / "config.json"
        self.config.write_text("{}", encoding="utf-8")
        for mocked in (patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(self.config)}),
                       patch.object(kitconfig, "_CANDIDATES", [self.config]),
                       patch.object(kit_settings, "CONFIG", self.config)):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.image = self.folder.parent / "selected.png"
        Image.new("RGB", (480, 320), "blue").save(self.image)

    def request(self, **changes):
        row = {"file": str(self.image), "dur": .5, "anchor": {"kind": "keyword", "cue_id": "0", "phrase": 0},
               "reason": "ผู้ใช้เลือกภาพประกอบคำนี้", "focus_x": .8,
               "source_ref": {"provider": "own", "page": "https://example.org/source", "license_status": "unverified"}}
        row.update(changes)
        return api.PlacedRequest(path=str(self.folder), broll=[row], music={"file": "", "volume": .2}, sfx={"on": False})

    def test_manual_asset_copies_original_and_resolves_real_keyword(self):
        before = self.image.read_bytes()
        result = api.draft_placed_save(self.request())["placed"]
        item = result["broll"][0]
        self.assertEqual(item["start"], 4)
        self.assertEqual(item["source_start"], 0)
        self.assertEqual(item["focus_x"], .8)
        self.assertEqual(self.image.read_bytes(), before)
        self.assertNotEqual(item["file"], str(self.image))
        self.assertEqual(Path(item["file"]).read_bytes(), before)
        self.assertEqual(item["source_ref"]["page"], "https://example.org/source")
        self.assertEqual(result["music"]["volume"], .2)
        loaded = api.draft_placed(str(self.folder))
        self.assertEqual(loaded["subs"][0]["id"], "0")
        self.assertEqual(loaded["assets"][0]["asset_id"], item["asset_id"])

    def test_invalid_plan_leaves_placed_and_catalog_unchanged(self):
        api.draft_placed_save(self.request())
        files = [self.folder / name for name in ("clipkit_placed.json", "clipkit_insert_assets.json")]
        before = {file: file.read_bytes() for file in files}
        second = self.folder.parent / "second.png"
        Image.new("RGB", (480, 320), "red").save(second)
        with self.assertRaises(HTTPException):
            api.draft_placed_save(self.request(file=str(second), dur=20))
        self.assertEqual({file: file.read_bytes() for file in files}, before)
        self.assertTrue(second.is_file())

    def test_nonfinite_timing_is_rejected_at_api_boundary(self):
        for field in ("start", "dur", "source_start", "focus_x", "focus_y"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.request(**{field: math.inf})

    def test_generic_ai_import_cannot_accidentally_apply_insert_answer(self):
        answer = json.dumps({"operation": "inserts"})
        with self.assertRaises(HTTPException) as caught:
            api.web_ai_import(api.WebAiRequest(step="clip", target=str(self.folder), answer=answer))
        self.assertIn("ตรวจแผน", caught.exception.detail)

    def test_search_keeps_repeated_opportunities_and_never_downloads_first_hit(self):
        import free_stock
        self.fixture.write("clipkit_punch.json", {"cue:0": [["normal", "keyword", "coffee"]],
                                                  "cue:1": [["next", "", "coffee"]]})
        with patch.object(free_stock, "search", return_value={"items": [{"id": "first"}]}) as search, \
             patch.object(free_stock, "download") as download:
            result = api.broll_fill(str(self.folder))
        self.assertEqual(result["queries"], 2)
        self.assertEqual(len(result["suggestions"]), 2)
        self.assertEqual(search.call_count, 1)
        download.assert_not_called()
        self.assertFalse((self.folder / "clipkit_placed.json").exists())
        self.assertFalse((self.folder / "clipkit_broll.json").exists())
        self.assertTrue(result["needs_review"])


if __name__ == "__main__":
    unittest.main()
