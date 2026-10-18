"""Synthetic role/ratio/cut parity: one measured plan feeds ASS, browser and native exports."""
import copy
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
with tempfile.TemporaryDirectory() as folder:
    config = Path(folder) / "config.json"
    config.write_text("{}")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import render
import capcut_build
import hf_build
import kit_settings

CANVASES = ((1080,1920), (1920,1080), (1080,1080))


class SubtitleExportTests(unittest.TestCase):
    def setUp(self):
        self.original = copy.deepcopy(capcut_build._T)
        capcut_build._T.clear()
        capcut_build._T.update(json.loads(capcut_build.TEMPLATES.read_text(encoding="utf-8")))
        self.addCleanup(lambda: capcut_build._T.clear())
        self.addCleanup(lambda: capcut_build._T.update(self.original))

    def fixture(self, looks=("pair",), captions=True):
        rows, words, picks, translated = [], {}, {}, {}
        for n, look in enumerate(looks):
            cue, start = f"source-{n}", n*2.25
            rows.append({"id":cue,"text":"normal keyword","start":start,"end":start+1.1})
            words["cue:"+cue] = [[0,.3,"normal "],[.8,1.1,"keyword"]]
            opts = {"look":look}
            if look == "color":
                opts["show"] = ["", "key"]
            if captions and look == "pair":
                opts["second"] = "yes"
            picks["cue:"+cue] = [["normal","keyword",opts]]
            translated["cue:"+cue] = f"caption {n}"
        return rows,words,picks,translated

    def preset(self, fixed=False):
        return {"fixed_size":fixed,"font":str(render.DEFAULT_FONT),
            "normal":{"size":14,"y":-.1,"color":"#ffffff","outline":"#000000","outline_width":.03},
            "emphasis":{"size":18,"y":-.35,"color":"#051192","outline":"#ffffff","outline_width":.03},
            "caption":{"size":6,"y":-.65,"color":"#ffffff","outline":"#000000","outline_width":.03,"language":"en"},
            "second":{"size":9,"y":.45,"x":.25,"rotation":-6,"color":"#ffffff","outline":"#000000","outline_width":.03}}

    def events(self, width, height, looks=("pair",), fixed=False):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder); (path/"synthetic.json").write_text(json.dumps(self.preset(fixed)))
            rows,words,picks,captions=self.fixture(looks)
            with patch.object(render,"PRESETS",path):
                return render.validate_pair_cues(rows,words,picks,style={"preset":"synthetic"},W=width,H=height,captions=captions)

    def test_all_roles_and_looks_keep_exact_word_clock_after_cuts_at_three_ratios(self):
        for width,height in CANVASES:
            for fixed in (False,True):
                with self.subTest(canvas=(width,height),fixed=fixed):
                    events=self.events(width,height,("pair","white","color","color","hold","red","skip"),fixed)
                    self.assertEqual({e["role"] for e in events},{"normal","emphasis","caption","second"})
                    keyword=next(e for e in events if e["role"]=="emphasis" and e["cue_id"]=="source-0")
                    self.assertAlmostEqual(keyword["start"],.8)
                    normal=next(e for e in events if e["role"]=="normal" and e["cue_id"]=="source-0")
                    self.assertEqual(normal["end"],2.25)
                    self.assertEqual(keyword["end"],normal["end"])
                    red=next(e for e in events if e["role"]=="emphasis" and e["cue_id"]=="source-5")
                    self.assertEqual(red["fill"],render.RED)
                    stack=next(e for e in events if e["role"]=="emphasis" and e["cue_id"]=="source-2")
                    self.assertEqual(stack["end"],9)
                    self.assertFalse(any(e["role"]!="caption" and e["cue_id"]=="source-6" for e in events))
                    for event in events:
                        self.assertGreaterEqual(event["bbox"][0],width*.11-.01)
                        self.assertLessEqual(event["bbox"][2],width*.89+.01)
                        self.assertGreaterEqual(event["bbox"][1],height*.11-.01)
                        self.assertLessEqual(event["bbox"][3],height*.77+.01)

    def test_adapters_serialize_exact_measured_geometry_font_outline_and_clocks(self):
        roles={"normal":"white","emphasis":"orange","caption":"caption","second":"white"}
        for width,height in CANVASES:
            for event in self.events(width,height,fixed=True):
                with self.subTest(canvas=(width,height),role=event["role"]):
                    original=copy.deepcopy(event)
                    ass=render.subtitle_ass(event)
                    self.assertIn(f"\\pos({event['center_x_px']:.3f},{event['center_y_px'] + event['size_px'] * event['font_cell_center_delta']:.3f})",ass)
                    self.assertIn(f"\\fs{event['size_px'] * event['font_cell_scale']:.3f}",ass)
                    html=hf_build.subtitle_html(event,"synthetic")
                    self.assertIn(f"left:{event['center_x_px']:.6f}px",html)
                    self.assertIn(f"top:{event['center_y_px']:.6f}px",html)
                    self.assertIn(f"font-size:{event['size_px']:.6f}px",html)
                    draft={"materials":{"texts":[]}}
                    segment=capcut_build._event_text(draft,roles[event["role"]],event,width,height)
                    material=draft["materials"]["texts"][0]
                    native=json.loads(material["content"])["styles"][0]
                    self.assertAlmostEqual(material["font_size"]*render.CAPCUT_PX*min(width,height)/1080,event["size_px"])
                    self.assertAlmostEqual((1-segment["clip"]["transform"]["y"])*height/2,event["center_y_px"])
                    self.assertAlmostEqual((1+segment["clip"]["transform"]["x"])*width/2,event["center_x_px"])
                    self.assertEqual(native["font"]["path"],event["font_file"])
                    self.assertAlmostEqual(native["strokes"][0]["width"]*event["size_px"]*.6+2,event["outline_px"])
                    self.assertEqual(segment["target_timerange"]["start"],round(event["start"]*1e6))
                    self.assertEqual(event,original)

    def test_fixed_sizes_and_centers_do_not_change_with_different_text_lengths(self):
        first=self.events(1080,1920,fixed=True)
        rows,words,picks,captions=self.fixture()
        rows[0]["text"]="a b"; words["cue:source-0"]=[[0,.3,"a "],[.8,1.1,"b"]]
        picks["cue:source-0"]=[["a","b",{"second":"no"}]]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/"synthetic.json").write_text(json.dumps(self.preset(True)))
            with patch.object(render,"PRESETS",path):
                second=render.validate_pair_cues(rows,words,picks,style={"preset":"synthetic"},W=1080,H=1920,captions=captions)
        for role in ("normal","emphasis","caption","second"):
            a=next(e for e in first if e["role"]==role);b=next(e for e in second if e["role"]==role)
            self.assertEqual((a["size_px"],a["center_x_px"],a["center_y_px"]),(b["size_px"],b["center_x_px"],b["center_y_px"]))

    def test_missing_translation_is_absent_and_source_caption_is_explicit(self):
        rows,words,picks,_=self.fixture(captions=False)
        for width,height in CANVASES:
            events=render.validate_pair_cues(rows,words,picks,style={"preset":"with-caption"},W=width,H=height)
            self.assertNotIn("caption",{e["role"] for e in events})

    def test_original_cue_bounds_are_checked_before_pause_hold_extension(self):
        rows,words,picks,captions=self.fixture(("pair","pair"))
        words["cue:source-0"][-1][1]=2
        with self.assertRaisesRegex(render.VideoEditError,"source-0.*ช่วงซับจริง"):
            render.validate_pair_cues(rows,words,picks,style={},W=1080,H=1920,captions=captions)

    def test_hold_ends_at_next_resolved_word_onset_not_native_row_start(self):
        rows=[{"id":"a","text":"first","start":0,"end":1},{"id":"b","text":"next","start":2,"end":4}]
        words={"cue:a":[[0,1,"first"]],"cue:b":[[.5,1,"next"]]}
        events=render.validate_pair_cues(rows,words,{},style={},W=1080,H=1920,duration=5)
        normal=[e for e in events if e["role"]=="normal"]
        self.assertEqual(normal[0]["end"],2.5)
        self.assertEqual(normal[1]["start"],2.5)
        self.assertEqual(normal[1]["end"],5)
        # A visual hold never relaxes the original source row bounds or overlap checks.
        words["cue:a"]=[[0,1.1,"first"]]
        with self.assertRaisesRegex(render.VideoEditError,"ช่วงซับจริง"):
            render.validate_pair_cues(rows,words,{},style={},W=1080,H=1920)
        words["cue:a"]=[[0,1,"first"]];rows[0]["end"]=2.01
        with self.assertRaisesRegex(render.VideoEditError,"ช่วงซับทับ"):
            render.validate_pair_cues(rows,words,{},style={},W=1080,H=1920)

    def test_fixed_overflow_reports_cue_and_role_without_silent_size_change(self):
        rows,words,picks,captions=self.fixture()
        picks["cue:source-0"][0][2]["show"]=["a"*300,"word"]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/"synthetic.json").write_text(json.dumps(self.preset(True)))
            with patch.object(render,"PRESETS",path), self.assertRaisesRegex(render.VideoEditError,"source-0.*normal.*show"):
                render.validate_pair_cues(rows,words,picks,style={"preset":"synthetic"},W=1080,H=1920,captions=captions)

    def test_invalid_fixed_role_collision_is_reported(self):
        rows,words,picks,captions=self.fixture(captions=False)
        preset=self.preset(True);preset["emphasis"]["y"]=preset["normal"]["y"]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/"synthetic.json").write_text(json.dumps(preset))
            with patch.object(render,"PRESETS",path), self.assertRaisesRegex(render.VideoEditError,"source-0.*(normal/emphasis|emphasis/normal).*ทับ"):
                render.validate_pair_cues(rows,words,picks,style={"preset":"synthetic"},W=1080,H=1920,captions=captions)

    def test_native_and_hyperframes_animate_children_or_native_channels_not_provider_effects(self):
        event=next(e for e in self.events(1080,1920) if e["role"]=="emphasis")
        animation=hf_build.subtitle_animation(event,"synthetic")
        self.assertIn("#synthetic > .subtitle-glyph",animation)
        draft={"materials":{"texts":[]}}
        segment=capcut_build._event_text(draft,"orange",event,1080,1920)
        self.assertEqual(segment["extra_material_refs"],[])
        self.assertEqual({c["property_type"] for c in segment["common_keyframes"]},{"KFTypeScaleX","KFTypeScaleY"})
        for channel in segment["common_keyframes"]:
            self.assertEqual([p["values"] for p in channel["keyframe_list"]],[[1.3],[1]])
            self.assertEqual(channel["keyframe_list"][-1]["time_offset"],140000)

    def test_motion_preflight_rejects_invented_clock_before_allocating_renderer(self):
        with patch("tempfile.mkdtemp") as allocate, patch("shutil.which", return_value="npx"):
            with self.assertRaisesRegex(kit_settings.HTTPException, "เวลาระดับคำจริง"):
                kit_settings.make_motion(kit_settings.MotionRequest(template="sentence-pair",name="synthetic",
                    params={"pairs":[[0,2,"normal","keyword",.1]]}))
            allocate.assert_not_called()

    def test_motion_preflight_uses_real_word_onset_instead_of_submitted_delay(self):
        params=kit_settings.sentence_pair_params({"pairs":[[0,2,"normal","keyword",.1]],
            "word_times":[[[0,.3,"normal "],[1.2,1.7,"keyword"]]]})
        emphasis=next(e for e in params["subtitle_events"] if e["role"]=="emphasis")
        self.assertEqual(emphasis["start"],1.2)

    def test_motion_preflight_rejects_invalid_canonical_geometry_before_render(self):
        event=self.events(1080,1920)[0]
        for change in ({"center_x_px":-1000},{"fill":[float("nan"),0,0]},{"size_px":float("inf")}):
            with self.subTest(change=change),self.assertRaises(kit_settings.HTTPException):
                kit_settings.sentence_pair_params({"width":1080,"height":1920,"subtitle_events":[dict(event,**change)]})

    def test_motion_render_receives_actual_ratio_duration_and_font_bytes(self):
        import render_process
        from types import SimpleNamespace
        for width,height in CANVASES:
            events=self.events(width,height)
            with tempfile.TemporaryDirectory() as folder:
                captured={}
                def fake_render(command,**kwargs):
                    work=Path(kwargs["cwd"])
                    captured["html"]=(work/"index.html").read_text(encoding="utf-8")
                    captured["font"]=(work/"subtitle-font-0.ttf").read_bytes()
                    if "check" in command:
                        return SimpleNamespace(returncode=0,stdout=json.dumps({"ok":True}),stderr="")
                    Path(command[-1]).write_bytes(b"synthetic fixture only")
                    return SimpleNamespace(returncode=0,stdout="",stderr="")
                with patch.object(kit_settings,"_read_config",return_value={"output_dir":folder}), \
                     patch("shutil.which",return_value="npx"), \
                     patch.object(kit_settings.host_platform,"hyperframes_command",side_effect=lambda command,*args:command), \
                     patch("motion_plan.verify_media",return_value={"sha256":"synthetic fixture only"}), \
                     patch.object(render_process,"run",side_effect=fake_render):
                    kit_settings.make_motion(kit_settings.MotionRequest(template="sentence-pair",name="synthetic",
                        params={"width":width,"height":height,"subtitle_events":events}))
                self.assertIn(f'data-width="{width}"',captured["html"])
                self.assertIn(f'data-height="{height}"',captured["html"])
                self.assertIn(f'data-duration="{max(e["end"] for e in events):g}"',captured["html"])
                self.assertEqual(captured["font"],render.DEFAULT_FONT.read_bytes())

    def test_cpu_ass_glyph_width_matches_measured_em_size(self):
        """Nominal equal font sizes are insufficient: libass uses the Windows cell, CSS uses em."""
        import shutil
        import subprocess
        from PIL import Image, ImageFont
        if not Path(render.FFMPEG).is_file() and not shutil.which(render.FFMPEG):
            self.skipTest("FFmpeg unavailable; CPU pixel acceptance remains pending")
        event=next(e for e in self.events(1080,1080) if e["role"]=="emphasis")
        # Raw AI show text must remain literal, including ASS-looking commands and escapes.
        literal = r"{\fs9999} A\N B\h C"
        event=dict(event,text=literal,start=0,end=1,effect="none")
        encoded=render.subtitle_ass(event).split("}",1)[1]
        self.assertEqual(event["text"],literal)
        self.assertIn(r"\{",encoded)
        self.assertNotIn(r"{\fs9999}",encoded)
        self.assertNotIn(r"\N",encoded)
        self.assertNotIn(r"\h",encoded)
        with tempfile.TemporaryDirectory() as folder:
            work=Path(folder);(work/"fonts").mkdir()
            shutil.copyfile(render.DEFAULT_FONT,work/"fonts"/render.DEFAULT_FONT.name)
            header=render.subtitle_ass_header(1080, 1080, event['font_family'])
            self.assertIn('Kerning: yes\n', header)
            (work/"subtitles.ass").write_text(header+render.subtitle_ass(event)+"\n",encoding="utf-8")
            result=subprocess.run([render.FFMPEG,"-v","error","-y","-f","lavfi","-i","color=s=1080x1080:d=0.1",
                "-vf","ass=subtitles.ass:fontsdir=fonts","-frames:v","1","-threads","1","-filter_threads","1","frame.png"],
                cwd=work,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr[-500:])
            image=Image.open(work/"frame.png").convert("RGB")
            points=[(x,y) for y in range(550,750) for x in range(150,930)
                    if (lambda rgb:rgb[2]>60 and rgb[2]>rgb[0]*2 and rgb[2]>rgb[1]*2)(image.getpixel((x,y)))]
            self.assertTrue(points,"actual ASS frame must contain the planned blue text")
            width=max(x for x,y in points)-min(x for x,y in points)+1
            self.assertLess(max(y for x,y in points)-min(y for x,y in points)+1,event["size_px"]*1.3)
            expected=ImageFont.truetype(str(render.DEFAULT_FONT),100).getlength(event["text"])*event["size_px"]/100
            self.assertGreater(width,expected*.9)
            self.assertLess(width,expected*1.1)

    def test_ai_display_override_cannot_inject_ass_layout_commands(self):
        rows,words,picks,captions=self.fixture(captions=False)
        literal=r"{\pos(0,0)\fs9999}safe\Nline"
        picks["cue:source-0"][0][2]["show"]=["normal",literal]
        events=render.validate_pair_cues(rows,words,picks,style={},W=1080,H=1920)
        event=next(e for e in events if e["role"]=="emphasis")
        self.assertEqual(event["text"],literal)
        encoded=render.subtitle_ass(event).split("}",1)[1]
        self.assertNotIn(r"{\pos",encoded)
        self.assertNotIn(r"\N",encoded)
        self.assertEqual(event["text"],literal)


if __name__ == "__main__":
    unittest.main()
