"""Motion API ownership and explicit replacement; renderer boundary is mocked."""
import json
import os
from pathlib import Path
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
        import video_editor
import kit_settings
import kitconfig
import render
import motion_plan
import host_platform
import jobstore


class MotionApiTests(unittest.TestCase):
    def setUp(self):
        self.path = self.enterContext(tempfile.TemporaryDirectory())
        config = Path(self.path) / "config.json"
        config.write_text("{}", encoding="utf-8")
        self.enterContext(patch.object(kitconfig, "_CANDIDATES", [config]))
        self.file = Path(self.path) / video_editor.OVERLAYS
        (Path(self.path)/'draft_content.json').write_text('{"materials":{"videos":[],"texts":[]}}')
        for name in ('old.mov','other.mov','original.mov','fixture.mov'):
            (Path(self.path)/name).write_bytes(b'synthetic media; renderer boundary mocked')
        self.enterContext(patch.object(host_platform,'capcut_running',return_value=False))
        self.enterContext(patch.object(jobstore,'STATE',Path(self.path)/'state'))
        self.timeline = {"style": {}, "width": 1080, "height": 1920, "duration": 8, "look": "pair",
                         "phrases": [], "hook": [], "motions": []}
        self.enterContext(patch.object(render,"render_draft",side_effect=lambda *args,**kwargs:self.timeline))
        self.enterContext(patch.object(video_editor,"_overlay_revision",return_value="a" * 64))
        self.make = self.enterContext(patch.object(kit_settings, "make_motion", side_effect=self.render))

    def render(self, body):
        plan=motion_plan.prepare_template(body.template,body.params,width=body.width,height=body.height,placement=body.placement)
        return {"file":str(Path(self.path)/'fixture.mov'),"recipe":{**plan,"version":1}}

    def req(self, **values):
        return video_editor.OverlayRequest(path=self.path, template="hook-title", start=1.123456,
                                          end=3.234567, text="literal", **values)

    def test_manual_preview_keeps_metadata_and_does_not_allocate_renderer(self):
        self.file.write_text('[{"start":6,"duration":1,"file":"original.mov"}]',encoding='utf-8')
        before=self.file.read_bytes()
        result=video_editor.draft_overlay_preview(self.req())
        self.make.assert_not_called();self.assertEqual(self.file.read_bytes(),before)
        self.assertEqual(result['start'],1.123456)
        self.assertNotIn(self.path,json.dumps(result))
        with patch.object(video_editor.project_lock,'writer',side_effect=video_editor.VideoEditError('busy')), \
                self.assertRaises(video_editor.HTTPException) as error:
            video_editor.draft_overlay_preview(self.req())
        self.assertEqual(error.exception.status_code,400)

    def test_close_start_never_deletes_old_motion_or_allocates_renderer(self):
        before = [{"placement_id": "old", "start": 1.13, "duration": .5, "file": "old.mov"}]
        self.file.write_text(json.dumps(before), encoding="utf-8")
        original = self.file.read_bytes()
        with self.assertRaises(video_editor.HTTPException) as error:
            video_editor.draft_overlay_add(self.req())
        self.assertEqual(error.exception.status_code, 409)
        self.make.assert_not_called()
        self.assertEqual(self.file.read_bytes(), original)

    def test_explicit_id_replaces_only_the_selected_recipe_and_preserves_clock(self):
        before = [{"placement_id": "old", "start": 1.13, "duration": .5, "file": "old.mov"},
                  {"placement_id": "other", "start": 4, "duration": .5, "file": "other.mov"}]
        self.file.write_text(json.dumps(before), encoding="utf-8")
        out = video_editor.draft_overlay_add(self.req(placement_id="old"))["overlays"]
        self.assertEqual(out[1], before[1])
        self.assertEqual(out[0]["placement_id"], "old")
        self.assertEqual(out[0]["start"], 1.123456)
        self.assertEqual(out[0]["duration"], 3.234567 - 1.123456)
        self.assertEqual(out[0]["params"]["key"], "literal")

    def test_missing_or_duplicate_id_cannot_render_or_remove(self):
        before = [{"placement_id": "same", "start": 1, "duration": 1},
                  {"placement_id": "same", "start": 3, "duration": 1}]
        self.file.write_text(json.dumps(before), encoding="utf-8")
        original = self.file.read_bytes()
        for selected in ("missing", "same"):
            with self.subTest(selected=selected), self.assertRaises(video_editor.HTTPException):
                video_editor.draft_overlay_add(self.req(placement_id=selected))
            with self.subTest(selected=selected), self.assertRaises(video_editor.HTTPException):
                video_editor.draft_overlay_remove(video_editor.OverlayDelete(path=self.path, placement_id=selected))
        self.make.assert_not_called()
        self.assertEqual(self.file.read_bytes(), original)

    def test_remove_by_id_or_exact_legacy_time_cannot_remove_nearby_items(self):
        before = [{"placement_id": "one", "start": 1, "duration": .01},
                  {"start": 1.03, "duration": .01}, {"start": 1.06, "duration": .01}]
        self.file.write_text(json.dumps(before), encoding="utf-8")
        rows = video_editor.draft_overlay_remove(video_editor.OverlayDelete(path=self.path, placement_id="one"))["overlays"]
        self.assertEqual(rows, before[1:])
        rows = video_editor.draft_overlay_remove(video_editor.OverlayDelete(path=self.path, start=1.03))["overlays"]
        self.assertEqual(rows, before[2:])
        with self.assertRaises(video_editor.HTTPException):
            video_editor.draft_overlay_remove(video_editor.OverlayDelete(path=self.path, start=1.04))

    def test_sentence_pair_owns_only_its_cue_including_caption_it_reproduces(self):
        phrase = {"start": 1, "end": 3, "hit": 2, "lead": "normal", "punch": "key",
                  "line": "normal key", "cue_id": "chosen", "opts": {}, "k": 0}
        timeline = {"style": {}, "width": 1080, "height": 1920, "duration": 3,"look":"pair", "phrases": [phrase], "hook": [], "motions": []}
        base = render.pair_events_from_timeline(timeline)
        # A neighboring held role can intersect this interval without belonging to this cue.
        neighbor = render.check_pair_events([dict(base[0], cue_id="neighbor", role="caption", text="n",
            center_x_px=200,center_y_px=300,size_px=20,outline_px=2,effect="none",start=0,end=3)],1080,1920)[0]
        caption = dict(base[0], role="caption", center_y_px=300, size_px=20, outline_px=2, effect="none")
        with patch.object(render, "render_draft", return_value=timeline), \
                patch.object(render, "pair_events_from_timeline", return_value=base + [caption, neighbor]):
            req = video_editor.OverlayRequest(path=self.path, template="sentence-pair", start=1, end=3,
                                              text="normal key", cue_id="chosen")
            row = video_editor.draft_overlay_add(req)["overlays"][0]
        self.assertEqual({e["cue_id"] for e in self.make.call_args.args[0].params["subtitle_events"]}, {"chosen"})
        self.assertEqual(row["cue_id"], "chosen")
        self.assertEqual(row["ownership"], {"mode": "replace", "targets": [
            {"cue_id": "chosen", "roles": ["caption", "emphasis", "normal"]}], "caption_policy": "replace"})
        self.assertIn("params", row)

    def test_failed_render_preserves_existing_metadata(self):
        before = [{"placement_id": "old", "start": 1.13, "duration": .5, "file": "old.mov"}]
        self.file.write_text(json.dumps(before), encoding="utf-8")
        original = self.file.read_bytes()
        self.make.side_effect = RuntimeError("synthetic renderer failure")
        with self.assertRaisesRegex(RuntimeError, "renderer failure"):
            video_editor.draft_overlay_add(self.req(placement_id="old"))
        self.assertEqual(self.file.read_bytes(), original)

    def test_generic_renderer_receives_the_project_canvas_and_stores_its_recipe(self):
        self.timeline.update(width=1920,height=1080)
        row=video_editor.draft_overlay_add(self.req())["overlays"][0]
        body=self.make.call_args.args[0]
        self.assertEqual((body.width,body.height),(1920,1080))
        self.assertEqual(row["canvas"],{"width":1920,"height":1080})
        self.assertEqual(row["ownership"],{"mode":"decorate","targets":[],"caption_policy":"preserve"})
        self.assertTrue(row["subject_review_pending"])

    def test_collision_with_preserved_caption_rejects_before_renderer(self):
        phrase={"start":0,"end":8,"hit":0,"lead":"caption","punch":"","line":"caption","cue_id":"keep","opts":{},"k":0}
        scene={**self.timeline,"phrases":[phrase]}
        event=render.pair_events_from_timeline(scene)[0]
        event=render.check_pair_events([dict(event,role="caption",center_y_px=600,size_px=20,outline_px=2,effect="none")],1080,1920)[0]
        with patch.object(render,"pair_events_from_timeline",return_value=[event]),self.assertRaises(video_editor.HTTPException):
            video_editor.draft_overlay_add(self.req())
        self.make.assert_not_called()
        self.assertFalse(self.file.exists())

    def test_confirmed_subject_zone_rejects_before_renderer(self):
        self.timeline["style"]={"motion_subject_zones":[{"start":0,"end":8,"x":.11,"y":.15,"w":.78,"h":.3}]}
        with self.assertRaises(video_editor.HTTPException) as error:
            video_editor.draft_overlay_add(self.req())
        self.assertIn("subject",str(error.exception.detail))
        self.make.assert_not_called()

    def test_word_anchor_rejects_a_guessed_start_before_renderer(self):
        points=[{"cue_id":"a","phrase":0,"punch":"key","start":1.5,"hit":1.5,"words":[{"word":3,"start":1.5}]}]
        with patch("insert_plan.opportunities",return_value=points),self.assertRaises(video_editor.HTTPException) as error:
            video_editor.draft_overlay_add(self.req(anchor={"kind":"word","cue_id":"a","word":3}))
        self.assertIn("anchor",str(error.exception.detail))
        self.make.assert_not_called()

    def test_changed_layout_result_cannot_publish_over_existing_metadata(self):
        before=[{"placement_id":"other","start":4,"duration":.5,"file":"other.mov"}]
        self.file.write_text(json.dumps(before),encoding="utf-8")
        original=self.file.read_bytes()
        def changed(body):
            out=self.render(body);out["recipe"]["layout_sha256"]="changed";return out
        self.make.side_effect=changed
        with self.assertRaises(video_editor.HTTPException) as error:
            video_editor.draft_overlay_add(self.req())
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(self.file.read_bytes(),original)

    def test_emphasis_only_motion_starts_at_its_word_while_normal_stays(self):
        phrase={"start":0,"end":3,"hit":1,"lead":"normal","punch":"key","line":"normal key",
                "cue_id":"chosen","opts":{},"k":0}
        self.timeline.update(duration=3,phrases=[phrase])
        policy={"mode":"replace","targets":[{"cue_id":"chosen","roles":["emphasis"]}],"caption_policy":"preserve"}
        req=video_editor.OverlayRequest(path=self.path,template="sentence-pair",start=1,end=3,
                                        text="normal key",cue_id="chosen",ownership=policy)
        row=video_editor.draft_overlay_add(req)["overlays"][0]
        self.assertEqual([e["role"] for e in row["params"]["subtitle_events"]],["emphasis"])
        self.assertEqual(row["params"]["subtitle_events"][0]["start"],0)
        self.assertEqual(render.visible_spans(0,3,[row],role="normal",cue_id="chosen"),[(0,3)])
        self.assertEqual(render.visible_spans(1,3,[row],role="emphasis",cue_id="chosen"),[])

    def test_simultaneous_disjoint_generic_recipes_append_but_collision_never_renders(self):
        first=self.req(placement={'x':.15,'y':.15,'w':.7,'h':.15})
        second=self.req(placement={'x':.15,'y':.55,'w':.7,'h':.15})
        initial=video_editor.draft_overlay_add(first)['overlays'][0]
        rows=video_editor.draft_overlay_add(second)['overlays']
        self.assertEqual(len(rows),2);self.assertEqual(rows[0],initial)
        self.assertEqual(rows[0]['start'],rows[1]['start'])
        self.assertEqual(self.make.call_count,2)
        before=self.file.read_bytes()
        with self.assertRaises(video_editor.HTTPException) as error:
            video_editor.draft_overlay_add(second)
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(self.make.call_count,2);self.assertEqual(self.file.read_bytes(),before)

    def test_unverified_or_stale_neighbor_refuses_before_concurrent_render(self):
        first=self.req(placement={'x':.15,'y':.15,'w':.7,'h':.15})
        second=self.req(placement={'x':.15,'y':.55,'w':.7,'h':.15})
        initial=video_editor.draft_overlay_add(first)['overlays'][0]
        for stale in (False,True):
            row={**initial,'recipe':dict(initial['recipe'])}
            if stale:
                self.timeline['motion_recipe_errors']=[{'placement_id':row['placement_id'],'error':'changed media'}]
            else:
                row['recipe'].pop('version')
            self.file.write_text(json.dumps([row]),encoding='utf-8');before=self.file.read_bytes()
            with self.assertRaises(video_editor.HTTPException) as error:
                video_editor.draft_overlay_add(second)
            self.assertEqual(error.exception.status_code,409)
            self.assertEqual(self.file.read_bytes(),before);self.assertEqual(self.make.call_count,1)


if __name__ == "__main__":
    unittest.main()
