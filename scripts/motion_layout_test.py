"""Measured generic templates and real alpha-media validation, with isolated input/config."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
with tempfile.TemporaryDirectory() as folder:
    config = Path(folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import motion_plan
import kit_settings
import kitconfig
import render


class MotionLayoutTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(folder)
        config = self.root / "config.json"; config.write_text("{}", encoding="utf-8")
        self.enterContext(patch.object(kitconfig, "_CANDIDATES", [config]))

    def params(self, template, **changes):
        result = ({"key": "ข้อความสำคัญ", "sub": "เหตุผลจากเนื้อหา", "style": "straight", "duration": 2.123456}
                  if template == "hook-title" else {"number": "87.50", "unit": "%", "label": "ค่าที่พูดถึง", "duration": 2.123456})
        return {**result, **changes}

    def test_actual_canvas_font_and_animated_bounds_at_three_ratios(self):
        for W,H in ((1080,1920),(1920,1080),(1080,1080)):
            for template,changes in (("hook-title",{}),("hook-title",{"style":"tilt"}),("stat-number",{})):
                with self.subTest(canvas=(W,H),template=template,changes=changes):
                    plan=motion_plan.prepare_template(template,self.params(template,**changes),width=W,height=H)
                    x0,y0,x1,y1=plan["bounds"]
                    self.assertTrue(0<=x0<x1<=W and 0<=y0<y1<=H)
                    self.assertEqual(plan["canvas"],{"width":W,"height":H})
                    self.assertEqual(plan["duration"],2.123456)
                    work=self.root/f'{W}-{H}-{template}-{changes.get("style","")}'
                    work.mkdir()
                    page=kit_settings._motion_project(plan,work).read_text(encoding="utf-8")
                    self.assertIn(f'data-width="{W}"',page);self.assertIn(f'data-height="{H}"',page)
                    self.assertIn('data-duration="2.123456"',page)
                    self.assertEqual((work/'motion-font-0.ttf').read_bytes(),render.DEFAULT_FONT.read_bytes())
                    self.assertIn('font-weight:700',page)
                    motion_plan.verify_inputs(plan)

    def test_invalid_placement_or_unreadable_text_rejects(self):
        for placement in ({"x":True,"y":0,"w":.5,"h":.5}, {"x":float('nan'),"y":0,"w":.5,"h":.5},
                          {"x":.5,"y":0,"w":.6,"h":.5}, {"x":.1,"y":.1,"w":.001,"h":.001}):
            with self.subTest(placement=placement),self.assertRaises(motion_plan.VideoEditError):
                motion_plan.prepare_template('hook-title',self.params('hook-title'),placement=placement)

    def test_missing_or_changed_font_cannot_be_silently_replaced(self):
        with patch.object(kit_settings,'_read_config',return_value={}), \
                patch('host_platform.find_font',return_value=None),self.assertRaises(motion_plan.VideoEditError):
            motion_plan.prepare_template('hook-title',self.params('hook-title',font='missing-font'))
        file=self.root/'font.ttf';file.write_bytes(render.DEFAULT_FONT.read_bytes())
        plan=motion_plan.prepare_template('hook-title',self.params('hook-title',font=str(file)))
        file.write_bytes(file.read_bytes()+b'changed')
        with self.assertRaisesRegex(motion_plan.VideoEditError,'changed'):
            motion_plan.verify_inputs(plan)

    def test_number_does_not_silently_round_beyond_javascript_integer_range(self):
        plan=motion_plan.prepare_template('stat-number',self.params('stat-number',number='1,250.50'))
        self.assertEqual(plan['layout']['number_text'],'1,250.50')
        with self.assertRaisesRegex(motion_plan.VideoEditError,'numeric'):
            motion_plan.prepare_template('stat-number',self.params('stat-number',number=str(2**53)))

    def test_real_decode_rejects_opaque_and_wrong_canvas_media(self):
        alpha=self.root/'alpha.mov';opaque=self.root/'opaque.mp4'
        for path,codec,source in ((alpha,'qtrle','color=black@0:s=64x64:r=10:d=0.2,format=rgba'),
                                  (opaque,'libx264','color=red:s=64x64:r=10:d=0.2')):
            subprocess.run([render.FFMPEG,'-v','error','-y','-f','lavfi','-i',source,'-c:v',codec,str(path)],check=True)
        plan={'canvas':{'width':64,'height':64},'duration':.2}
        result=motion_plan.verify_media(alpha,plan)
        self.assertTrue(result['alpha_decode']);self.assertEqual(result['duration'],.2)
        with self.assertRaisesRegex(motion_plan.VideoEditError,'alpha'):
            motion_plan.verify_media(opaque,plan)
        with self.assertRaisesRegex(motion_plan.VideoEditError,'canvas'):
            motion_plan.verify_media(alpha,{'canvas':{'width':65,'height':64},'duration':.2})

    def check(self,motion,events,**kwargs):
        motion_plan.check_placement(motion,events,width=100,height=100,duration=3,
            safe_box={'w':1,'top':0,'bottom':1},**kwargs)

    def test_decoration_preserves_roles_and_rejects_active_text_collision(self):
        motion={'start':0,'duration':3,'bounds':[10,10,30,30],
                'ownership':{'mode':'decorate','targets':[],'caption_policy':'preserve'}}
        caption={'start':0,'end':3,'bbox':[40,40,60,60],'role':'caption','cue_id':'c','text':'caption'}
        self.check(motion,[caption])
        with self.assertRaisesRegex(motion_plan.VideoEditError,'caption'):
            self.check(motion,[{**caption,'bbox':[15,15,25,25]}])

    def test_pair_uses_each_measured_role_box_not_its_empty_bounding_union(self):
        normal={'start':0,'end':3,'bbox':[10,10,30,30],'role':'normal','cue_id':'a','text':'normal'}
        emphasis={'start':1,'end':3,'bbox':[70,70,90,90],'role':'emphasis','cue_id':'a','text':'key'}
        caption={'start':0,'end':3,'bbox':[40,40,60,60],'role':'caption','cue_id':'b','text':'caption'}
        motion={'start':0,'duration':3,'bounds':[10,10,90,90],'params':{'subtitle_events':[normal,emphasis]},
                'ownership':{'mode':'replace','targets':[{'cue_id':'a','roles':['normal','emphasis']}],'caption_policy':'preserve'}}
        self.check(motion,[normal,emphasis,caption])
        with self.assertRaisesRegex(motion_plan.VideoEditError,'caption'):
            self.check(motion,[normal,emphasis,{**caption,'bbox':[15,15,25,25]}])
        with self.assertRaisesRegex(motion_plan.VideoEditError,'reproduce'):
            self.check({**motion,'params':{'subtitle_events':[normal]}},[normal,emphasis,caption])

    def test_timed_subject_zones_are_checked_without_inventing_face_tracking(self):
        motion={'start':0,'duration':1,'bounds':[10,10,30,30],
                'ownership':{'mode':'decorate','targets':[],'caption_policy':'preserve'}}
        zone={'start':1,'end':3,'x':.1,'y':.1,'w':.2,'h':.2}
        self.check(motion,[],subject_zones=[zone])
        with self.assertRaisesRegex(motion_plan.VideoEditError,'subject'):
            self.check(motion,[],subject_zones=[{**zone,'start':0}])
        with self.assertRaisesRegex(motion_plan.VideoEditError,'subject'):
            self.check(motion,[],subject_zones=[{**zone,'x':True}])

    def test_full_card_cannot_claim_to_replace_a_caption_it_does_not_render(self):
        caption={'start':0,'end':3,'bbox':[40,40,60,60],'role':'caption','cue_id':'c','text':'caption'}
        motion={'start':0,'duration':3,'bounds':[10,10,90,90],
                'ownership':{'mode':'full_card','targets':[],'caption_policy':'preserve'}}
        with self.assertRaisesRegex(motion_plan.VideoEditError,'caption'):
            self.check(motion,[caption])
        with self.assertRaisesRegex(motion_plan.VideoEditError,'reproduce'):
            self.check({**motion,'ownership':{**motion['ownership'],'caption_policy':'replace'}},[caption])
        self.check({**motion,'ownership':{**motion['ownership'],'caption_policy':'hide'}},[caption])

    def test_simultaneous_motion_checks_active_footprints_and_touching_edges(self):
        own={'mode':'decorate','targets':[],'caption_policy':'preserve'}
        first={'start':0,'duration':3,'bounds':[10,10,30,30],'ownership':own}
        second={**first,'bounds':[30,10,50,30]}
        self.check(first,[],others=[second])
        with self.assertRaisesRegex(motion_plan.VideoEditError,'overlap'):
            self.check(first,[],others=[{**second,'bounds':[29,10,50,30]}])
        parts=[{'start':0,'end':1,'bbox':[10,10,30,30]}, {'start':1,'end':3,'bbox':[70,70,90,90]}]
        dynamic={**first,'bounds':[10,10,90,90],'params':{'subtitle_events':parts}}
        self.check({**second,'start':1,'duration':2},[],others=[dynamic])
        for params in (None, [], {'subtitle_events':{}}, {'subtitle_events':True}):
            with self.subTest(params=params),self.assertRaisesRegex(motion_plan.VideoEditError,'array'):
                self.check(first,[],others=[{**dynamic,'params':params}])
        with self.assertRaisesRegex(motion_plan.VideoEditError,'invalid measured'):
            self.check(first,[],others=[{**dynamic,'params':{'subtitle_events':[{'start':0,'end':1,'bbox':None}]}}])

    def test_simultaneous_motions_cannot_claim_the_same_role_even_when_boxes_differ(self):
        own={'mode':'replace','targets':[{'cue_id':'c','roles':['normal']}],'caption_policy':'preserve'}
        first={'start':0,'duration':3,'bounds':[10,10,30,30],'ownership':own}
        second={**first,'bounds':[70,70,90,90]}
        with self.assertRaisesRegex(motion_plan.VideoEditError,'same subtitle role'):
            motion_plan.check_overlap(first,second)
        motion_plan.check_overlap(first,{**second,'ownership':{**own,'targets':[{'cue_id':'other','roles':['normal']}]}})
        with self.assertRaisesRegex(motion_plan.VideoEditError,'legacy'):
            motion_plan.check_overlap(first,{'start':1,'duration':1,'bounds':[70,70,90,90]})

    def test_other_motion_hides_only_its_explicit_owned_text_interval(self):
        event={'start':0,'end':3,'bbox':[10,10,30,30],'role':'normal','cue_id':'c','text':'text'}
        candidate={'start':0,'duration':3,'bounds':[10,10,30,30],
                   'ownership':{'mode':'decorate','targets':[],'caption_policy':'preserve'}}
        other={'start':0,'duration':3,'bounds':[70,70,90,90],
               'ownership':{'mode':'replace','targets':[{'cue_id':'c','roles':['normal']}],'caption_policy':'preserve'}}
        self.check(candidate,[event],others=[other])
        with self.assertRaisesRegex(motion_plan.VideoEditError,'normal'):
            self.check(candidate,[event],others=[{**other,'duration':1}])
        with self.assertRaisesRegex(motion_plan.VideoEditError,'normal'):
            self.check(candidate,[event],others=[{**other,'ownership':{**other['ownership'],'targets':[{'cue_id':'other','roles':['normal']}]}}])


if __name__=='__main__':
    unittest.main()
