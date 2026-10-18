"""Stored motion is replanned against actual cue/layout state before every export adapter."""
import copy
import json
import shutil
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import motion_cut_test as fixture
import capcut_build
import hf_build

render = fixture.render
motion_plan = fixture.motion_plan


class MotionFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.MotionCutTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.folder = self.f.folder
        self.old = self.f.install()

    def timeline(self):
        return render.render_draft(self.f.path, '', dry=True)

    def reject_export(self):
        timeline = self.timeline()
        self.assertTrue(timeline['motion_recipe_errors'])
        with self.assertRaisesRegex(render.VideoEditError, 'motion เก่า'):
            render.render_draft(self.f.path, str(self.folder/'exports'))
        with patch.object(render, 'render_draft', return_value=timeline), \
                patch.object(hf_build, '_proxy') as proxy, \
                self.assertRaisesRegex(render.VideoEditError, 'motion เก่า'):
            hf_build._build(self.f.path)
        proxy.assert_not_called()
        with patch.object(render, 'render_draft', return_value=timeline), \
                patch.object(capcut_build, '_machine_paths', side_effect=AssertionError('machine resources touched before preflight')) as machine, \
                patch.object(capcut_build.shutil, 'copytree') as copytree, \
                self.assertRaisesRegex(render.VideoEditError, 'motion เก่า'):
            capcut_build._build(self.f.path, self.folder/'native-stage', self.folder/'native-final')
        copytree.assert_not_called()
        machine.assert_not_called()

    def test_current_recipe_is_read_only_and_uses_supplied_timeline_without_recursion(self):
        before = self.f.files()
        timeline = self.timeline()
        self.assertEqual(timeline['motion_recipe_errors'], [])
        with patch.object(render, 'render_draft', side_effect=AssertionError('recursive dry run')):
            self.assertEqual(motion_plan.verify_timeline(self.folder, timeline), [])
        self.assertEqual(self.f.files(), before)

    def test_media_clock_allows_frame_quantization_but_rejects_invalid_or_long_duration(self):
        for duration in (4.000000000000001, 4 + 1/30):
            row=copy.deepcopy(self.old);row['recipe']['media']['duration']=duration
            self.f.f.write('clipkit_overlays.json',[row])
            self.assertEqual(self.timeline()['motion_recipe_errors'],[])
        for duration, fps in ((4.04,30),(float('nan'),30),(4,0),(4,True),(4,'30')):
            with self.subTest(duration=duration,fps=fps):
                row=copy.deepcopy(self.old);row['recipe']['media'].update(duration=duration,fps=fps)
                self.f.f.write('clipkit_overlays.json',[row])
                self.assertTrue(self.timeline()['motion_recipe_errors'])

    def test_changed_word_clock_is_visible_for_repair_but_stops_all_export_adapters(self):
        words = self.f.f.read('clipkit_words.json')
        words['normal keyword'][1][0] = 1.8
        self.f.f.write('clipkit_words.json', words)
        self.reject_export()

    def test_changed_preset_size_refuses_old_pixels(self):
        presets = self.folder.parent/'presets'
        presets.mkdir()
        preset = json.loads((render.PRESETS/'default.json').read_text())
        preset['normal']['size'] *= .9
        (presets/'default.json').write_text(json.dumps(preset))
        with patch.object(render,'PRESETS',presets):
            self.reject_export()

    def test_changed_caption_refuses_old_pixels(self):
        self.f.f.write('clipkit_style.json',{'anim':'pair','preset':'with-caption','music':'','sfx':False})
        self.f.f.write('clipkit_caption.json',{'cue:0':'original caption','cue:1':'next caption'})
        self.f.f.write('clipkit_overlays.json',[])
        self.f.install()
        self.assertEqual(self.timeline()['motion_recipe_errors'],[])
        self.f.f.write('clipkit_caption.json',{'cue:0':'changed caption','cue:1':'next caption'})
        self.reject_export()

    def test_changed_media_or_missing_alpha_attestation_refuses_export(self):
        original = Path(self.old['file']).read_bytes()
        Path(self.old['file']).write_bytes(b'changed same-length media')
        self.assertTrue(self.timeline()['motion_recipe_errors'])
        Path(self.old['file']).write_bytes(original)
        row = copy.deepcopy(self.old)
        row['recipe']['media']['alpha_decode'] = False
        self.f.f.write('clipkit_overlays.json', [row])
        self.reject_export()

    def test_mismatched_recipe_params_bounds_or_canvas_are_not_trusted(self):
        for key, value in [('params', {}), ('bounds',[0,0,1,1]), ('canvas',{'width':1,'height':1})]:
            with self.subTest(key=key):
                row = copy.deepcopy(self.old)
                row[key] = value
                self.f.f.write('clipkit_overlays.json', [row])
                self.assertTrue(self.timeline()['motion_recipe_errors'])

    def test_malformed_recipe_inputs_or_media_remain_repairable_and_block_export(self):
        for key, value in [('inputs',[]),('inputs',{}),('hashes',None),('media',[])]:
            with self.subTest(key=key, value=value):
                row = copy.deepcopy(self.old)
                row['recipe'][key] = value
                self.f.f.write('clipkit_overlays.json',[row])
                self.reject_export()

    def test_template_or_font_hash_changes_refuse_recipe(self):
        row = copy.deepcopy(self.old)
        row['recipe']['hashes']['template'] = '0'*64
        self.f.f.write('clipkit_overlays.json', [row])
        self.reject_export()

    def test_regenerated_partial_cut_is_current_and_keeps_original_bytes(self):
        original = Path(self.old['file']).read_bytes()
        fixture.capcut_edit.trim_pauses(self.f.path, ranges=[(3.25,3.75)])
        self.assertEqual(self.timeline()['motion_recipe_errors'], [])
        self.assertEqual(Path(self.old['file']).read_bytes(), original)

    def test_legacy_without_recipe_is_not_fabricated_as_verified(self):
        row = {key:self.old[key] for key in ('file','start','duration')}
        self.f.f.write('clipkit_overlays.json', [row])
        timeline = self.timeline()
        self.assertEqual(timeline['motion_recipe_errors'], [])
        self.assertTrue(timeline['motions'][0]['review_required'])
        self.assertNotIn('recipe', timeline['motions'][0])

    def test_export_snapshot_binds_custom_motion_font_without_writing_packet(self):
        font = self.folder/'custom.ttf'
        shutil.copy2(render.DEFAULT_FONT,font)
        fixture.video_editor.draft_overlay_add(fixture.video_editor.OverlayRequest(path=self.f.path,
            template='hook-title',start=0,end=1,text='title',params={'key':'title','duration':1,'font':str(font)}))
        state = fixture.identity.state_dir('clip',self.folder)
        before = {p.relative_to(state).as_posix():p.read_bytes() for p in state.rglob('*') if p.is_file()}
        check = fixture.identity.export_check(self.folder)
        check()
        self.assertEqual({p.relative_to(state).as_posix():p.read_bytes() for p in state.rglob('*') if p.is_file()},before)
        font.write_bytes(b'new font bytes')
        with self.assertRaisesRegex(render.VideoEditError, 'เปลี่ยนระหว่างส่งออก'):
            check()

    def test_export_snapshot_binds_native_metadata_and_existing_output(self):
        output = self.folder.parent/'approved.mp4'
        output.write_bytes(b'approved')
        check = fixture.identity.export_check(self.folder,output)
        self.f.f.write('draft_meta_info.json',{'tm_duration':7000000})
        with self.assertRaisesRegex(render.VideoEditError,'เปลี่ยนระหว่างส่งออก'):
            check()
        check = fixture.identity.export_check(self.folder,output)
        output.write_bytes(b'new external approval')
        with self.assertRaisesRegex(render.VideoEditError,'เปลี่ยนระหว่างส่งออก'):
            check()
        self.assertEqual(output.read_bytes(),b'new external approval')


if __name__ == '__main__':
    unittest.main()
