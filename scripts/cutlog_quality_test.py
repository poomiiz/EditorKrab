"""Editorial choices must not fail because of fixed letter, duration or emphasis quotas."""
import json
import tempfile
import unittest
from pathlib import Path

import cutlog


class EditorialObservations(unittest.TestCase):
    def test_semantic_choices_and_approximate_duration_remain_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "clipkit_punch.json").write_text(json.dumps({
                "spoken": [["A complete normal phrase longer than eighteen letters", ""]]}), encoding="utf-8")
            for length, target in ((30, 30), (200, 180), (20, 30)):
                with self.subTest(length=length):
                    result = cutlog.analyze(root, target, length, 0)
                    self.assertEqual(result["problems"], [])
                    self.assertEqual(result["human_review"], "pending")
                    self.assertEqual(result["observations"]["target_seconds"], target)
            self.assertTrue(cutlog.analyze(root, None, 0, None)["problems"])


if __name__ == "__main__":
    unittest.main()
