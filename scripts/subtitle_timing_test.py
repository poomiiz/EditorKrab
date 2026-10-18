"""Emphasis must follow the spoken word, without re-timing a fitted phrase by letter count."""
import hashlib
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
with tempfile.TemporaryDirectory() as import_folder:
    config = Path(import_folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import render
import draft_quality
import hf_build
import video_edit
sys.path.insert(0, str(ROOT / "scripts"))
import capcut_font_test


class SubtitleTimingTests(unittest.TestCase):
    def test_fixed_subtitles_keep_size_and_reject_overflow(self):
        for text in ("สั้น", "ข้อความปกติ"):
            result = render.one_line(text, render.DEFAULT_FONT, 100, 900, 1, 3, fixed=True)
            self.assertEqual(result, [(text, 1, 3, 100)])
        with self.assertRaisesRegex(video_edit.VideoEditError, "show"):
            render.one_line("ข้อความยาว" * 20, render.DEFAULT_FONT, 100, 900, 1, 3, fixed=True)

    def test_fixed_preset_preserves_reference_centers(self):
        with tempfile.TemporaryDirectory() as tmp:
            preset = json.loads((render.PRESETS / "default.json").read_text(encoding="utf-8"))
            preset.update(fixed_size=True)
            preset["normal"].update(size=22.7, y=-.366)
            preset["emphasis"].update(size=22.136, y=-.484759)
            (Path(tmp) / "reference.json").write_text(json.dumps(preset), encoding="utf-8")
            with patch.object(render, "PRESETS", Path(tmp)):
                look = render.pair_look({"preset": "reference"}, 1080, 1920)
            self.assertEqual(look["lead"][0], 22.7)
            self.assertEqual(look["punch"][0], 22.136)
            # A safe-zone translation moves the complete pair, preserving the reference spacing.
            self.assertAlmostEqual(look["lead"][1] - look["punch"][1], -.366 + .484759)

    def test_size_tracks_canvas_ratio_for_all_outputs(self):
        relative = []
        for width, height in ((1080, 1920), (1920, 1080), (1080, 1080)):
            look = render.pair_look({}, width, height)
            relative.append(look["lead"][0] * render.CAPCUT_PX * min(width, height) / 1080 / height)
        self.assertAlmostEqual(relative[0], relative[1])
        self.assertAlmostEqual(relative[0], relative[2])
    def test_both_rows_hold_through_pause_until_replacement(self):
        events = render._pair("normal keyword", [[0, .3, "normal "], [.3, .5, "keyword"]],
                              0, 4, 1080, 1920, "Kanit", render.DEFAULT_FONT, render.pair_look({}),
                              picked=[["normal", "keyword"]])
        self.assertTrue(events)
        for event in events:
            self.assertEqual(event.split(",")[2], "0:00:04.00")
    def test_pair_rows_fit_without_overlap_on_both_canvases(self):
        for width, height in ((1920, 1080), (1080, 1920)):
            look = render.pair_look({}, width, height)
            centers = [height / 2 - look[r][1] * height / 2 for r in ("lead", "punch")]
            sizes = [look[r][0] * render.CAPCUT_PX * min(width, height) / 1080 * render.LINE_H for r in ("lead", "punch")]
            self.assertGreater(centers[1] - sizes[1] / 2, centers[0] + sizes[0] / 2)
            self.assertLessEqual(centers[1] + sizes[1] / 2, height * render.SAFE_BOTTOM + 0.001)

    def test_split_inside_one_timing_token_is_refused(self):
        with self.assertRaisesRegex(render.VideoEditError, "ละเอียดกว่า"):
            render._pair("abcd", [[0, 2, "abcd"]], 0, 2, 1080, 1920, "Kanit", render.DEFAULT_FONT,
                         render.pair_look({}), picked=[["a", "b"], ["c", "d"]])
    def test_late_keyword_uses_its_actual_word_start(self):
        marks = []
        render._pair("wait for the keyword", [[0, .4, "wait "], [.4, .7, "for "], [.7, 1, "the "], [1.8, 2.2, "keyword"]],
                     5, 7.2, 1080, 1920, "Kanit", render.DEFAULT_FONT, render.pair_look({}),
                     picked=[["wait for the", "keyword"]], marks=marks)
        phrase = next(m for m in marks if m[0] == "phrase")
        self.assertAlmostEqual(phrase[9], 6.8, places=2)
        self.assertIn(("pop", 6.8), marks)

    def test_spelling_change_before_keyword_does_not_move_its_onset(self):
        marks = []
        render._pair("AI keyword", [[0, 1, "artificial intelligence "], [1.8, 2.2, "keyword"]],
                     5, 7.2, 1080, 1920, "Kanit", render.DEFAULT_FONT, render.pair_look({}),
                     picked=[["AI", "keyword"]], marks=marks)
        self.assertIn(("pop", 6.8), marks)

    def test_normal_text_remains_during_keyword_and_ends_with_it(self):
        events = render._pair("normal keyword", [[0, 1.8, "normal "], [1.8, 2.5, "keyword"]],
                              0, 2.5, 1080, 1920, "Kanit", render.DEFAULT_FONT, render.pair_look({}),
                              picked=[["normal", "keyword"]])
        normal = next(e for e in events if e.endswith("normal"))
        keyword = next(e for e in events if e.endswith("keyword"))
        self.assertEqual(normal.split(",")[1:3], ["0:00:00.00", "0:00:02.50"])
        self.assertEqual(keyword.split(",")[1:3], ["0:00:01.80", "0:00:02.50"])

    def test_unselected_content_stays_plain(self):
        marks = []
        render._pair("ข้อความปกติที่ยังไม่ได้เลือกคำเน้น", None, 0, 3, 1080, 1920, "Kanit", render.DEFAULT_FONT,
                     render.pair_look({}), marks=marks)
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0][4], "")

    def test_keyword_without_word_times_is_refused(self):
        with self.assertRaisesRegex(render.VideoEditError, "เวลาระดับคำ"):
            render._pair("normal keyword", None, 0, 3, 1080, 1920, "Kanit", render.DEFAULT_FONT,
                         render.pair_look({}), picked=[["normal", "keyword"]])

    def test_pair_fitting_does_not_split_into_letter_timed_pieces(self):
        parts = render.one_line("a long lead that has already been timed to speech", render.DEFAULT_FONT, 140, 400, 0, 3, one=True)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0][1:3], (0, 3))
        self.assertLess(parts[0][3], 140)

    def test_real_dry_timing_reaches_both_hyperframes_and_capcut(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.json"
            config.write_text('{"render_encoder":"libx264"}')
            self.enterContext(patch.object(render.video_edit.kitconfig, "_CANDIDATES", [config]))
            source = root / "source.mp4"
            source.touch()
            project = root / "source project"
            project.mkdir()
            clip = {"scale": {"x": 1, "y": 1}, "transform": {"x": 0, "y": 0}}
            timing = {"start": 0, "duration": 2_500_000}
            draft = {"canvas_config": {"width": 1080, "height": 1920}, "materials": {
                "videos": [{"id": "v", "path": str(source)}],
                "texts": [{"id": "t", "font_size": 15, "content": json.dumps({"text": "normal keyword", "styles": []})}]},
                "tracks": [{"type": "video", "segments": [{"material_id": "v", "clip": clip,
                    "target_timerange": timing, "source_timerange": timing}]},
                    {"type": "text", "segments": [{"id": "subtitle", "material_id": "t", "clip": clip, "target_timerange": timing}]}]}
            (project / "draft_content.json").write_text(json.dumps(draft))
            (project / "clipkit_words.json").write_text(json.dumps({"normal keyword": [[0, 1.8, "normal "], [1.8, 2.5, "keyword"]]}))
            (project / "clipkit_punch.json").write_text(json.dumps({"normal keyword": [["normal", "keyword"]]}))
            (project / "clipkit_style.json").write_text('{"anim":"pair","music":"","sfx":false}')
            metadata = {"width": 1080, "height": 1920, "duration": 2.5, "fps": 30}
            with patch.object(render, "probe", return_value=metadata), patch.object(draft_quality, "probe", return_value=metadata):
                timeline = render.render_draft(str(project), str(root / "out"), dry=True)
            self.assertEqual(timeline["phrases"][0]["hit"], 1.8)
            with patch.object(hf_build.render, "render_draft", return_value=timeline), \
                    patch.object(hf_build, "_proxy", return_value="synthetic.mp4"):
                html = hf_build.build(str(project)).read_text(encoding="utf-8")
            windows = {text: (float(start), float(start) + float(length)) for start, length, text in re.findall(
                r'<div[^>]*class="clip subtitle-event"[^>]*data-start="([\d.]+)" data-duration="([\d.]+)"[^>]*><div[^>]*>(normal|keyword)</div>', html)}
            self.assertEqual(windows["normal"][0], 0)
            self.assertEqual(windows["keyword"][0], 1.8)
            self.assertAlmostEqual(windows["normal"][1], windows["keyword"][1], places=3)
            self.assertAlmostEqual(windows["normal"][1], 2.5, places=3)
            harness = capcut_font_test.CapCutFontTests()
            harness.setUp()
            try:
                base = root / "capcut fixture"
                base.mkdir()
                _, (output, _) = harness.run_build(base, timeline=timeline)
                built = json.loads((output / "draft_content.json").read_text(encoding="utf-8"))
                materials = {m["id"]: json.loads(m["content"])["text"] for m in built["materials"]["texts"]}
                windows = {materials[s["material_id"]]: s["target_timerange"] for track in built["tracks"] if track["type"] == "text"
                           for s in track["segments"] if materials[s["material_id"]] in {"normal", "keyword"}}
                self.assertEqual(windows["normal"]["start"], 0)
                self.assertEqual(windows["keyword"]["start"], 1_800_000)
                ends = [span["start"] + span["duration"] for span in windows.values()]
                self.assertEqual(ends, [2_500_000, 2_500_000])
            finally:
                harness.doCleanups()

    def test_project_creation_copies_existing_actual_word_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            source.touch()
            words = [[10, 11.8, "normal "], [11.8, 12.5, "keyword"]]
            video_edit.transcript_file(str(source)).write_text(json.dumps([
                {"start": 10, "end": 12.5, "text": "normal keyword", "words": words}]))
            video_edit.transcript_file(str(source)).with_suffix(".meta.json").write_text(json.dumps({
                "schema_version": 2, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}))
            template = root / "template"
            template.mkdir()
            clip = {"scale": {"x": 1, "y": 1}, "transform": {"x": 0, "y": 0}}
            draft = {"materials": {"videos": [{"id": "v"}], "texts": [{"id": "t", "content": '{"text":"old","styles":[]}'}]},
                     "tracks": [{"type": "video", "segments": [{"material_id": "v", "extra_material_refs": [], "clip": clip}]},
                                {"type": "text", "segments": [{"material_id": "t", "clip": clip}]}]}
            (template / "draft_content.json").write_text(json.dumps(draft))
            metadata = {"duration": 20, "width": 1080, "height": 1920, "vertical": True, "audio_channels": 2}
            with patch.object(video_edit, "_template_dir", return_value=template), patch.object(video_edit, "probe", return_value=metadata):
                result = video_edit.create_capcut_draft(str(source), "new project", 10, 12.5,
                    subs=[{"start": 0, "end": 2.5, "text": "normal keyword"}], drafts_root=str(root))
            clock = json.loads((Path(result["draft_path"]) / "clipkit_words.json").read_text())
            rows = capcut_font_test.build.capcut_edit.read_draft(result["draft_path"])["subtitles"]
            self.assertEqual(clock, {"cue:" + rows[0]["id"]: [[0, 1.8, "normal "], [1.8, 2.5, "keyword"]]})


if __name__ == "__main__":
    unittest.main()
