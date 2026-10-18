"""Packets reject source/name collisions and stale edits without changing project files."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
with tempfile.TemporaryDirectory() as folder:
    config = Path(folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import web_ai_identity as identity
from video_edit import VideoEditError


class PacketIdentity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"actual source")
        self.transcript = Path(str(self.source) + ".transcript.json")
        self.write(self.transcript, [{"start": 0, "end": 3, "text": "spoken", "words": [[0, 3, "spoken"]]}])
        self.project = self.root / "project"
        self.project.mkdir()
        self.draft = {"id": "draft", "materials": {"videos": [{"path": str(self.source)}], "texts": []},
                      "canvas_config": {"width": 1080, "height": 1920}, "tracks": []}
        self.write(self.project / "draft_content.json", self.draft)
        self.write(self.project / "clipkit.json", {"raw": str(self.source)})
        self.write(self.project / "clipkit_style.json", {"anim": "pair", "preset": "fixed"})
        self.presets = self.root / "presets"
        self.presets.mkdir()
        self.font = self.root / "font.ttf"
        self.font.write_bytes(b"font version one")
        self.preset = self.presets / "fixed.json"
        self.write(self.preset, {"font": str(self.font), "fixed_size": True})
        self.motion = self.root / "motion"
        self.page = self.motion / "test" / "index.html"
        self.page.parent.mkdir(parents=True)
        self.page.write_text('<head><script type="application/json" id="clipkit-motion">{}</script></head>', encoding="utf-8")
        for attribute, value in (("PRESETS", self.presets), ("MOTION", self.motion)):
            current = patch.object(identity, attribute, value)
            current.start()
            self.addCleanup(current.stop)

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def answer(self, step="clip", target=None, preferences=None):
        target = target or (self.project if step == "clip" else self.source)
        return {"clipkit_web_ai": identity.VERSION, "step": step,
                **identity.issue(step, str(target), preferences)}

    def assert_stale(self, answer, step="clip", target=None):
        with self.assertRaises(VideoEditError):
            identity.verify(step, str(target or (self.project if step == "clip" else self.source)), answer)

    def test_same_name_different_source_and_target_cannot_use_packet(self):
        first = self.answer("stories")
        other = self.root / "different" / self.source.name
        other.parent.mkdir()
        other.write_bytes(self.source.read_bytes())
        self.write(Path(str(other) + ".transcript.json"), [])
        second = self.answer("stories", other)
        self.assertNotEqual(first["source_id"], second["source_id"])
        self.assertNotEqual(first["revision"], second["revision"])
        self.assert_stale(first, "stories", other)
        renamed = self.root / "other" / self.project.name
        renamed.parent.mkdir()
        import shutil
        shutil.copytree(self.project, renamed)
        clip = self.answer()
        identity.issue("clip", renamed)
        self.assert_stale(clip, target=renamed)

    def test_revision_covers_transcript_brief_target_and_source(self):
        mutations = [lambda: self.write(self.transcript, [{"text": "changed"}]),
                     lambda: Path(str(self.source) + ".brief.txt").write_text("new brief", encoding="utf-8"),
                     lambda: self.write(Path(str(self.source) + ".story-preferences.json"), {"max_minutes": 1}),
                     lambda: self.source.write_bytes(b"changed media")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                answer = self.answer("stories")
                mutate()
                self.assert_stale(answer, "stories")

    def test_clip_revision_covers_draft_clock_style_preset_font_and_template(self):
        mutations = [lambda: self.write(self.project / "draft_content.json", {**self.draft, "duration": 12}),
                     lambda: self.write(self.project / "clipkit_words.json", {"cue:one": [[0, 1, "word"]]}),
                     lambda: self.write(self.project / "clipkit_style.json", {"anim": "pair", "preset": "fixed", "safe_zone": "reels"}),
                     lambda: self.write(self.preset, {"font": str(self.font), "fixed_size": False}),
                     lambda: self.font.write_bytes(b"font version two"),
                     lambda: self.page.write_text(self.page.read_text(encoding="utf-8") + "new template", encoding="utf-8")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                answer = self.answer()
                mutate()
                self.assert_stale(answer)

    def test_clip_revision_covers_replaced_source_and_original_transcript(self):
        answer = self.answer()
        self.source.write_bytes(b"replacement")
        self.assert_stale(answer)
        answer = self.answer()
        self.write(self.transcript, [{"text": "corrected", "words": [[0, 1, "corrected"]]}])
        self.assert_stale(answer)

    def test_native_timeline_copy_registry_list_and_seal_changes_are_stale(self):
        inner = self.project / "Timelines" / "native-id" / "draft_content.json"
        inner.parent.mkdir(parents=True)
        self.write(inner, self.draft)
        registry = self.project / "Timelines" / "project.json"
        self.write(registry, {"main_timeline_id": "native-id"})
        mutations = [lambda: self.write(inner, {**self.draft, "native_manual_edit": True}),
                     lambda: self.write(registry, {"main_timeline_id": "other-id"}),
                     lambda: self.write(self.project / "clipkit_seal.json", {"draft_content": "accepted"}),
                     lambda: inner.rename(inner.with_name("removed-draft.json")),
                     lambda: self.write(inner, self.draft)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                answer = self.answer()
                mutate()
                self.assert_stale(answer)

    def test_import_dependency_change_invalidates_packet(self):
        dependency = self.root / "dependency.py"
        dependency.write_text("version = 1\n", encoding="utf-8")
        with patch.object(identity, "DEPENDENCIES", (str(dependency),)):
            answer = self.answer()
            dependency.write_text("version = 2\n", encoding="utf-8")
            self.assert_stale(answer)

    def test_verify_does_not_write_and_public_identity_has_no_local_paths(self):
        answer = self.answer()
        before = {str(file): (file.stat().st_mtime_ns, file.read_bytes()) for file in self.root.rglob("*") if file.is_file()}
        actual = identity.verify("clip", str(self.project), answer)
        after = {str(file): (file.stat().st_mtime_ns, file.read_bytes()) for file in self.root.rglob("*") if file.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(actual, {k: v for k, v in answer.items() if k not in ("clipkit_web_ai", "step")})
        self.assertNotIn(str(self.root), json.dumps(actual))
        self.assertNotIn(self.project.name, json.dumps(actual))
        self.assertNotIn(self.source.name, json.dumps(actual))

    def test_preferences_distinguish_explicit_natural_from_inherited(self):
        self.write(Path(str(self.source) + ".story-preferences.json"), {"max_minutes": 3})
        inherited = self.answer()
        self.assertEqual(inherited["preferences"], {"max_minutes": 3})
        explicit = self.answer(preferences={"max_minutes": None})
        self.assertEqual(explicit["preferences"], {"max_minutes": None})
        identity.verify("clip", str(self.project), explicit)
        self.assertNotEqual(inherited["revision"], explicit["revision"])
        selected = self.answer(preferences={"max_minutes": 2})
        self.assertEqual(selected["preferences"], {"max_minutes": 2})
        identity.verify("clip", str(self.project), selected)
        self.write(Path(str(self.source) + ".story-preferences.json"), {"max_minutes": 5})
        self.assert_stale(selected)

    def test_new_export_keeps_pending_answer_and_older_packet_record(self):
        old = self.answer("stories")
        pending = self.source.with_name(self.source.name + ".web-ai-stories.answer.json")
        pending.write_text(json.dumps(old), encoding="utf-8")
        new = self.answer("stories")
        self.assertNotEqual(old["packet_id"], new["packet_id"])
        self.assertEqual(old["revision"], new["revision"])
        self.assertTrue(pending.is_file())
        self.assertTrue((identity.state_dir("stories", self.source) / "packets" / (old["packet_id"] + ".json")).is_file())
        self.assert_stale(old, "stories")
        identity.verify("stories", str(self.source), new)

    def test_cache_only_reuses_media_sha_for_unchanged_canonical_source(self):
        original = identity._file
        source = identity._canonical(self.source)
        hashed = []
        def digest(path, required=False):
            if identity._canonical(path) == source:
                hashed.append(path)
            return original(path, required)
        with patch.object(identity, "_file", side_effect=digest):
            answer = self.answer("stories")
            identity.verify("stories", str(self.source), answer)
            self.answer("stories")
            self.assertEqual(len(hashed), 1)
            self.source.write_bytes(b"updated media")
            self.answer("stories")
            self.assertEqual(len(hashed), 2)

    def test_full_file_hashing_and_stale_checks_work_without_file_digest(self):
        import web_ai_transaction as transaction
        content = bytes(range(256)) * 8193
        self.source.write_bytes(content)
        expected = identity.hashlib.sha256(content).hexdigest()
        with patch.dict(identity.hashlib.__dict__):
            identity.hashlib.__dict__.pop("file_digest", None)
            self.assertEqual(identity._file(self.source), expected)
            self.assertEqual(transaction._hash(self.source), expected)
            answer = self.answer("stories")
            identity.verify("stories", str(self.source), answer)
            self.source.write_bytes(content[:-1] + b"different tail")
            self.assert_stale(answer, "stories")
            self.assertNotEqual(transaction._hash(self.source), expected)
            self.source.write_bytes(b"")
            empty = identity.hashlib.sha256(b"").hexdigest()
            self.assertEqual(identity._file(self.source), empty)
            self.assertEqual(transaction._hash(self.source), empty)

    def test_unproven_asr_sha_cannot_hide_changed_media(self):
        metadata = Path(str(self.source) + ".transcript.meta.json")
        self.write(metadata, {"schema_version": 2, "sha256": "0" * 64})
        with self.assertRaisesRegex(VideoEditError, "คำถอดเสียงไม่ตรง"):
            self.answer("stories")
        self.assertFalse(identity.state_dir("stories", self.source).exists())

    def test_matching_asr_sha_still_checks_actual_media_and_replacement(self):
        metadata = Path(str(self.source) + ".transcript.meta.json")
        actual = identity.hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.write(metadata, {"schema_version": 2, "sha256": actual})
        answer = self.answer("stories")
        record = identity._json(identity.state_dir("stories", self.source) / "packets" / (answer["packet_id"] + ".json"))
        self.assertEqual(next(iter(record["sources"].values()))["sha256"], actual)
        self.source.write_bytes(b"different source")
        self.assert_stale(answer, "stories")
        with self.assertRaisesRegex(VideoEditError, "คำถอดเสียงไม่ตรง"):
            self.answer("stories")

    def test_pending_asr_blocks_packet_and_verify_but_allows_planning_revision(self):
        answer = self.answer("stories")
        active = identity.state_dir("stories", self.source) / "active.json"
        before = active.read_bytes()
        original, _ = identity._snapshot("stories", self.source, None, {}, require_transcript=False)
        part = Path(str(self.source) + ".transcript.part.json")
        part.write_text("{}", encoding="utf-8")
        self.assert_stale(answer, "stories")
        with self.assertRaisesRegex(VideoEditError, "ยังไม่เสร็จ"):
            self.answer("stories")
        pending, _ = identity._snapshot("stories", self.source, None, {}, require_transcript=False)
        self.assertNotEqual(identity._hash(original), identity._hash(pending))
        self.assertEqual(active.read_bytes(), before)
        self.assertEqual(part.read_text(encoding="utf-8"), "{}")
        part.unlink()
        optional, _ = identity._snapshot("stories", self.source, None, {}, require_transcript=False)
        required, _ = identity._snapshot("stories", self.source, None, {})
        self.assertEqual(identity._hash(original), identity._hash(optional))
        self.assertEqual(identity._hash(optional), identity._hash(required))

    def test_missing_declared_media_is_an_error(self):
        self.source.unlink()
        with self.assertRaisesRegex(VideoEditError, "ฟุตต้นทาง"):
            self.answer()
        with self.assertRaisesRegex(VideoEditError, "ฟุตต้นทาง"):
            self.answer("stories")

    def test_old_missing_and_tampered_identity_cannot_verify(self):
        answer = self.answer()
        for mutate in (lambda x: x.update(clipkit_web_ai=1), lambda x: x.pop("packet_id"),
                       lambda x: x.update(packet_id="../path"), lambda x: x.update(revision="bad"),
                       lambda x: x.update(source_id="bad"), lambda x: x.update(step="stories")):
            changed = copy.deepcopy(answer)
            mutate(changed)
            self.assert_stale(changed)

    def test_invalid_preferences_are_rejected_before_creating_identity(self):
        for value in (0, -1, True, float("nan"), float("inf"), "3"):
            with self.subTest(value=value), self.assertRaises(VideoEditError):
                self.answer("stories", preferences={"max_minutes": value})
        self.assertFalse(identity.state_dir("stories", self.source).exists())

    def test_manual_packet_lane_preserves_browser_latest_and_rejects_old_manual_or_tampered_operation(self):
        browser = self.answer()
        active = identity.state_dir('clip', self.project)/'active.json'
        before = active.read_bytes()
        manual = {'clipkit_web_ai':2, 'step':'clip', **identity.issue('clip',self.project,operation='manual')}
        identity.verify('clip',self.project,browser)
        identity.verify('clip',self.project,manual)
        newer = {'clipkit_web_ai':2, 'step':'clip', **identity.issue('clip',self.project,operation='manual')}
        self.assertEqual(active.read_bytes(),before)
        with self.assertRaises(VideoEditError):identity.verify('clip',self.project,manual)
        identity.verify('clip',self.project,newer)
        for answer in (browser,newer):
            changed = dict(answer)
            changed['operation'] = None if answer.get('operation') else 'manual'
            with self.assertRaises(VideoEditError):identity.verify('clip',self.project,changed)


if __name__ == "__main__":
    unittest.main()
