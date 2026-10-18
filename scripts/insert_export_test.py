"""Synthetic insert offsets, cover/focus, cache identity, export parity and cut clocks."""
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
with tempfile.TemporaryDirectory() as folder:
    config = Path(folder) / "config.json"
    config.write_text("{}")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import render
import capcut_build
import capcut_edit
import hf_build
import insert_geometry
import insert_plan
import jobstore
import video_editor


class InsertExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path(render.FFMPEG).is_file() and not shutil.which(render.FFMPEG):
            raise unittest.SkipTest("FFmpeg unavailable; actual insert export proof remains pending")
        cls.media = tempfile.TemporaryDirectory()
        cls.root = Path(cls.media.name)
        cls.main, cls.video, cls.image = (cls.root / name for name in ("speech.mp4", "insert.mp4", "bars.png"))
        def make(arguments):
            result = subprocess.run([render.FFMPEG, "-v", "error", "-y", *arguments], capture_output=True, timeout=60)
            if result.returncode:
                raise AssertionError(result.stderr[-1000:])
        make(["-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=3", "-f", "lavfi", "-i",
              "sine=frequency=440:duration=3", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(cls.main)])
        make(["-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=2", "-f", "lavfi", "-i",
              "color=c=blue:s=320x180:r=30:d=2", "-f", "lavfi", "-i", "sine=frequency=880:duration=4",
              "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
              "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(cls.video)])
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (900, 180), "blue")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 299, 179), fill="red"); draw.rectangle((600, 0, 899, 179), fill="green")
        image.save(cls.image)

    @classmethod
    def tearDownClass(cls):
        cls.media.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(jobstore, "STATE", self.base / "state"))
        resources, fonts, user_data = (self.base / name for name in ("resources", "fonts", "user data"))
        for folder in (resources, fonts, user_data):
            folder.mkdir()
        fixture_config = {"render_encoder": "libx264", "capcut_resources": str(resources),
                          "fonts_dir": str(fonts), "capcut_user_data": str(user_data)}
        config = self.base / "config.json"
        config.write_text(json.dumps(fixture_config), encoding="utf-8")
        self.stack.enter_context(patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}))
        self.stack.enter_context(patch.object(render.video_edit.kitconfig, "_CANDIDATES", [config]))
        self.fixture_fonts = fonts
        self.stack.enter_context(patch.object(render.video_edit.kitconfig, "simple", return_value=False))
        self.stack.enter_context(patch.object(capcut_edit, "_log_change"))
        self.stack.enter_context(patch.object(capcut_build.host_platform, "capcut_running", return_value=False))

    def project(self, name="project", W=320, H=180, ramp=False):
        project = self.base / name
        project.mkdir()
        span = {"start": 0, "duration": 3_000_000}
        clip = {"scale": {"x": 1, "y": 1}, "transform": {"x": 0, "y": 0}}
        draft = {"id": name, "duration": 3_000_000, "canvas_config": {"width": W, "height": H},
                 "materials": {"videos": [{"id": "source", "path": str(self.main)}], "texts": []},
                 "tracks": [{"type": "video", "segments": [{"id": "source", "material_id": "source", "clip": clip,
                    "extra_material_refs": [], "source_timerange": dict(span), "target_timerange": dict(span), "volume": 1}]}]}
        (project / "draft_content.json").write_text(json.dumps(draft), encoding="utf-8")
        (project / "clipkit_style.json").write_text(json.dumps({"anim": "none", "sfx": False, "music": "", "ramp": ramp}), encoding="utf-8")
        return project

    def place(self, project, source=None, **changes):
        catalog = insert_plan.register_assets(project, [source or self.video])
        item = {"asset_id": catalog["candidates"][0], "anchor": {"kind": "timeline", "start": .5},
                "reason": "Synthetic source-range test", "dur": .7, "source_start": 2, "focus_x": .5, "focus_y": .5}
        item.update(changes)
        rows = insert_plan.normalize(project, [item])
        placed = {"broll": rows, "music": {"file": "", "volume": .12}, "sfx": {"on": False, "volume": 1}}
        (project / "clipkit_placed.json").write_text(json.dumps(placed), encoding="utf-8")
        return rows

    def native(self, project, timeline):
        # Exercise the actual Mac template serializer without an installed CapCut or host font scan.
        with ExitStack() as stack:
            stack.enter_context(patch.object(render, "render_draft", return_value=timeline))
            stack.enter_context(patch.object(capcut_build.host_platform, "is_macos", return_value=True))
            stack.enter_context(patch.object(capcut_build.host_platform, "find_font", side_effect=lambda name, cfg:
                                self.fixture_fonts / name if (self.fixture_fonts / name).is_file() else None))
            serialize = stack.enter_context(patch.object(capcut_build.host_platform, "mac_template_paths",
                                            wraps=capcut_build.host_platform.mac_template_paths))
            folder, _ = capcut_build.build(str(project))
        serialize.assert_called_once()
        self.assertEqual(serialize.call_args.args[1]["capcut_resources"], str(self.base / "resources"))
        draft = json.loads((folder / "draft_content.json").read_text(encoding="utf-8"))
        main = capcut_edit._video_track(draft)
        inserts = [segment for track in draft["tracks"] if track["type"] == "video" and track is not main for segment in track["segments"]]
        return draft, inserts

    def test_content_cache_does_not_alias_equal_names_sizes_or_preserved_mtime_changes(self):
        out = self.base / "composition"; out.mkdir()
        sources = []
        for name, contents in (("a", b"AAAA"), ("b", b"BBBB")):
            folder = self.base / name; folder.mkdir(); file = folder / "same.mp4"; file.write_bytes(contents); sources.append(file)
        first, second = [hf_build._local(out, str(file)) for file in sources]
        self.assertNotEqual(first, second)
        self.assertEqual((out / first).read_bytes(), b"AAAA"); self.assertEqual((out / second).read_bytes(), b"BBBB")
        before = sources[0].stat(); sources[0].write_bytes(b"CCCC")
        os.utime(sources[0], ns=(before.st_atime_ns, before.st_mtime_ns))
        third = hf_build._local(out, str(sources[0]))
        self.assertNotEqual(first, third)
        self.assertEqual((out / first).read_bytes(), b"AAAA")
        self.assertEqual((out / third).read_bytes(), b"CCCC")
        self.assertEqual(sources[1].read_bytes(), b"BBBB")

    def test_cache_copy_failure_preserves_published_asset_and_cleans_only_own_temporary(self):
        out = self.base / "composition"; out.mkdir(); source = self.base / "asset.mp4"; source.write_bytes(b"AAAA")
        published = hf_build._local(out, str(source)); source.write_bytes(b"BBBB")
        with patch("shutil.copy2", side_effect=OSError("synthetic disk full")), self.assertRaises(OSError):
            hf_build._local(out, str(source))
        self.assertEqual((out / published).read_bytes(), b"AAAA")
        self.assertFalse(list((out / "media").glob(".asset-*")))

    def test_geometry_has_one_focus_cover_rectangle_at_three_ratios(self):
        for W,H in ((1080,1920),(1920,1080),(1080,1080)):
            for focus in (0,.5,1):
                result = insert_geometry.geometry({"width": 900, "height": 180}, W=W,H=H,focus_x=focus)
                frame, clip = result["frame"], result["clip"]
                self.assertGreaterEqual(frame["width"],W); self.assertGreaterEqual(frame["height"],H)
                self.assertLessEqual(frame["left"],0); self.assertGreaterEqual(frame["left"],W-frame["width"])
                fit=min(W/900,H/180)
                self.assertAlmostEqual(900*fit*clip["scale_x"],frame["width"])
                self.assertAlmostEqual((W-frame["width"])/2+clip["x"]*W/2,frame["left"])

    def test_still_and_video_hf_native_share_exact_source_and_geometry_at_three_ratios(self):
        for number,(W,H) in enumerate(((180,320),(320,180),(240,240))):
            for kind,source,offset in (("video",self.video,2),("image",self.image,0)):
                project=self.project(f"{kind}-{number}",W,H)
                rows=self.place(project,source,source_start=offset,focus_x=.1)
                timeline=render.render_draft(str(project),"",dry=True)
                self.assertEqual(timeline["broll"][0]["source_start"],offset)
                with patch.object(render,"render_draft",return_value=timeline),patch.object(hf_build,"_proxy",return_value="main.mp4"):
                    page=hf_build.build(str(project)).read_text(encoding="utf-8")
                frame=rows[0]["frame"]
                for key,css in (("left","left"),("top","top"),("width","width"),("height","height")):
                    self.assertIn(f'{css}:{frame[key]}px',page)
                if kind=="video":
                    self.assertIn('data-media-start="2.000000000"',page)
                    self.assertIn(' muted playsinline',page)
                draft,segments=self.native(project,timeline)
                self.assertEqual(len(segments),1)
                segment=segments[0]
                self.assertEqual(segment["source_timerange"],{"start":offset*1_000_000,"duration":700_000})
                self.assertEqual(segment["target_timerange"],{"start":500_000,"duration":700_000})
                self.assertEqual(segment["volume"],0)
                self.assertEqual(segment["clip"]["transform"],{"x":rows[0]["clip"]["x"],"y":rows[0]["clip"]["y"]})

    def test_short_source_missing_file_and_outside_timeline_reject_before_export(self):
        project=self.project()
        catalog=insert_plan.register_assets(project,[self.video]); asset=catalog["candidates"][0]
        item={"asset_id":asset,"anchor":{"kind":"timeline","start":.5},"reason":"Synthetic invalid range","dur":1,"source_start":3.5}
        for changes in ({},{"source_start":0,"anchor":{"kind":"timeline","start":2.5}}):
            with self.subTest(changes=changes),self.assertRaises(render.VideoEditError):
                insert_plan.normalize(project,[dict(item,**changes)])
        Path(catalog["assets"][asset]["file"]).unlink()
        with self.assertRaises(render.VideoEditError):insert_plan.normalize(project,[dict(item,source_start=0)])

    def test_ramp_partial_consumption_and_dry_never_encodes(self):
        self.assertAlmostEqual(insert_geometry.consumption(.3,ramp=True),.75)
        self.assertAlmostEqual(insert_geometry.consumption(1,ramp=True),1.6)
        project=self.project(ramp=True)
        self.place(project,source_start=1,dur=.3)
        with patch.object(render,"speed_ramp",side_effect=AssertionError("dry must not encode")):
            timeline=render.render_draft(str(project),"",dry=True)
        self.assertEqual(timeline["broll"][0]["derived_recipe"],"ramp-v2")
        self.assertFalse((project/"clipkit_ramp").exists())
        actual=render.insert_playback(project,timeline["broll"],materialize=True)
        self.assertEqual(actual[0]["media_start"],0)
        self.assertAlmostEqual(render.probe(actual[0]["play"])["duration"],.3,delta=.04)
        with patch.object(render.subprocess,"run",side_effect=AssertionError("valid recipe cache must be reused")):
            again=render.insert_playback(project,timeline["broll"],materialize=True)
        self.assertEqual(again[0]["play"],actual[0]["play"])
        with patch.object(render,"render_draft",return_value=timeline),patch.object(hf_build,"_proxy",return_value="main.mp4"):
            page=hf_build.build(str(project)).read_text(encoding="utf-8")
        self.assertIn(insert_plan.file_digest(actual[0]["play"])+".mp4",page)
        self.assertIn('data-media-start="0.000000000"',page)
        draft,segments=self.native(project,timeline)
        material=next(m for m in draft["materials"]["videos"] if m["id"]==segments[0]["material_id"])
        self.assertTrue(Path(material["path"]).samefile(actual[0]["play"]))
        self.assertEqual(segments[0]["source_timerange"],{"start":0,"duration":300_000})

    def test_ramp_rejects_insufficient_source_without_encoding(self):
        project=self.project(ramp=True)
        with patch.object(render,"speed_ramp",side_effect=AssertionError("must reject before encoding")),self.assertRaises(render.VideoEditError):
            self.place(project,source_start=3.5,dur=.5)

    def test_search_candidate_map_never_creates_a_placement_or_guesses_duration(self):
        project=self.project()
        draft=json.loads((project/"draft_content.json").read_text())
        draft["materials"]["texts"]=[{"id":"text","font_size":15,"content":json.dumps({"text":"normal keyword","styles":[]})}]
        draft["tracks"].append({"type":"text","segments":[{"id":"cue","material_id":"text","clip":{"scale":{"x":1,"y":1},"transform":{"x":0,"y":0}},
            "target_timerange":{"start":0,"duration":3_000_000}}]})
        (project/"draft_content.json").write_text(json.dumps(draft))
        (project/"clipkit_style.json").write_text('{"anim":"pair","music":"","sfx":false}')
        (project/"clipkit_words.json").write_text(json.dumps({"cue:cue":[[0,1,"normal "],[1,2,"keyword"]]}))
        (project/"clipkit_punch.json").write_text(json.dumps({"cue:cue":[["normal","keyword","coffee"]]}))
        (project/"clipkit_broll.json").write_text(json.dumps({"coffee":{"file":str(self.video)}}))
        timeline=render.render_draft(str(project),"",dry=True)
        self.assertEqual(timeline["phrases"][0]["query"],"coffee")
        self.assertEqual(timeline["broll"],[])
        self.assertFalse((project/"clipkit_placed.json").exists())

    def test_safe_zone_checks_the_actual_ratio_layout_and_keeps_unsafe_role_warning(self):
        presets=self.base/"presets";presets.mkdir()
        unsafe=json.loads((render.PRESETS/"default.json").read_text(encoding="utf-8"))
        unsafe.update(fixed_size=True)
        unsafe["normal"]["y"]=1.5
        (presets/"unsafe.json").write_text(json.dumps(unsafe),encoding="utf-8")
        for number,(W,H) in enumerate(((1080,1920),(1920,1080),(1080,1080))):
            project=self.project(f"safe-zone-{number}",W,H)
            (project/"clipkit_style.json").write_text('{"anim":"pair","preset":"default"}')
            self.assertEqual(render.safe_zone(str(project)),[],(W,H))
            # This role remains above the actual canvas after ratio adaptation.
            (project/"clipkit_style.json").write_text('{"anim":"pair","preset":"unsafe"}')
            with patch.object(render,"PRESETS",presets):
                warnings=render.safe_zone(str(project))
            self.assertTrue(any("preset unsafe lead line" in warning for warning in warnings),(W,H,warnings))

    def test_hf_canonical_glyph_transform_is_owned_only_by_gsap(self):
        project=self.project()
        timeline=render.render_draft(str(project),"",dry=True)
        events=render.validate_pair_cues([{"id":"cue","text":"word","start":0,"end":1}],{}, {},style={},W=320,H=180)
        timeline.update(look="pair",subtitle_events=events)
        timeline.pop("plain_events", None)  # This fixture deliberately switches to a fresh pair plan.
        with patch.object(render,"render_draft",return_value=timeline):
            page=hf_build.build(str(project)).read_text(encoding="utf-8")
        css=re.search(r"<style>(.*?)</style>",page,re.S)[1]
        glyph_rules=[body for selectors,body in re.findall(r"([^{}]+)\{([^{}]*)\}",css)
                     if ".subtitle-glyph" in [s.strip() for s in selectors.split(",")]]
        self.assertTrue(glyph_rules)
        self.assertFalse(any(re.search(r"(?:^|;)\s*transform\s*:",body) for body in glyph_rules))
        children=re.findall(r'<div class="subtitle-glyph" style="([^"]*)">',page)
        self.assertTrue(children)
        self.assertIn('style="position:absolute;inset:0">',page)
        self.assertTrue(all("transform:" not in style for style in children))
        self.assertIn(' > .subtitle-glyph",{xPercent:-50,yPercent:-50,rotation:',page)
        # Legacy plain/hook text keeps its existing CSS centering on a separate class.
        self.assertRegex(css,r"\.txt\{[^}]*transform:translateY\(-50%\)")

    def test_cut_shifts_timeline_anchor_once_preserves_source_focus_and_undo_restores_bytes(self):
        project=self.project();self.place(project,source_start=2,anchor={"kind":"timeline","start":1.5},focus_x=.1)
        before=(project/"clipkit_placed.json").read_bytes()
        video_editor.draft_cut(video_editor.CutRange(path=str(project),start=0,end=.5))
        row=json.loads((project/"clipkit_placed.json").read_text())["broll"][0]
        self.assertEqual(row["start"],1);self.assertEqual(row["anchor"]["start"],1)
        self.assertEqual(row["source_start"],2);self.assertEqual(row["focus_x"],.1)
        normalized=insert_plan.normalize(project,[row]);self.assertEqual(normalized[0]["start"],1)
        video_editor.draft_uncut(video_editor.DraftPath(path=str(project)))
        self.assertEqual((project/"clipkit_placed.json").read_bytes(),before)

    def test_cut_late_placement_publish_failure_restores_every_metadata_byte(self):
        project=self.project();self.place(project,anchor={"kind":"timeline","start":1.5})
        content=project/"draft_content.json"
        draft=json.loads(content.read_text())
        draft["materials"]["texts"]=[{"id":"text","content":json.dumps({"text":"normal keyword","styles":[]})}]
        draft["tracks"].append({"type":"text","segments":[{"id":"cue","material_id":"text",
            "clip":{"transform":{"x":0,"y":0}},"target_timerange":{"start":0,"duration":3_000_000}}]})
        content.write_text(json.dumps(draft))
        (project/"clipkit_words.json").write_text(json.dumps({"normal keyword":[[0,1,"normal "],[1,2,"keyword"]]}))
        (project/"clipkit_overlays.json").write_text(json.dumps([{"start":1.5,"duration":.7,"template":"synthetic"}]))
        inner=project/"Timelines"/"main";inner.mkdir(parents=True)
        (inner.parent/"project.json").write_text('{"main_timeline_id":"main"}')
        (inner/"draft_content.json").write_bytes(content.read_bytes())
        (project/"draft_meta_info.json").write_text('{"tm_duration":3000000,"tm_draft_modified":1}')
        capcut_edit._seal(project,content.read_text())
        snapshot=lambda:{p.relative_to(project).as_posix():p.read_bytes() for p in project.rglob("*") if p.is_file() and ".clipkit-web-ai" not in p.relative_to(project).parts}
        before=snapshot();original=capcut_edit.atomic.write_text;failed=False;words_published=False
        def fail_once(path,*args,**kwargs):
            nonlocal failed,words_published
            if Path(path).name=="clipkit_placed.json" and not failed:
                failed=True
                words_published=(Path(path).parent/"clipkit_words.json").read_bytes()!=before["clipkit_words.json"]
                raise OSError("injected late placement publish failure")
            return original(path,*args,**kwargs)
        with patch.object(capcut_edit.atomic,"write_text",side_effect=fail_once),self.assertRaisesRegex(OSError,"late placement"):
            capcut_edit.trim_pauses(str(project),ranges=[(0,.5)])
        self.assertTrue(failed);self.assertTrue(words_published)
        self.assertEqual(snapshot(),before)

    def test_cut_failure_preserves_absent_sidecars_and_new_backup_absence(self):
        project=self.project()
        snapshot=lambda:{p.relative_to(project).as_posix():p.read_bytes() for p in project.rglob("*") if p.is_file() and ".clipkit-web-ai" not in p.relative_to(project).parts}
        before=snapshot();original=capcut_edit.atomic.write_text;failed=False
        def fail_once(path,*args,**kwargs):
            nonlocal failed
            if Path(path).name==capcut_edit.SEAL and not failed:
                failed=True
                raise OSError("injected seal publish failure")
            return original(path,*args,**kwargs)
        with patch.object(capcut_edit.atomic,"write_text",side_effect=fail_once),self.assertRaisesRegex(OSError,"seal publish"):
            capcut_edit.trim_pauses(str(project),ranges=[(0,.5)])
        self.assertTrue(failed);self.assertEqual(snapshot(),before)
        for name in ("clipkit_words.json","clipkit_placed.json","clipkit_overlays.json","draft_content.json.bak_trim",capcut_edit.SEAL):
            self.assertFalse((project/name).exists(),name)

    def test_crossing_insert_cut_refuses_before_any_project_write(self):
        project=self.project();self.place(project)
        before={p.name:p.read_bytes() for p in project.iterdir() if p.is_file()}
        with self.assertRaisesRegex(render.VideoEditError,"สร้างใหม่หลังตัด"):
            capcut_edit.trim_pauses(str(project),ranges=[(.8,1)])
        self.assertEqual(before,{p.name:p.read_bytes() for p in project.iterdir() if p.is_file()})

    def test_cpu_three_ratios_use_nonzero_source_offset_and_keep_primary_speech(self):
        import numpy as np
        for number,(W,H) in enumerate(((180,320),(320,180),(240,240))):
            project=self.project(f"cpu-{number}",W,H);self.place(project,source_start=2)
            result=render.render_draft(str(project),str(self.base/"exports"))
            output=result["file"]
            frame=subprocess.run([render.FFMPEG,"-v","error","-ss","0.8","-i",output,"-frames:v","1","-f","rawvideo","-pix_fmt","rgb24","pipe:1"],capture_output=True,timeout=30)
            self.assertEqual(frame.returncode,0,frame.stderr)
            pixels=np.frombuffer(frame.stdout,dtype=np.uint8).reshape(H,W,3)
            center=pixels[H//2,W//2].astype(int)
            self.assertGreater(center[2],150);self.assertLess(center[0],80)
            audio=subprocess.run([render.FFMPEG,"-v","error","-ss","0.7","-t","0.2","-i",output,"-vn","-ac","1","-ar","8000","-f","f32le","pipe:1"],capture_output=True,timeout=30)
            samples=np.frombuffer(audio.stdout,dtype=np.float32);self.assertGreater(len(samples),1000)
            spectrum=np.abs(np.fft.rfft(samples));frequencies=np.fft.rfftfreq(len(samples),1/8000)
            self.assertAlmostEqual(frequencies[spectrum.argmax()],440,delta=10)

    def test_cpu_still_focus_selects_the_expected_source_region_at_three_ratios(self):
        import numpy as np
        for number,(W,H,focus,channel) in enumerate(((180,320,0,0),(320,180,1,1),(240,240,.5,2))):
            project=self.project(f"still-{number}",W,H)
            self.place(project,self.image,source_start=0,focus_x=focus)
            result=render.render_draft(str(project),str(self.base/"exports"))
            frame=subprocess.run([render.FFMPEG,"-v","error","-ss","0.8","-i",result["file"],"-frames:v","1",
                                  "-f","rawvideo","-pix_fmt","rgb24","pipe:1"],capture_output=True,timeout=30)
            self.assertEqual(frame.returncode,0,frame.stderr)
            pixels=np.frombuffer(frame.stdout,dtype=np.uint8).reshape(H,W,3)
            center=pixels[H//2,W//2].astype(int)
            self.assertGreater(center[channel],90)
            self.assertGreater(center[channel],max(center[index] for index in range(3) if index!=channel)*2)


if __name__=="__main__":
    unittest.main()
