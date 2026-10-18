"""A learned preset uses the displayed size, including the editor's scale."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preset_from_capcut import learn


class PresetScale(unittest.TestCase):
    def test_library_roundtrip_keeps_layout_without_sharing_local_font_path(self):
        from urllib.parse import parse_qs, urlparse
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            config = p / "config.json"
            config.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
                import style_lab
            self.project(p, {"normal": {}, "emphasis": {}})
            style = learn(p)
            style["font"] = "C:/private/fonts/reference.ttf"
            lab = p / "lab"
            lab.mkdir()
            (lab / "style.json").write_text(json.dumps(style), encoding="utf-8")
            (lab / "source.json").write_text('{"source":"private-project"}', encoding="utf-8")
            (lab / "preview.gif").touch()
            presets = p / "presets"
            presets.mkdir()
            with patch.object(style_lab, "PRESETS", presets), patch.object(style_lab, "PARTS", p / "parts"), \
                    patch.object(style_lab, "PREVIEWS", p / "previews"), patch.object(style_lab, "_render"), \
                    patch.object(style_lab, "_repo", return_value="example/project"):
                style_lab.save(lab, "trial", parts=["main"])
                saved = json.loads((presets / "my-trial.json").read_text(encoding="utf-8"))
                self.assertTrue(saved["fixed_size"])
                self.assertEqual(saved["font"], style["font"])
                body = parse_qs(urlparse(style_lab.share("my-trial")["url"]).query)["body"][0]
                self.assertNotIn(style["font"], body)
                self.assertIn('"fixed_size": true', body)

    def project(self, folder, scale):
        texts, segments = [], []
        for role, color, y in (("normal", [1, 1, 1], -.366), ("emphasis", [0, 0, .5], -.484759)):
            texts.append({"id": role, "content": json.dumps({"styles": [{"size": 25,
                "fill": {"content": {"solid": {"color": color}}}}]})})
            segments.append({"material_id": role, "clip": {"scale": scale[role], "transform": {"y": y}}})
        (folder / "draft_content.json").write_text(json.dumps({"materials": {"texts": texts},
            "tracks": [{"type": "text", "segments": segments}]}), encoding="utf-8")

    def test_measured_scale_and_position_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            self.project(p, {"normal": {"x": .908, "y": .908},
                "emphasis": {"x": .8854454897620365, "y": .8854454897620365}})
            result = learn(p)
            self.assertEqual(result["normal"]["size"], 22.7)
            self.assertEqual(result["emphasis"]["size"], 22.136)
            self.assertEqual(result["emphasis"]["y"], -.484759)

    def test_missing_scale_is_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            self.project(p, {"normal": {}, "emphasis": {}})
            self.assertEqual(learn(p)["normal"]["size"], 25)

    def test_unrepresentable_scale_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            for scale in ({"x": 1, "y": .5}, {"x": 0, "y": 0}, {"x": float("nan"), "y": 1}):
                self.project(p, {"normal": scale, "emphasis": {}})
                with self.assertRaises(ValueError):
                    learn(p)


if __name__ == "__main__":
    unittest.main()
