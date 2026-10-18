"""Native still/pop geometry is shared by ASS, HF, native output and motion planning."""
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import motion_cut_test as fixture
import capcut_build
import hf_build
import capcut_font_test as native_fixture

render, api, web = fixture.render, fixture.video_editor, fixture.web


class PlainSubtitleTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.MotionCutTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.folder, self.path = self.f.folder, self.f.path
        self.draft = self.f.f.read('draft_content.json')
        self.style = {'anim': 'none', 'music': '', 'sfx': False}
        self.save()

    def save(self):
        self.f.f.write('draft_content.json', self.draft)
        self.f.f.write('clipkit_style.json', self.style)

    def timeline(self):
        return render.render_draft(self.path, '', dry=True)

    def test_real_clock_hold_and_native_geometry_at_all_ratios(self):
        segment = self.draft['tracks'][1]['segments'][0]
        segment['clip'].update(rotation=17, transform={'x': .1, 'y': -.2})
        for width, height in ((1080, 1920), (1920, 1080), (1080, 1080)):
            for effect in ('none', 'pop'):
                with self.subTest(canvas=(width, height), effect=effect):
                    self.draft['canvas_config'].update(width=width, height=height)
                    self.style['anim'] = effect
                    self.save()
                    timeline = self.timeline()
                    events = timeline['subtitle_events']
                    first = next(e for e in events if e['cue_id'] == '0')
                    self.assertEqual((first['start'], first['end']), (2, 6))
                    self.assertEqual(first['rotation'], 17)
                    cue = [e for e in events if e['cue_id'] == '0']
                    self.assertAlmostEqual(sum(e['center_x_px'] for e in cue) / len(cue), width * .55)
                    self.assertEqual(first['effect'], effect)
                    self.assertEqual(events, web.validate_subtitles(self.path))
                    self.assertEqual(events, render.check_pair_events(events, width, height))
                    self.assertIn('\\pos(', render.subtitle_ass(first))

    def test_multiline_long_text_remains_simultaneous_and_safe(self):
        text = 'ข้อความยาวทดสอบพื้นที่ปลอดภัย ' * 12 + '\n' + 'Second line ' * 12
        self.draft['materials']['texts'][0]['content'] = json.dumps({'text': text, 'styles': []})
        self.f.f.write('clipkit_words.json', {})
        self.f.f.write('clipkit_punch.json', {})
        self.style['anim'] = 'pop'
        self.save()
        events = [e for e in self.timeline()['plain_events'] if e['cue_id'] == '0']
        self.assertEqual(len(events), 2)
        self.assertEqual({(e['start'], e['end']) for e in events}, {(2, 6)})
        self.assertEqual(events[0]['size_px'], events[1]['size_px'])
        self.assertLessEqual(events[0]['bbox'][3], events[1]['bbox'][1] + .05)
        self.assertEqual(''.join(e['text'] for e in events), text.replace('\n', ''))

    def test_native_serializer_preserves_plain_color_font_and_outline(self):
        style = {'font': {'path': str(render.DEFAULT_FONT)},
                 'fill': {'content': {'solid': {'color': [.2, .7, .4]}}},
                 'strokes': [{'width': .04, 'content': {'solid': {'color': [.1, .2, .3]}}}]}
        self.draft['materials']['texts'][0]['content'] = json.dumps({'text': 'normal keyword', 'styles': [style]})
        self.save()
        event = self.timeline()['plain_events'][0]
        templates = copy.deepcopy(capcut_build._T)
        self.addCleanup(lambda: (capcut_build._T.clear(), capcut_build._T.update(templates)))
        capcut_build._T.update(json.loads(capcut_build.TEMPLATES.read_text(encoding='utf-8')))
        native = {'materials': {'texts': []}}
        segment = capcut_build._event_text(native, 'white', event, 1920, 1080)
        written = native['materials']['texts'][0]
        actual = json.loads(written['content'])['styles'][0]
        self.assertEqual(written['font_path'], event['font_file'])
        self.assertEqual(actual['fill']['content']['solid']['color'], [.2, .7, .4])
        self.assertAlmostEqual(actual['strokes'][0]['width'], .04)
        self.assertAlmostEqual(segment['clip']['transform']['y'], 1 - 2 * event['center_y_px'] / 1080)

    def test_hf_uses_shared_events_and_copies_each_native_font(self):
        second = self.folder / 'second.ttf'
        second.write_bytes(render.DEFAULT_FONT.read_bytes())
        self.draft['materials']['texts'][1]['font_path'] = str(second)
        self.save()
        expected = self.timeline()['subtitle_events']
        with patch.object(hf_build, '_proxy', return_value='generated.mp4'):
            page = hf_build.build(self.path).read_text(encoding='utf-8')
        self.assertEqual(page.count('class="clip subtitle-event"'), len(expected))
        self.assertIn('font-family:ClipKitSubtitle0;', page)
        self.assertIn('font-family:ClipKitSubtitle1;', page)
        for event in expected:
            self.assertIn(f'left:{event["center_x_px"]:.6f}px;top:{event["center_y_px"]:.6f}px;', page)
        for reference in re.findall(r'@font-face\{[^}]*src:url\("([^"]+)"\)', page):
            self.assertTrue((self.folder / 'clipkit_hf' / reference).is_file())

    def test_native_build_uses_measured_plan_instead_of_default_white_16(self):
        self.draft['materials']['texts'][0]['content'] = json.dumps({'text': 'First line\nSecond line', 'styles': []})
        self.f.f.write('clipkit_words.json', {})
        self.f.f.write('clipkit_punch.json', {})
        self.save()
        timeline = self.timeline()
        harness = native_fixture.CapCutFontTests()
        harness.setUp()
        try:
            with tempfile.TemporaryDirectory() as folder:
                _, (output, _) = harness.run_build(Path(folder), timeline=timeline)
                native = json.loads((output / 'draft_content.json').read_text(encoding='utf-8'))
            texts = {m['id']: m for m in native['materials']['texts']}
            segments = [s for t in native['tracks'] if t['type'] == 'text' for s in t['segments']]
            self.assertEqual(len(segments), len(timeline['subtitle_events']))
            expected = {(e['text'], round(e['start'] * 1000000)): e for e in timeline['subtitle_events']}
            tracks = [t for t in native['tracks'] if t['type'] == 'text' and t['segments']]
            self.assertEqual(len(tracks), 2, 'simultaneous normal lines need independent native tracks')
            for track in tracks:
                ranges = [s['target_timerange'] for s in track['segments']]
                self.assertTrue(all(a['start'] + a['duration'] <= b['start'] for a, b in zip(ranges, ranges[1:])))
            for segment in segments:
                text = json.loads(texts[segment['material_id']]['content'])['text']
                event = expected[text, segment['target_timerange']['start']]
                self.assertAlmostEqual(texts[segment['material_id']]['font_size'], event['size_px'] / render.CAPCUT_PX)
                self.assertEqual(segment['target_timerange'], {'start': round(event['start'] * 1000000), 'duration': round((event['end'] - event['start']) * 1000000)})
        finally:
            harness.doCleanups()

    def test_rotated_rows_follow_the_block_axis_without_wide_vertical_box_gaps(self):
        import math
        self.draft['materials']['texts'][0]['content'] = json.dumps({'text': 'First line\nSecond line', 'styles': []})
        self.f.f.write('clipkit_words.json', {})
        self.f.f.write('clipkit_punch.json', {})
        for rotation in (-45, -12, 0, 30, 90, 180):
            self.draft['tracks'][1]['segments'][0]['clip']['rotation'] = rotation
            self.style['anim'] = 'pop'
            self.save()
            first, second = [e for e in self.timeline()['plain_events'] if e['cue_id'] == '0']
            dx, dy = second['center_x_px'] - first['center_x_px'], second['center_y_px'] - first['center_y_px']
            angle = math.radians(rotation)
            self.assertAlmostEqual(dx * math.cos(angle) + dy * math.sin(angle), 0)
            self.assertAlmostEqual(math.hypot(dx, dy), (first['size_px'] * render.LINE_H + 2 * first['outline_px']) * 1.3)
            self.assertFalse(render._quads_intersect(first['quad'], second['quad']))

    def test_oriented_cells_allow_empty_aabb_corners_but_reject_real_overlap_and_peak_strokes(self):
        import math
        event = dict(self.timeline()['plain_events'][0], text='long parallel line', size_px=40,
                     center_x_px=800, center_y_px=400, outline_px=4, shadow=2, effect='pop')
        for rotation in (-45, 30, 90, 180):
            angle = math.radians(rotation)
            pitch = (40 * render.LINE_H + 12) * 1.3
            first = dict(event, rotation=rotation)
            second = dict(first, cue_id='different', center_x_px=800 - math.sin(angle) * (pitch + .1),
                          center_y_px=400 + math.cos(angle) * (pitch + .1))
            before = copy.deepcopy([first, second])
            measured = render.check_pair_events([first, second], 1920, 1080)
            self.assertEqual([first, second], before)
            self.assertFalse(render._quads_intersect(measured[0]['quad'], measured[1]['quad']))
            if rotation in (-45, 30):
                self.assertTrue(all(max(measured[0]['bbox'][k], measured[1]['bbox'][k]) < min(measured[0]['bbox'][k+2], measured[1]['bbox'][k+2]) for k in (0,1)))
            with self.assertRaisesRegex(render.VideoEditError, 'ข้อความทับกัน'):
                render.check_pair_events([first, dict(second, center_x_px=800 - math.sin(angle) * (pitch - 1),
                                                      center_y_px=400 + math.cos(angle) * (pitch - 1))], 1920, 1080)
        with self.assertRaisesRegex(render.VideoEditError, 'positive dimensions'):
            render.check_pair_events([dict(event, text='', outline_px=0, shadow=0)], 1920, 1080)

    def test_unrepresentable_rich_style_or_anisotropic_scale_is_never_silently_flattened(self):
        original = copy.deepcopy(self.draft)
        for kind in ('styled ranges', 'scale'):
            self.draft = copy.deepcopy(original)
            if kind == 'scale':
                self.draft['tracks'][1]['segments'][0]['clip']['scale']['y'] = 1.5
            else:
                self.draft['materials']['texts'][0]['content'] = json.dumps({'text': 'normal keyword', 'styles': [{}, {}]})
            self.save()
            before = self.f.files()
            with self.assertRaisesRegex(render.VideoEditError, kind):
                self.timeline()
            self.assertEqual(self.f.files(), before)

    def test_generic_motion_sees_plain_subtitles_and_sentence_replacement_uses_actual_roles(self):
        bad = api.OverlayRequest(path=self.path, template='hook-title', start=2, end=6, text='HOOK',
                                placement={'x': .2, 'y': .45, 'w': .6, 'h': .1})
        with self.assertRaises(api.HTTPException):
            api.prepare_overlay(bad)
        self.assertEqual(self.f.calls, [])
        good = bad.model_copy(update={'placement': {'x': .2, 'y': .12, 'w': .6, 'h': .1}})
        self.assertEqual(api.prepare_overlay(good)['ownership']['mode'], 'decorate')
        plan = api.prepare_overlay(api.OverlayRequest(path=self.path, template='sentence-pair', start=2, end=6,
                                                      text='normal keyword', cue_id='0'))
        self.assertEqual(plan['ownership']['targets'], [{'cue_id': '0', 'roles': ['normal']}])
        self.assertEqual(plan['prepared']['source_params']['subtitle_events'][0]['size_px'], self.timeline()['plain_events'][0]['size_px'])

    def test_hidden_normal_is_restored_in_underlying_motion_context(self):
        timeline = self.timeline()
        timeline['motions'] = [{'start': 3, 'duration': 1}]
        shown = render.pair_events_from_timeline(timeline)
        self.assertEqual([(e['start'], e['end']) for e in shown if e['cue_id'] == '0'], [(2, 3), (4, 6)])
        timeline['motions'] = []
        self.assertEqual([(e['start'], e['end']) for e in render.pair_events_from_timeline(timeline) if e['cue_id'] == '0'], [(2, 6)])

    def test_real_onset_quiet_cut_and_undo_share_the_retained_clock(self):
        words = self.f.f.read('clipkit_words.json')
        words['normal keyword'][0][0] = .25
        self.f.f.write('clipkit_words.json', words)
        before = self.f.files()
        self.assertEqual(self.timeline()['plain_events'][0]['start'], 2.25)
        api.draft_cut(api.CutRange(path=self.path, start=3.25, end=3.75))
        first = self.timeline()['plain_events'][0]
        self.assertEqual((first['start'], first['end']), (2.25, 5.5))
        api.draft_uncut(api.DraftPath(path=self.path))
        after = self.f.files()
        self.assertEqual({name: after[name] for name in before}, before)
        self.assertEqual(self.timeline()['plain_events'][0]['start'], 2.25)

    def test_invalid_inactive_preset_does_not_persist_in_plain_style(self):
        before = self.f.files()
        with self.assertRaises(api.HTTPException) as error:
            api.draft_look(api.LookRequest(path=self.path, style={'preset': 'does-not-exist'}))
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.f.files(), before)

    def test_browser_packet_contains_actual_plain_layout_without_font_paths(self):
        captured = {}
        def packet(step, target, prompt, data):
            captured.update(data)
            return self.folder / 'not-written.md'
        with patch.object(web, '_motion_menu', return_value=[]), patch.object(web, '_issue_packet', side_effect=packet):
            web.export_clip(self.path)
        layout = captured['subtitle_layout']
        self.assertEqual(layout['mode'], 'none')
        self.assertEqual(layout['events'][0]['end'], 6)
        self.assertEqual(layout['events'][0]['size_px'], self.timeline()['plain_events'][0]['size_px'])
        self.assertNotIn(str(self.folder), json.dumps(layout))
        self.assertTrue(all(Path(e['font_file']).name == e['font_file'] for e in layout['events']))

    def test_bad_native_geometry_refuses_instead_of_falling_back(self):
        original = copy.deepcopy(self.draft)
        for value in (float('nan'), -2, 0):
            self.draft = copy.deepcopy(original)
            self.draft['materials']['texts'][0]['font_size'] = value
            if value == 0:
                self.draft['tracks'][1]['segments'][0]['clip']['transform']['x'] = 10
            self.save()
            with self.assertRaises(render.VideoEditError):
                self.timeline()
        self.draft = copy.deepcopy(original)
        self.draft['materials']['texts'][0]['font_path'] = str(self.folder / 'missing.ttf')
        self.save()
        with self.assertRaisesRegex(render.VideoEditError, 'font file is missing'):
            self.timeline()

    def test_cpu_raster_ink_stays_inside_measured_box_at_all_ratios(self):
        from PIL import Image, ImageFont, __version__ as pillow_version
        for width, height in ((1080,1920), (1920,1080), (1080,1080)):
            self.draft['canvas_config'].update(width=width, height=height)
            self.draft['tracks'][1]['segments'][0]['clip']['rotation'] = -12
            self.style['anim'] = 'pop'
            self.save()
            event = self.timeline()['plain_events'][0]
            event = dict(event, start=0, end=1, effect_start=0)
            with tempfile.TemporaryDirectory() as folder:
                work=Path(folder);(work/'fonts').mkdir()
                shutil.copyfile(event['font_file'],work/'fonts/font.ttf')
                header=render.subtitle_ass_header(width, height, event['font_family'])
                self.assertIn('Kerning: yes\n', header)
                (work/'subs.ass').write_text(header+render.subtitle_ass(event)+'\n',encoding='utf-8')
                encoded = subprocess.run([render.FFMPEG,'-v','info','-y','-f','lavfi','-i',f'color=black:s={width}x{height}:d=0.1',
                    '-vf','ass=subs.ass:fontsdir=fonts','-frames:v','1','-threads','1','-filter_threads','1','frame.png'],
                    cwd=work,capture_output=True,check=True,timeout=30)
                image=Image.open(work/'frame.png').convert('L')
                ink=image.point(lambda value:255 if value>100 else 0).getbbox()
                self.assertIsNotNone(ink)
                x0,y0,x1,y1=event['bbox']
                inside = x0-2 <= ink[0] < ink[2] <= x1+2 and y0-2 <= ink[1] < ink[3] <= y1+2
                if not inside:
                    # Keep generated raster/provider evidence before the temporary fixture disappears.
                    proof = Path(__file__).resolve().parents[1] / 'bug_reports/macos-acceptance' / f'plain-raster-{width}x{height}'
                    proof.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(work/'frame.png', proof/'frame.png')
                    shutil.copyfile(work/'subs.ass', proof/'subs.ass')
                    font = ImageFont.truetype(event['font_file'], 100)
                    diagnostic = {'pillow':pillow_version, 'event':dict(event,font_file=Path(event['font_file']).name),
                        'ink':ink, 'font_advance_100':font.getlength(event['text']), 'font_bbox_100':font.getbbox(event['text'])}
                    (proof/'metrics.json').write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2),encoding='utf-8')
                    (proof/'ffmpeg.log').write_text(encoded.stderr.decode('utf-8',errors='replace').replace(str(work),'[fixture]'),encoding='utf-8')
                self.assertTrue(inside, (width,height,event['bbox'],ink))


if __name__ == '__main__':
    unittest.main()
