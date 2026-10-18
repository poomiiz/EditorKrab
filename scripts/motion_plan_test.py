"""Cue/role ownership and unchanged clocks shared by the motion output adapters."""
import copy
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "scripts")]
with tempfile.TemporaryDirectory() as folder:
    config = Path(folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import render
import capcut_font_test
import hf_build
import insert_plan
import kitconfig
import motion_plan


def policy(mode="decorate", targets=None, caption_policy="preserve"):
    return {"mode": mode, "targets": targets or [], "caption_policy": caption_policy}


class MotionPlanTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        config = Path(folder) / "config.json"
        config.write_text("{}", encoding="utf-8")
        self.enterContext(patch.object(kitconfig, "_CANDIDATES", [config]))

    def test_decoration_preserves_every_role_and_input(self):
        motion = {"start": 1, "duration": 2, "ownership": policy()}
        before = copy.deepcopy(motion)
        for role in motion_plan.ROLES:
            self.assertEqual(render.visible_spans(0, 4, [motion], role=role, cue_id="a"), [(0, 4)])
        self.assertEqual(motion, before)

    def test_replacement_names_the_actual_cue_occurrence_and_roles(self):
        targets = [{"cue_id": "a", "roles": ["emphasis", "second"]}]
        motion = {"start": 1.123456, "duration": .5, "ownership": policy("replace", targets)}
        for cue in ("a", "b"):
            for role in motion_plan.ROLES:
                expected = [(0, 1.123456), (1.623456, 4)] if cue == "a" and role in ("emphasis", "second") else [(0, 4)]
                self.assertEqual(render.visible_spans(0, 4, [motion], role=role, cue_id=cue), expected)

    def test_full_card_requires_an_explicit_caption_policy(self):
        for caption_policy in ("preserve", "replace", "hide"):
            motion = {"start": 1, "duration": 2, "ownership": policy("full_card", caption_policy=caption_policy)}
            for role in motion_plan.ROLES:
                expected = [(0, 4)] if role == "caption" and caption_policy == "preserve" else [(0, 1), (3, 4)]
                self.assertEqual(render.visible_spans(0, 4, [motion], role=role, cue_id="a"), expected)
        del motion["ownership"]["caption_policy"]
        with self.assertRaises(render.VideoEditError):
            motion_plan.ownership(motion)

    def test_invalid_ownership_cannot_become_legacy_or_hide_captions(self):
        invalid = [None, {}, policy("legacy_full_card"), policy("decorate", [{"cue_id": "a", "roles": ["normal"]}]),
                   policy(caption_policy="hide"), policy("replace"),
                   policy("replace", [{"cue_id": "a", "roles": ["caption"]}]),
                   policy("replace", [{"cue_id": "a", "roles": ["normal"]}], "replace"),
                   policy("replace", [{"cue_id": "a", "roles": ["normal", "normal"]}]),
                   policy("replace", [{"cue_id": "", "roles": ["normal"]}]),
                   policy("replace", [{"cue_id": "a", "roles": ["unknown"]}]),
                   policy("replace", [{"cue_id": "a", "roles": [{"bad": 1}]}]),
                   {**policy(), "extra": True}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(render.VideoEditError):
                motion_plan.ownership({"ownership": value})
        with self.assertRaisesRegex(render.VideoEditError, "role"):
            render.visible_spans(0, 4, [{"start": 1, "duration": 1, "ownership": policy()}])

    def test_legacy_is_conservative_and_is_not_a_fabricated_recipe(self):
        motion = {"start": 1, "duration": 2, "file": "old.mov"}
        self.assertEqual(motion_plan.ownership(motion)["mode"], "legacy_full_card")
        self.assertEqual(render.visible_spans(0, 4, [motion]), [(0, 1), (3, 4)])
        self.assertNotIn("ownership", motion)

    def test_shared_anchor_preserves_indices_and_subframe_word_clock(self):
        points = [{"cue_id": "a", "phrase": 0, "punch": "key", "start": .123456, "hit": .234567,
                   "words": [{"word": 3, "start": .234567}]},
                  {"cue_id": "b", "phrase": 0, "punch": "key", "start": 2.123456, "hit": 2.234567,
                   "words": [{"word": 3, "start": 2.234567}]}]
        for cue, expected in (("a", .234567), ("b", 2.234567)):
            for anchor in ({"kind": "keyword", "cue_id": cue, "phrase": 0},
                           {"kind": "word", "cue_id": cue, "word": 3}):
                self.assertEqual(insert_plan.resolve_anchor(None, anchor, points=points)[0], expected)
        invalid = [{"kind": "word", "cue_id": "a", "word": 2},
                   {"kind": "word", "cue_id": "a", "word": True},
                   {"kind": "phrase", "cue_id": "a", "phrase": True},
                   {"kind": "timeline", "start": float("nan")}, {"kind": "timeline", "start": True}]
        for anchor in invalid:
            with self.subTest(anchor=anchor), self.assertRaises(render.VideoEditError):
                insert_plan.resolve_anchor(None, anchor, points=points)

    def timeline(self, W, H, motion):
        look = render.pair_look({}, W, H)
        look.update(lead=(20, .25, [1, 1, 1], [0, 0, 0], .08),
                    punch=(25, -.1, [1, .5, 0], [0, 0, 0], .08),
                    caption=(10, -.4, [1, 1, 1], [0, 0, 0], .05), caption_lang="th")
        phrases = [{"start": a, "end": a + 1.5, "hit": a + 1, "lead": "normal", "punch": "key",
                    "line": "normal key", "cue_id": cue, "opts": {}, "k": 0}
                   for cue, a in (("a", 0), ("b", 1.5))]
        family, font = render._font(str(render.DEFAULT_FONT))
        events = render.pair_events(phrases, look, W, H, family, font, held={"motions": [motion]})
        return {"style": {}, "zoom": [], "width": W, "height": H, "duration": 3, "look": "pair",
                "phrases": phrases, "subtitle_events": events, "hook": [], "broll": [], "motions": [motion],
                "sfx": {"on": False}, "music": {}, "fit": {"x": 0, "y": 0, "w": W, "h": H},
                "source": "fixture.mp4", "segments": [], "progress": False, "emoji": [], "pops": []}

    def test_three_adapters_keep_captions_and_unowned_repeated_cue(self):
        for W, H in ((1080, 1920), (1920, 1080), (1080, 1080)):
            with self.subTest(canvas=(W, H)), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                source = base / "motion.mov"
                source.write_bytes(b"generated serializer fixture; media probe mocked")
                motion = {"file": str(source), "start": .5, "duration": 2,
                          "ownership": policy("replace", [{"cue_id": "a", "roles": ["emphasis"]}])}
                timeline = self.timeline(W, H, motion)
                events = timeline["subtitle_events"]
                self.assertEqual(len(events), 5)
                self.assertFalse(any(e["cue_id"] == "a" and e["role"] == "emphasis" for e in events))
                self.assertEqual(len([e for e in events if e["role"] == "caption"]), 2)
                self.assertEqual(next(e["start"] for e in events if e["role"] == "emphasis"), 2.5)
                self.assertTrue(all("Dialogue:" in render.subtitle_ass(e) for e in events))
                with patch.object(render, "render_draft", return_value=timeline), \
                        patch.object(hf_build, "_proxy", return_value="fixture.mp4"), \
                        patch.object(hf_build, "_motion", return_value="motion.webm"):
                    page = hf_build.build(folder).read_text(encoding="utf-8")
                actual = re.findall(r'class="clip subtitle-event" data-role="([^"]+)" data-start="([\d.]+)" data-duration="([\d.]+)"', page)
                expected = [(e["role"], f'{e["start"]:.6f}', f'{e["end"] - e["start"]:.6f}') for e in events]
                self.assertEqual(actual, expected)
                harness = capcut_font_test.CapCutFontTests()
                harness.setUp()
                try:
                    with patch.object(render.video_edit, "probe", return_value={"duration": 3, "width": W, "height": H}):
                        _, (output, _) = harness.run_build(base, timeline=timeline)
                    draft = json.loads((output / "draft_content.json").read_text(encoding="utf-8"))
                    actual = sorted((s["render_index"], s["target_timerange"]["start"], s["target_timerange"]["duration"])
                                    for track in draft["tracks"] if track["type"] == "text" for s in track["segments"])
                    roles = {"normal": 14001, "emphasis": 14002, "caption": 14003, "second": 14005}
                    expected = sorted((roles[e["role"]], round(e["start"] * 1e6), round((e["end"] - e["start"]) * 1e6)) for e in events)
                    self.assertEqual(actual, expected)
                finally:
                    harness.doCleanups()


if __name__ == "__main__":
    unittest.main()
