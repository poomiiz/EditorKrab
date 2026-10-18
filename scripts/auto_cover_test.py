"""Long Thai pilot titles reach the cover renderer without losing words."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import kit_settings
with tempfile.TemporaryDirectory() as config_folder:
    config = Path(config_folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import video_edit
        import video_editor


class AutoCover(unittest.TestCase):
    def test_long_title_reuses_layout_and_persists_cover(self):
        title = "สอน AI ให้ตอบเป็นภาษาของเรา"
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / ("trial - 1 " + title)
            folder.mkdir()
            (folder / "clipkit.json").write_text(json.dumps({"raw": "source.mp4", "start": 36, "end": 135}))
            (folder / "clipkit_style.json").write_text(json.dumps({"anim": "pair"}))
            with patch.object(video_edit, "find_focus", return_value={"best_at": 50}), \
                 patch.object(kit_settings, "make_cover", return_value={"file": "cover.png"}) as make:
                self.assertEqual(video_editor.auto_cover(str(folder)), "cover.png")
            request = make.call_args.args[0]
            self.assertEqual("".join((request.l1 + request.l2).split()), "".join(title.split()))
            self.assertEqual(request.at, 50)
            self.assertEqual(json.loads((folder / "clipkit_style.json").read_text()), {"anim": "pair", "cover": "cover.png"})


if __name__ == "__main__":
    unittest.main()
