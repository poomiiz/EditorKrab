"""Motion owns its interval in FFmpeg, browser composition and editable CapCut output."""
import json
import os
import re
import subprocess
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
import hf_build
import kitconfig
import motion_plan
import host_platform
import jobstore
from capcut_font_test import CapCutFontTests


class MotionHandoffTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        config = Path(folder) / "config.json"
        config.write_text("{}", encoding="utf-8")
        self.enterContext(patch.object(kitconfig, "_CANDIDATES", [config]))
        self.enterContext(patch.object(host_platform,'capcut_running',return_value=False))
        self.enterContext(patch.object(jobstore,'STATE',Path(folder)/'state'))

    def test_cue_motion_uses_real_ratio_geometry_and_rejects_overflow(self):
        import kit_settings
        import video_editor
        for W, H in ((1080, 1920), (1920, 1080), (1080, 1080)):
            with self.subTest(canvas=(W, H)), tempfile.TemporaryDirectory() as folder:
                (Path(folder)/'draft_content.json').write_text('{"materials":{"videos":[],"texts":[]}}')
                (Path(folder)/'fixture.mov').write_bytes(b'synthetic renderer fixture')
                look = render.pair_look({}, W, H)
                look.update(fixed_size=True, font=str(render.DEFAULT_FONT))
                phrase = {"start": 0, "end": 2, "hit": 1, "lead": "normal", "punch": "key",
                          "line": "normal key", "cue_id": "cue", "opts": {}, "k": 0}
                timeline = {"style": {}, "width": W, "height": H,"look":"pair","duration":2, "phrases": [phrase], "hook": [], "motions": []}
                req = video_editor.OverlayRequest(path=folder, template="sentence-pair", start=0, end=2,
                    text="normal key", cue_id="cue", params={"pairs": [[0, 2, "normal", "key", 1]]})
                with patch.object(render, "render_draft", return_value=timeline), \
                        patch.object(render, "pair_look", return_value=look), \
                        patch.object(kit_settings, "make_motion", side_effect=lambda body:{"file":str(Path(folder)/'fixture.mov'), "recipe":
                            motion_plan.prepare_template(body.template,body.params,width=body.width,height=body.height)}) as make:
                    video_editor.draft_overlay_add(req)
                    params = make.call_args.args[0].params
                    expected = render.pair_events_from_timeline(timeline)
                    self.assertEqual((params["width"], params["height"]), (W, H))
                    self.assertEqual(params["subtitle_events"], expected)
                    self.assertEqual(next(e["start"] for e in expected if e["role"] == "emphasis"), 1)
                    before = (Path(folder) / "clipkit_overlays.json").read_bytes()
                    req.params["pairs"][0][4] = .2
                    with self.assertRaisesRegex(video_editor.HTTPException, "Timeline"):
                        video_editor.draft_overlay_add(req)
                    req.params = None
                    phrase["lead"] = "overflow " * 100
                    phrase["line"] = req.text = phrase["lead"] + phrase["punch"]
                    with self.assertRaisesRegex(video_editor.HTTPException, "show"):
                        video_editor.draft_overlay_add(req)
                    self.assertEqual(make.call_count, 1)
                    self.assertEqual((Path(folder) / "clipkit_overlays.json").read_bytes(), before)

    def test_text_is_preserved_on_both_sides_and_boundaries(self):
        motion = [{"start": 3, "duration": 3}]
        self.assertEqual(render.visible_spans(2, 7, motion), [(2, 3), (6, 7)])
        self.assertEqual(render.visible_spans(3, 6, motion), [])
        self.assertEqual(render.visible_spans(0, 3, motion), [(0, 3)])
        self.assertEqual(render.visible_spans(6, 9, motion), [(6, 9)])
        self.assertEqual(render.visible_spans(1, 9, motion + [{"start": 7, "duration": 1}]), [(1, 3), (6, 7), (8, 9)])

    def test_invalid_motion_fails_with_actionable_error(self):
        for motion in ([{}], [{"start": 0, "duration": float("nan")}],
                       [{"start": 0, "duration": 1}, {"start": "1", "duration": 1}],
                       [{"start": -1, "duration": 1}], [{"start": 0, "duration": 12}]):
            with self.subTest(motion=motion), self.assertRaises(render.VideoEditError):
                render.check_motions(motion, 10)
        with tempfile.NamedTemporaryFile() as source, patch.object(render, "probe", return_value={"duration": 1}):
            with self.assertRaisesRegex(render.VideoEditError, "exceeds its media"):
                render.check_motions([{"start": 0, "duration": 2, "file": source.name}], 10)

    def test_pair_ass_never_covers_motion(self):
        events = render._pair("สวัสดีครับ", [[0, 2, "สวัสดี"], [2, 4, "ครับ"]], 0, 4, 1080, 1920, "Kanit", render.DEFAULT_FONT,
                              render.pair_look({}), picked=[["สวัสดี", "ครับ"]],
                              held={"white": "", "motions": [{"start": 1, "duration": 1}]})
        def seconds(t):
            h, m, s = map(float, t.split(":"))
            return h * 3600 + m * 60 + s
        self.assertTrue(events)
        for line in "\n".join(events).splitlines():
            a, b = map(seconds, line.split(",")[1:3])
            self.assertFalse(a < 2 and b > 1, line)

    def timeline(self, motion):
        return {"style": {}, "zoom": [], "width": 1080, "height": 1920, "duration": 3,
                "look": "plain", "phrases": [{"start": 0, "end": 3, "lead": "ทดสอบ", "punch": ""}],
                "hook": [], "broll": [], "motions": [motion], "sfx": {"on": False}, "music": {},
                "fit": {"x": 0, "y": 0, "w": 1080, "h": 1920}, "source": "fixture.mp4",
                "segments": [], "progress": False, "emoji": [], "pops": []}

    def test_hf_short_span_does_not_extend_into_motion(self):
        with tempfile.TemporaryDirectory() as folder:
            motion = {"start": 0.02, "duration": 2, "file": "fixture.mov"}
            with patch.object(render, "render_draft", return_value=self.timeline(motion)), \
                 patch.object(hf_build, "_proxy", return_value="fixture.mp4"), \
                 patch.object(hf_build, "_motion", return_value="motion.webm"):
                page = hf_build.build(folder).read_text(encoding="utf-8")
            self.assertIn('src="motion.webm"', page)
            spans = re.findall(r'class="clip txt" data-start="([\d.]+)" data-duration="([\d.]+)"', page)
            self.assertEqual(len(spans), 2)
            for a, duration in spans:
                a, b = float(a), float(a) + float(duration)
                self.assertFalse(a < 2.02 and b > 0.02)
            self.assertNotIn('"#"', page)

    def test_capcut_preserves_mov_and_text_windows(self):
        harness = CapCutFontTests()
        harness.setUp()
        try:
            with tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "motion.mov"
                source.write_bytes(b"fixture; probe mocked")
                motion = {"start": 1, "duration": 1, "file": str(source)}
                with patch.object(render.video_edit, "probe", return_value={"duration": 1, "width": 1080, "height": 1920}):
                    _, (output, _) = harness.run_build(Path(folder), timeline=self.timeline(motion))
                draft = json.loads((output / "draft_content.json").read_text(encoding="utf-8"))
                self.assertIn(source.as_posix(), [m.get("path") for m in draft["materials"]["videos"]])
                windows = [s["target_timerange"] for t in draft["tracks"] if t["type"] == "text" for s in t["segments"]]
                self.assertEqual(len(windows), 2)
                for w in windows:
                    self.assertFalse(w["start"] < 2_000_000 and w["start"] + w["duration"] > 1_000_000)
        finally:
            harness.doCleanups()

    def test_transparent_mov_becomes_browser_video(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "alpha.mov"
            subprocess.run([render.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                            "color=black@0:s=64x64:r=10:d=0.2,format=rgba,drawbox=x=20:y=20:w=20:h=20:color=red@1:t=fill:replace=1",
                            "-c:v", "qtrle", str(source)], check=True)
            result = hf_build._motion(Path(folder), str(source))
            self.assertTrue((Path(folder) / result).is_file())
            # Force libvpx decoding: FFmpeg's native VP9 decoder discards the alpha plane.
            alpha = subprocess.check_output([render.FFMPEG, "-v", "error", "-c:v", "libvpx-vp9", "-i", str(Path(folder) / result),
                                             "-frames:v", "1", "-vf", "alphaextract", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
            self.assertEqual((min(alpha), max(alpha)), (0, 255))

    def test_concurrent_verified_media_pass_spatial_gate_and_keep_independent_export_tracks(self):
        own={'mode':'decorate','targets':[],'caption_policy':'preserve'}
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);motions=[]
            for index,bounds in enumerate(([100,300,400,500],[600,900,900,1100])):
                file=base/f'motion{index}.mov';file.write_bytes(b'owned synthetic probe fixture')
                motions.append({'start':1,'duration':1,'file':str(file),'bounds':bounds,'ownership':own,'recipe':{'version':1}})
            with patch.object(render,'probe',return_value={'duration':1}):
                render.check_motions(motions,3)
                with self.assertRaisesRegex(render.VideoEditError,'overlap'):
                    render.check_motions([motions[0],{**motions[1],'bounds':motions[0]['bounds']}],3)
                with self.assertRaisesRegex(render.VideoEditError,'verified recipes'):
                    render.check_motions([motions[0],{k:v for k,v in motions[1].items() if k!='recipe'}],3)
            # Serialization fixture only; full stored-recipe verification has separate regressions.
            for motion in motions:
                motion.pop('recipe')
            timeline=self.timeline(motions[0]);timeline['motions']=motions;timeline['phrases']=[]
            with patch.object(render,'render_draft',return_value=timeline),patch.object(hf_build,'_proxy',return_value='fixture.mp4'),patch.object(hf_build,'_motion',side_effect=lambda out,file:Path(file).name):
                page=hf_build.build(folder).read_text(encoding='utf-8')
            self.assertEqual(re.findall(r'<video[^>]*data-track-index="(8|9)"[^>]*>',page),['8','9'])
            harness=CapCutFontTests();harness.setUp()
            try:
                with patch.object(render.video_edit,'probe',return_value={'duration':1,'width':1080,'height':1920}):
                    _,(output,_)=harness.run_build(base,timeline=timeline)
                draft=json.loads((output/'draft_content.json').read_text(encoding='utf-8'))
                tracks=[t for t in draft['tracks'] if t['type']=='video' and t['segments'] and t['segments'][0].get('render_index')==2]
                self.assertEqual(len(tracks),2)
                self.assertTrue(all(len(t['segments'])==1 and t['segments'][0]['target_timerange']=={'start':1000000,'duration':1000000} for t in tracks))
            finally:
                harness.doCleanups()


if __name__ == "__main__":
    unittest.main()
