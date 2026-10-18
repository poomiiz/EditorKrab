"""Reports never transmit diagnostics, even when an older config opted into auto reporting."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_settings as kit
import cutlog


class Reports(unittest.TestCase):
    def test_no_report_upload_or_automatic_persistence(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(kit, "KIT", Path(folder)), \
             patch.object(kit, "_read_config", return_value={"auto_bug_report": True}), \
             patch("urllib.request.urlopen", side_effect=AssertionError("network report attempted")):
            self.assertEqual(kit.report_bug("private error", "?path=private", ["private log"], True),
                             {"sent": False, "skipped": True})
            result = kit.report_bug("my description", "?path=private", ["private log"])
            url = urlsplit(result["issue_url"])
            self.assertEqual((url.scheme, url.netloc, url.path),
                             ("https", "github.com", "/poomiiz/EditorKrab/issues/new"))
            self.assertEqual(parse_qs(url.query), {"title": ["[bug] EditorKrab"], "body": ["my description"]})
            self.assertFalse(result["sent"])
            self.assertEqual(list(Path(folder).iterdir()), [])
            self.assertIn("disabled", cutlog._sheet({"text": "private transcript", "video": "private.mp4"}))


if __name__ == "__main__":
    unittest.main()
