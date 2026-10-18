"""Native delivery builds publish new revisions; failures never touch an existing project."""
import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
with tempfile.TemporaryDirectory() as import_folder:
    config = Path(import_folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import capcut_build as build
        import draft_quality
        import delivery_manifest


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


class NativeBuildSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native safety ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "ทดสอบ native"
        self.project.mkdir()
        self.source = self.base / "footage.mp4"
        self.source.write_bytes(b"fixture media boundary")
        draft = {"id": "original", "duration": 1_000_000,
                 "canvas_config": {"width": 1080, "height": 1920},
                 "materials": {"videos": [{"id": "video", "path": str(self.source)}], "texts": []},
                 "tracks": [{"type": "video", "segments": [{"id": "segment", "material_id": "video",
                     "source_timerange": {"start": 0, "duration": 1_000_000},
                     "target_timerange": {"start": 0, "duration": 1_000_000},
                     "clip": {"scale": {"x": 1.0, "y": 1.0}}, "extra_material_refs": []}]}]}
        text = json.dumps(draft, ensure_ascii=False)
        (self.project / "draft_content.json").write_text(text, encoding="utf-8")
        timeline = self.project / "Timelines" / "original"
        timeline.mkdir(parents=True)
        (timeline / "draft_content.json").write_text(text, encoding="utf-8")
        (timeline.parent / "project.json").write_text('{"main_timeline_id":"original"}', encoding="utf-8")
        (self.project / "draft_meta_info.json").write_text(json.dumps({"draft_id": "original",
            "draft_name": self.project.name, "draft_fold_path": self.project.as_posix(),
            "draft_root_path": self.base.as_posix(), "draft_json_file": (self.project / "draft_content.json").as_posix(),
            "draft_cover": (self.project / "draft_cover.jpg").as_posix(), "tm_duration": 1_000_000}), encoding="utf-8")
        (self.project / "draft_cover.jpg").write_bytes(b"cover")
        build.capcut_edit._seal(self.project, text)
        self.existing = self.project.with_name(self.project.name + " · CapCut")
        self.existing.mkdir()
        (self.existing / "draft_content.json").write_bytes(b"human changes must survive")
        (self.existing / "keep.txt").write_bytes(b"private client work")
        self.before_source, self.before_existing = snapshot(self.project), snapshot(self.existing)
        self.timeline = {"style": {}, "zoom": [], "width": 1080, "height": 1920, "look": "plain",
                         "phrases": [], "hook": [], "broll": [], "sfx": {"on": False}, "music": {}}
        templates = json.loads(build.TEMPLATES.read_text(encoding="utf-8"))
        for role in ("white", "orange", "caption", "title"):
            material = templates[role]["mat"]
            material["font_path"] = build.render.DEFAULT_FONT.as_posix()
            body = json.loads(material["content"])
            for style in body["styles"]:
                style["font"]["path"] = build.render.DEFAULT_FONT.as_posix()
            material["content"] = json.dumps(body)
        fixture = self.base / "templates.json"
        fixture.write_text(json.dumps(templates), encoding="utf-8")
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(build, "TEMPLATES", fixture))
        stack.enter_context(patch.object(build, "_machine_paths", side_effect=lambda text: text))
        self.process = stack.enter_context(patch.object(build.host_platform, "capcut_running", return_value=False))
        stack.enter_context(patch.object(build.render, "render_draft", side_effect=lambda *a, **k: copy.deepcopy(self.timeline)))
        stack.enter_context(patch.object(draft_quality, "probe", return_value={"duration": 1.0}))

    def assert_preserved(self):
        self.assertEqual(snapshot(self.project), self.before_source)
        self.assertEqual(snapshot(self.existing), self.before_existing)
        self.assertFalse(any(".capcut-build-" in p.name for p in self.base.iterdir()))

    def test_success_publishes_valid_revision_with_final_metadata_and_timeline(self):
        build._T["previous_project_only"] = {"private": "do not reuse"}
        output, warnings = build.build(str(self.project))
        build.render.render_draft.assert_called_once_with(str(self.project.resolve()), "", dry=True, final=True)
        self.assertNotEqual(output, self.existing)
        self.assertTrue(output.name.startswith(self.project.name + " · CapCut"))
        self.assertEqual(warnings, [])
        meta = json.loads((output / "draft_meta_info.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["draft_name"], output.name)
        self.assertEqual(meta["draft_fold_path"], output.as_posix())
        self.assertEqual(meta["draft_json_file"], (output / "draft_content.json").as_posix())
        self.assertEqual(meta["draft_cover"], (output / "draft_cover.jpg").as_posix())
        draft = build.capcut_edit._load(output)
        self.assertNotEqual(draft["id"], "original")
        inner = output / "Timelines" / draft["id"] / "draft_content.json"
        self.assertEqual(inner.read_bytes(), (output / "draft_content.json").read_bytes())
        self.assertEqual((output / "clipkit_ai_version.json").read_bytes(), inner.read_bytes())
        self.assertTrue(draft_quality.check(str(output))["structural_pass"])
        self.assertGreaterEqual(self.process.call_count, 2)
        self.assertNotIn("previous_project_only", build._T)
        self.assert_preserved()

    def test_new_native_revision_excludes_source_ai_identity_and_receipts(self):
        state = self.project / ".clipkit-web-ai" / "receipts"
        state.mkdir(parents=True)
        (state / "private.json").write_text('{"target_id":"source project"}', encoding="utf-8")
        self.before_source = snapshot(self.project)
        output, _ = build.build(str(self.project))
        self.assertFalse((output / ".clipkit-web-ai").exists())
        self.assert_preserved()

    def test_another_build_preserves_every_byte_of_previous_revision(self):
        output, _ = build.build(str(self.project))
        (output / "draft_content.json").write_bytes(b"edited in CapCut by a person")
        before = snapshot(output)
        next_output, _ = build.build(str(self.project))
        self.assertNotEqual(next_output, output)
        self.assertEqual(snapshot(output), before)
        self.assert_preserved()

    def test_metadata_alias_path_is_resolved_before_relocating_delivery(self):
        spacer = self.base / "spacer"
        spacer.mkdir()
        alias = spacer / ".." / self.project.name
        file = self.project / "draft_meta_info.json"
        meta = json.loads(file.read_text(encoding="utf-8"))
        meta["draft_json_file"] = (alias / "draft_content.json").as_posix()
        file.write_text(json.dumps(meta), encoding="utf-8")
        before = snapshot(self.project)
        output, _ = build.build(str(self.project))
        actual = json.loads((output / "draft_meta_info.json").read_text(encoding="utf-8"))
        self.assertEqual(actual["draft_json_file"], (output / "draft_content.json").as_posix())
        self.assertEqual(snapshot(self.project), before)

    def test_capcut_running_is_refused_before_any_writes(self):
        before = snapshot(self.base)
        self.process.return_value = True
        with self.assertRaisesRegex(build.VideoEditError, "CapCut"):
            build.build(str(self.project))
        self.assertEqual(snapshot(self.base), before)

    def test_capcut_opening_before_publish_discards_only_owned_staging(self):
        self.process.side_effect = [False, True]
        unrelated = self.base / ".another.capcut-build-keep"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_bytes(b"different job")
        with self.assertRaisesRegex(build.VideoEditError, "CapCut"):
            build.build(str(self.project))
        self.assertEqual((unrelated / "keep.txt").read_bytes(), b"different job")
        unrelated.rename(self.base / "different job")
        self.assertEqual({p.name for p in self.base.iterdir() if p.name.startswith(self.existing.name)}, {self.existing.name})
        self.assert_preserved()

    def test_copy_failure_cleans_partial_staging_and_preserves_previous_delivery(self):
        def fail_copy(src, dst, **kwargs):
            Path(dst).mkdir()
            (Path(dst) / "partial.json").write_text("partial")
            raise OSError("disk full during copy")
        with patch.object(build.shutil, "copytree", side_effect=fail_copy):
            with self.assertRaisesRegex(OSError, "disk full"):
                build.build(str(self.project))
        self.assert_preserved()

    def test_manifest_failure_keeps_original_and_previous_native_delivery(self):
        with patch.object(delivery_manifest, "prepare", side_effect=OSError("manifest disk full")):
            with self.assertRaisesRegex(OSError, "manifest disk full"):
                build.build(str(self.project))
        self.assert_preserved()

    def test_failure_after_copy_publishes_no_half_project(self):
        with patch.object(build.capcut_edit, "_save", side_effect=OSError("write failed")):
            with self.assertRaisesRegex(OSError, "write failed"):
                build.build(str(self.project))
        self.assertEqual({p.name for p in self.base.iterdir() if p.name.startswith(self.existing.name)}, {self.existing.name})
        self.assert_preserved()

    def test_publish_failure_cleans_staging_and_preserves_old_revisions(self):
        rename = Path.rename
        def denied(path, target):
            if path.name == "draft" and ".capcut-build-" in path.parent.name:
                raise PermissionError("publication denied")
            return rename(path, target)
        with patch.object(Path, "rename", autospec=True, side_effect=denied):
            with self.assertRaisesRegex(PermissionError, "publication denied"):
                build.build(str(self.project))
        self.assert_preserved()

    def test_existing_revision_collision_is_refused_without_overwriting(self):
        fixed = build.uuid.UUID("00000000-0000-0000-0000-000000000001")
        collision = self.project.with_name(self.project.name + " · CapCut " + fixed.hex)
        collision.mkdir()  # Even an empty destination must survive rather than be replaced by rename.
        with patch.object(build.uuid, "uuid4", return_value=fixed):
            with self.assertRaisesRegex(build.VideoEditError, "already exists"):
                build.build(str(self.project))
        self.assertEqual(list(collision.iterdir()), [])
        self.assert_preserved()

    def test_invalid_generated_draft_is_not_published(self):
        original_save = build.capcut_edit._save
        def invalid(folder, draft, op):
            draft["tracks"][0]["segments"][0]["source_timerange"]["duration"] = 2_000_000
            return original_save(folder, draft, op)
        with patch.object(build.capcut_edit, "_save", side_effect=invalid):
            with self.assertRaisesRegex(build.VideoEditError, "durations must match"):
                build.build(str(self.project))
        self.assert_preserved()

    def test_missing_source_media_does_not_publish(self):
        self.source.unlink()
        with self.assertRaisesRegex(build.VideoEditError, "ไม่พบฟุตต้นทาง"):
            build.build(str(self.project))
        self.assert_preserved()

    def test_source_edit_during_native_build_is_preserved_without_publication(self):
        original_build = build._build
        def changed(*args):
            result = original_build(*args)
            self.source.write_bytes(b'external source edit during build')
            return result
        with patch.object(build, '_build', side_effect=changed), self.assertRaisesRegex(build.VideoEditError, 'เปลี่ยนระหว่างส่งออก'):
            build.build(str(self.project))
        self.assert_preserved()
        self.assertEqual(self.source.read_bytes(), b'external source edit during build')
        self.assertEqual(list(self.base.glob(self.project.name+' · CapCut *')), [])

    def test_changed_source_seal_is_not_accepted(self):
        content = self.project / "draft_content.json"
        content.write_bytes(content.read_bytes() + b" ")
        changed = snapshot(self.project)
        with self.assertRaisesRegex(build.VideoEditError, "ถูกแก้นอก"):
            build.build(str(self.project))
        self.assertEqual(snapshot(self.project), changed)
        self.assertEqual(snapshot(self.existing), self.before_existing)

    def test_concurrent_builds_get_distinct_valid_revisions_and_serialize_templates(self):
        active, peak = 0, 0
        monitor = threading.Lock()
        def timeline(*args, **kwargs):
            nonlocal active, peak
            with monitor:
                active += 1
                peak = max(peak, active)
            time.sleep(.03)
            with monitor:
                active -= 1
            return copy.deepcopy(self.timeline)
        with patch.object(build.render, "render_draft", side_effect=timeline):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outputs = list(pool.map(lambda _: build.build(str(self.project))[0], range(2)))
        self.assertEqual(peak, 1)
        self.assertEqual(len(set(outputs)), 2)
        for output in outputs:
            self.assertTrue(draft_quality.check(str(output))["structural_pass"])
        self.assert_preserved()


if __name__ == "__main__":
    unittest.main()
