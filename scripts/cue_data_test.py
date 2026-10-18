"""Occurrence sidecars retain independent data and reject ambiguous legacy clocks."""
import json
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
        import cue_data


class CueDataTests(unittest.TestCase):
    def test_merge_and_split_sources_keep_original_occurrence_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            cue_data.record_sources(folder, {"a": ["a", "b"]})
            cue_data.record_sources(folder, {"a": ["a", "c"]})
            cue_data.record_sources(folder, {"child1": ["a"], "child2": ["a"]})
            data = json.loads((Path(folder) / "clipkit_cues.json").read_text(encoding="utf-8"))
            self.assertEqual(data["cue:a"]["source_cues"], ["a", "b", "c"])
            self.assertEqual(data["cue:child1"], data["cue:child2"])
            self.assertEqual(data["cue:child1"]["source_cues"], ["a", "b", "c"])
    def test_duplicate_wording_keeps_two_independent_clocks(self):
        rows = [{"id": "a", "text": "same"}, {"id": "b", "text": "same"}]
        clocks = {"cue:a": [[0, 1, "same"]], "cue:b": [[2, 3, "same"]]}
        with tempfile.TemporaryDirectory() as folder:
            cue_data.save(folder, "words.json", clocks)
            self.assertEqual(cue_data.load(folder, "words.json", rows), clocks)
            self.assertEqual(cue_data.select(rows, "same", "b"), rows[1])
            with self.assertRaises(cue_data.VideoEditError):
                cue_data.select(rows, "same")

    def test_unique_legacy_read_is_nonmutating_and_first_write_keeps_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "words.json"
            original = json.dumps({"unique": [[0, 1, "unique"]]})
            file.write_text(original, encoding="utf-8")
            data = cue_data.load(folder, "words.json", [{"id": "a", "text": "unique"}])
            self.assertEqual(file.read_text(encoding="utf-8"), original)
            self.assertEqual(list(Path(folder).iterdir()), [file])
            cue_data.save(folder, "words.json", data)
            self.assertEqual(json.loads(file.read_text(encoding="utf-8")), {"cue:a": [[0, 1, "unique"]]})
            self.assertEqual(Path(str(file) + ".legacy.bak").read_text(encoding="utf-8"), original)

    def test_ambiguous_legacy_or_stale_selection_never_invents_identity(self):
        rows = [{"id": "a", "text": "same"}, {"id": "b", "text": "same"}]
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "words.json"
            file.write_text('{"same": [[0,1,"same"]]}', encoding="utf-8")
            original = file.read_bytes()
            with self.assertRaises(cue_data.VideoEditError):
                cue_data.load(folder, "words.json", rows)
            self.assertEqual(file.read_bytes(), original)
            with self.assertRaises(cue_data.VideoEditError):
                cue_data.select(rows, "old text", "a")


if __name__ == "__main__":
    unittest.main()
