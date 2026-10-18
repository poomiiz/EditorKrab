"""Explicit timed subject regions validate and publish without altering existing motion media."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import motion_cut_test as fixture
import motion_plan
import video_editor
import web_ai_identity as identity
import web_ai_transaction as transaction


def zone(start=0,end=8,**kwargs):
    return dict(start=start,end=end,x=0,y=0,w=.05,h=.05,**kwargs)


class SubjectZones(unittest.TestCase):
    def setUp(self):
        self.f=fixture.MotionCutTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.folder=self.f.folder;self.path=self.f.path

    def save(self,zones,previous=None):
        return video_editor.draft_subject_zones(video_editor.SubjectZonesRequest(path=self.path,previous=previous or [],zones=zones))

    def test_strict_timed_normalized_contract_even_without_any_motion(self):
        for changes in ({'start':True},{'start':float('nan')},{'end':9},{'end':0},{'w':0},{'h':float('inf')},{'x':-1},{'x':.99,'w':.05},{'extra':1},{'y':'0'}):
            with self.subTest(changes=changes):
                row=zone();row.update(changes);before=self.f.files()
                with self.assertRaises(video_editor.HTTPException):self.save([row])
                self.assertEqual(self.f.files(),before)
        self.assertEqual(motion_plan.validate_subject_zones([],8),[])
        with self.assertRaises(fixture.capcut_edit.VideoEditError):motion_plan.validate_subject_zones({},8)

    def test_temporal_coverage_needs_every_part_of_motion_not_any_zone(self):
        self.assertTrue(motion_plan.subject_review_pending([],2,6))
        self.assertTrue(motion_plan.subject_review_pending([zone(0,1)],2,6))
        self.assertTrue(motion_plan.subject_review_pending([zone(2,3),zone(4,6)],2,6))
        self.assertFalse(motion_plan.subject_review_pending([zone(4,6),zone(2,4)],2,6))
        self.assertFalse(motion_plan.subject_review_pending([zone(0,8)],2,6))

    def test_save_updates_review_projection_without_reencoding_or_media_mutation(self):
        old=self.f.install();self.f.calls.clear();original=Path(old['file']).read_bytes();metadata=(self.folder/'clipkit_overlays.json').read_bytes()
        result=self.save([zone()])
        self.assertEqual(result['motion_recipe_errors'],[])
        self.assertFalse(result['motions'][0]['subject_review_pending'])
        self.assertEqual(self.f.calls,[]);self.assertEqual(Path(old['file']).read_bytes(),original)
        self.assertEqual((self.folder/'clipkit_overlays.json').read_bytes(),metadata)
        cleared=self.save([],previous=[zone()]);self.assertTrue(cleared['motions'][0]['subject_review_pending'])

    def test_partial_time_region_keeps_motion_review_pending(self):
        self.f.install();result=self.save([zone(0,1)])
        self.assertTrue(result['motions'][0]['subject_review_pending'])

    def test_conflicting_region_is_saved_and_blocks_old_motion_export_until_repaired(self):
        old=self.f.install();self.f.calls.clear();original=Path(old['file']).read_bytes();row=zone();row.update(x=.4,y=.5,w=.3,h=.4)
        result=self.save([row]);self.assertTrue(result['motion_recipe_errors'])
        self.assertEqual(result['style']['motion_subject_zones'],[row]);self.assertEqual(self.f.calls,[])
        self.assertEqual(Path(old['file']).read_bytes(),original)
        with self.assertRaisesRegex(fixture.capcut_edit.VideoEditError,'motion'):
            fixture.render.render_draft(self.path,str(self.folder/'output'))

    def test_stale_form_refuses_before_mutating_any_metadata(self):
        self.save([zone()]);before=self.f.files()
        with self.assertRaises(video_editor.HTTPException) as error:self.save([])
        self.assertEqual(error.exception.status_code,409);self.assertEqual(self.f.files(),before)

    def test_receipt_failure_restores_prior_style_and_keeps_browser_active(self):
        identity.issue('clip',self.path);active=identity.state_dir('clip',self.path)/'active.json';pointer=active.read_bytes();before=self.f.files();write=transaction._write
        def fail(file,value):
            if file.parent.name=='receipts':raise OSError('receipt failure')
            return write(file,value)
        with patch.object(transaction,'_write',side_effect=fail),self.assertRaisesRegex(OSError,'receipt failure'):
            self.save([zone()])
        self.assertEqual(self.f.files(),before);self.assertEqual(active.read_bytes(),pointer)

    def test_browser_packet_receives_regions_and_old_answer_becomes_stale(self):
        import web_ai_json as web
        answer={'clipkit_web_ai':2,'step':'clip',**identity.issue('clip',self.path)}
        active=identity.state_dir('clip',self.path)/'active.json';pointer=active.read_bytes()
        self.save([zone()]);self.assertEqual(active.read_bytes(),pointer)
        with self.assertRaises(fixture.capcut_edit.VideoEditError):identity.verify('clip',self.path,answer)
        with patch.object(web,'_motion_menu',return_value=[]):text=web.export_clip(self.path).read_text(encoding='utf-8')
        data=json.loads(text.rsplit('```json\n',1)[1].split('\n```',1)[0])
        self.assertEqual(data['motion_subject_zones'],[zone()])
        self.assertNotIn(self.path,json.dumps(data['motion_subject_zones']))

    def test_generic_style_route_cannot_bypass_region_validation(self):
        before=self.f.files();row=zone();row['w']=2
        with self.assertRaises(video_editor.HTTPException):
            video_editor.draft_look(video_editor.LookRequest(path=self.path,style={'motion_subject_zones':[row]}))
        self.assertEqual(self.f.files(),before)


if __name__=='__main__':unittest.main()
