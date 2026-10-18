"""Public CapCut templates keep usable fonts in both material and nested text JSON."""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
with tempfile.TemporaryDirectory() as import_folder:
    config = Path(import_folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import capcut_build as build

ROLES = ("white", "orange", "caption", "title")


def font_paths(templates):
    for role in ROLES:
        material = templates[role]["mat"]
        yield role, material["font_path"]
        for style in json.loads(material["content"])["styles"]:
            yield role, style["font"]["path"]


class CapCutFontTests(unittest.TestCase):
    def test_fitted_native_text_leaves_room_for_outline_and_rounding(self):
        from PIL import ImageFont
        templates = json.loads(build.TEMPLATES.read_text(encoding="utf-8"))
        build._T.update(templates)
        mat = build._T["white"]["mat"]
        body = json.loads(mat["content"])
        body["styles"][0]["font"]["path"] = str(build.render.DEFAULT_FONT)
        mat["content"] = json.dumps(body)
        draft = {"materials": {"texts": []}}
        segments = build._texts(draft, "white", "หรือการตอบเอกสารต่างๆ", 0, 2, -.3, 22.7,
                                [1, 1, 1], [0, 0, 0], 1080, 1920, {})
        self.assertEqual(len(segments), 1)
        written = draft["materials"]["texts"][0]
        px = written["font_size"] * build.render.CAPCUT_PX
        font = ImageFont.truetype(str(build.render.DEFAULT_FONT), round(px))
        self.assertLess(font.getlength("หรือการตอบเอกสารต่างๆ"), 1080 * build.render.SAFE_W)

    def setUp(self):
        self.original_templates = copy.deepcopy(build._T)
        build._T.clear()
        self.addCleanup(lambda: build._T.update(self.original_templates))
        self.addCleanup(build._T.clear)

    def make_project(self, base, duration):
        project = base / "test project"
        project.mkdir()
        source = base / "source.mp4"
        source.touch()
        span = {"start": 0, "duration": round(duration * build.US)}
        draft = {"id": "test", "duration": span["duration"],
                 "canvas_config": {"width": 1080, "height": 1920},
                 "materials": {"videos": [{"id": "video", "path": str(source)}], "texts": []},
                 "tracks": [{"type": "video", "segments": [{"material_id": "video",
                             "target_timerange": span, "source_timerange": span,
                             "clip": {"scale": {"x": 1.0, "y": 1.0}}, "extra_material_refs": []}]}]}
        (project / "draft_content.json").write_text(json.dumps(draft), encoding="utf-8")
        return project

    def run_build(self, base, *, templates=None, installed=False, default_font=None, timeline=None):
        timeline = timeline or {"style": {}, "zoom": [], "width": 1080, "height": 1920, "look": "plain",
                    "phrases": [], "hook": [], "broll": [], "sfx": {"on": False}, "music": {}}
        duration = float(timeline.get("duration", 1.0))
        project = self.make_project(base, duration)
        resources, fonts, data = base / "resources", base / "fonts", base / "user data"
        for folder in (resources, fonts, data):
            folder.mkdir()
        if installed:
            source = json.loads(build.TEMPLATES.read_text(encoding="utf-8"))
            for _, name in font_paths(source):
                if name.startswith("{CAPCUT_APP}"):
                    target = resources / name.removeprefix("{CAPCUT_APP}/Resources/")
                else:
                    target = fonts / Path(name).name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(build.render.DEFAULT_FONT, target)
        config = {"fonts_dir": str(fonts), "capcut_resources": str(resources),
                  "capcut_user_data": str(data)}
        def fixture_probe(file):
            if Path(file).resolve() == (base / "source.mp4").resolve():
                return {"duration": duration}
            return build.video_edit.probe(file)
        with ExitStack() as stack:
            stack.enter_context(patch.object(build.host_platform, "capcut_running", return_value=False))
            stack.enter_context(patch.object(build.draft_quality, "probe", side_effect=fixture_probe))
            stack.enter_context(patch.object(build.host_platform, "is_macos", return_value=True))
            stack.enter_context(patch.object(build.capcut_edit.kitconfig, "_load", return_value=config))
            # No platform font scan is needed: test real fixture font files and public template resolution.
            stack.enter_context(patch.object(build.host_platform, "find_font", side_effect=lambda name, cfg:
                                             fonts / name if (fonts / name).is_file() else None))
            stack.enter_context(patch.object(build.render, "render_draft", return_value=timeline))
            stack.enter_context(patch.object(build.capcut_edit, "_log_change"))
            if templates is not None:
                fixture = base / "templates.json"
                fixture.write_text(json.dumps(templates, ensure_ascii=False), encoding="utf-8")
                stack.enter_context(patch.object(build, "TEMPLATES", fixture))
                stack.enter_context(patch.object(build, "_machine_paths", side_effect=lambda text: text))
            if default_font is not None:
                stack.enter_context(patch.object(build.render, "DEFAULT_FONT", default_font))
            result = build.build(str(project))
        return project, result

    def assert_all_fonts(self, expected):
        for role, font in font_paths(build._T):
            with self.subTest(role=role, font=font):
                self.assertEqual(font, expected)
                self.assertTrue(Path(font).is_file())
        # The actual text writer carries both material and nested style paths into its output.
        draft = {"materials": {"texts": []}}
        for role in ROLES:
            build._text(draft, role, "ทดสอบ font", 0, 1, 0, 16)
        for material in draft["materials"]["texts"]:
            self.assertEqual(material["font_path"], expected)
            self.assertEqual(json.loads(material["content"])["styles"][0]["font"]["path"], expected)
            self.assertEqual(material["text_size"], material["font_size"])
            self.assertEqual(material["font_title"], build.render._font(expected)[0])
            self.assertEqual(material["font_name"], build.render._font(expected)[0])

    def test_public_templates_missing_fonts_fallback_in_every_material_and_style(self):
        with tempfile.TemporaryDirectory() as folder:
            project, (output, warnings) = self.run_build(Path(folder))
            self.assert_all_fonts(build.render.DEFAULT_FONT.as_posix())
            self.assertEqual(len(warnings), 3)
            for name in ("DB Heavent Bd v3.2.1.ttf", "DB HelvethaicaMon X Bd v3.2.ttf", "en.ttf"):
                self.assertTrue(any(name in warning and "using Kanit" in warning for warning in warnings))
            self.assertTrue((output / "draft_content.json").is_file())
            self.assertEqual(json.loads((project / "draft_content.json").read_text())["id"], "test")

    def test_installed_fonts_are_preserved_without_warnings(self):
        with tempfile.TemporaryDirectory(prefix="capcut fonts ' ") as folder:
            _, (_, warnings) = self.run_build(Path(folder), installed=True)
            self.assertEqual(warnings, [])
            self.assertTrue(all(Path(font).is_file() for _, font in font_paths(build._T)))
            self.assertNotIn(build.render.DEFAULT_FONT.as_posix(), [font for _, font in font_paths(build._T)])
            source = json.loads(build.TEMPLATES.read_text(encoding="utf-8"))
            self.assertEqual([(role, Path(font).name) for role, font in font_paths(build._T)],
                             [(role, Path(font).name) for role, font in font_paths(source)])

    def test_missing_paths_with_json_quotes_and_backslashes_are_replaced_structurally(self):
        templates = json.loads(build.TEMPLATES.read_text(encoding="utf-8"))
        missing = 'missing "Mac fonts"/Thai\\font.ttf'
        for role in ROLES:
            material = templates[role]["mat"]
            material["font_path"] = missing
            body = json.loads(material["content"])
            body["text"] = missing  # ordinary text must not change just because it equals a font path
            for style in body["styles"]:
                style["font"]["path"] = missing
            material["content"] = json.dumps(body, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as folder:
            _, (_, warnings) = self.run_build(Path(folder), templates=templates)
            self.assert_all_fonts(build.render.DEFAULT_FONT.as_posix())
            self.assertEqual(len(warnings), 1)
            for role in ROLES:
                self.assertEqual(json.loads(build._T[role]["mat"]["content"])["text"], missing)

    def test_empty_font_paths_use_kanit_with_explicit_warning(self):
        templates = json.loads(build.TEMPLATES.read_text(encoding="utf-8"))
        for role in ROLES:
            material = templates[role]["mat"]
            material["font_path"] = ""
            body = json.loads(material["content"])
            for style in body["styles"]:
                style["font"]["path"] = ""
            material["content"] = json.dumps(body, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as folder:
            _, (_, warnings) = self.run_build(Path(folder), templates=templates)
            self.assert_all_fonts(build.render.DEFAULT_FONT.as_posix())
            self.assertEqual(len(warnings), 1)
            self.assertIn("empty", warnings[0])
            self.assertIn("using Kanit", warnings[0])

    def test_missing_kanit_fails_before_copying_project(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with self.assertRaisesRegex(build.VideoEditError, "font file missing"):
                self.run_build(base, default_font=base / "missing Kanit.ttf")
            self.assertTrue((base / "test project" / "draft_content.json").is_file())
            self.assertFalse(any(base.glob("test project · CapCut*")))

    def test_invalid_font_path_fails_explicitly_before_copying_project(self):
        templates = json.loads(build.TEMPLATES.read_text(encoding="utf-8"))
        templates["title"]["mat"]["font_path"] = ["not", "a path"]
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with self.assertRaisesRegex(build.VideoEditError, "font path must be a string"):
                self.run_build(base, templates=templates)
            self.assertFalse(any(base.glob("test project · CapCut*")))


if __name__ == "__main__":
    unittest.main()
