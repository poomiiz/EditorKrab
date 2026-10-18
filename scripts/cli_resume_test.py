"""CLI resumes only the exact bound selection and build options, without private media."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))
with tempfile.TemporaryDirectory() as import_folder:
    config = Path(import_folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import run_clip as cli
import jobstore
import story_selection
import web_ai_identity
import web_ai_json


def accept_selection(raw, phrases, items):
    cli.video_edit.transcript_file(str(raw)).write_text(json.dumps(phrases), encoding="utf-8")
    web_ai_json.import_stories(str(raw), {"clipkit_web_ai": 2, **web_ai_identity.issue("stories", str(raw)),
                                        "step": "stories", "video": Path(raw).name, "stories": items})
    return story_selection.read_selection(str(raw), phrases)


def write_project(project, raw, story, *, shape="portrait", look="pair", preset="default"):
    project = Path(project)
    project.mkdir(exist_ok=True)
    duration = round((story["end"] - story["start"]) * 1_000_000)
    width, height = cli.video_edit.CANVAS.get(shape, (1920, 1080))
    draft = {"id": project.name, "duration": duration,
             "canvas_config": {"width": width, "height": height},
             "materials": {"videos": [{"id": "video", "path": str(raw), "width": 1920, "height": 1080}], "texts": []},
             "tracks": [{"type": "video", "segments": [{"id": "segment", "material_id": "video",
                 "source_timerange": {"start": round(story["start"] * 1_000_000), "duration": duration},
                 "target_timerange": {"start": 0, "duration": duration}}]}]}
    (project / "draft_content.json").write_text(json.dumps(draft), encoding="utf-8")
    (project / "clipkit.json").write_text(json.dumps({"raw": str(raw), "start": story["start"], "end": story["end"]}), encoding="utf-8")
    (project / "clipkit_style.json").write_text(json.dumps({"anim": look, "preset": preset, "cover": True}), encoding="utf-8")
    (project / "clipkit_punch.json").write_text("{}", encoding="utf-8")
    return str(project)


class CliResume(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        config = self.root / "config.json"
        config.write_text("{}", encoding="utf-8")
        patch.object(cli.video_edit.kitconfig, "_CANDIDATES", [config]).start()
        patch.object(jobstore, "STATE", self.root / "locks").start()
        patch.object(cli, "say").start()
        self.presets = self.root / "presets"
        self.presets.mkdir()
        shutil.copyfile(ROOT / "presets" / "default.json", self.presets / "default.json")
        shutil.copyfile(self.presets / "default.json", self.presets / "other.json")
        self.font = self.root / "font.ttf"
        self.font.write_bytes(b"synthetic font identity; no font renderer runs")
        patch.object(cli.render, "PRESETS", self.presets).start()
        patch.object(cli.render, "DEFAULT_FONT", self.font).start()
        self.raw = self.root / "talk.mp4"
        self.raw.write_bytes(b"synthetic source; no decoder runs")
        self.phrases = [{"start": 0, "end": 10, "text": "first thought"},
                        {"start": 10, "end": 20, "text": "second thought"}]
        self.items = [{"title": "Complete thought", "summary": "Synthetic test", "start": 0, "end": 20}]
        self.selected = accept_selection(self.raw, self.phrases, self.items)
        self.context = self.context_for()
        self.story = self.selected[0]
        self.manifest = cli._manifest(self.context, self.story)
        self.state_file = Path(str(self.raw) + ".clipkit_run.json")
        self.state = cli._load_resume(self.state_file)
        self.project = self.root / "project"
        write_project(self.project, self.raw, self.story)
        cli._save_project(self.state_file, self.state, self.manifest, str(self.project))

    def context_for(self, **options):
        return cli._resume_context(str(self.raw), **({"shape": "portrait", "look": "pair", "preset": "default"} | options))

    def test_identical_manifest_reuses_readable_project_without_changing_ai_state(self):
        ai_state = web_ai_identity.state_dir("stories", str(self.raw))
        before = {p.relative_to(ai_state).as_posix(): p.read_bytes() for p in ai_state.rglob("*") if p.is_file()}
        with patch.object(web_ai_identity, "issue", side_effect=AssertionError("cache issued an AI packet")):
            current = self.context_for()
            self.assertEqual(cli._resume_project(cli._load_resume(self.state_file), cli._manifest(current, self.story)), str(self.project))
            self.assertEqual(cli._links(self.selected, [1], self.state, current), [])
        self.assertEqual({p.relative_to(ai_state).as_posix(): p.read_bytes() for p in ai_state.rglob("*") if p.is_file()}, before)

    def test_changed_inputs_reject_old_context(self):
        cases = [(self.raw, b"different synthetic source content"),
                 (Path(str(self.raw) + ".transcript.json"), json.dumps(self.phrases + [{"start": 20, "end": 30, "text": "new"}]).encode()),
                 (Path(str(self.raw) + ".transcript.meta.json"), b'{"model":"different"}'),
                 (Path(str(self.raw) + ".brief.txt"), b"different brief"),
                 (Path(str(self.raw) + ".story-preferences.json"), b'{"max_minutes":3}'),
                 (Path(str(self.raw) + ".stories.json"), b'[{"title":"Changed","start":0,"end":20}]'),
                 (story_selection.selection_file(str(self.raw)), b'{}'),
                 (self.presets / "default.json", (self.presets / "default.json").read_bytes() + b"\n"),
                 (self.font, b"different font")]
        for file, changed in cases:
            with self.subTest(file=file.name):
                accept_selection(self.raw, self.phrases, self.items)
                context = self.context_for()
                manifest = cli._manifest(context, self.story)
                cli._save_project(self.state_file, self.state, manifest, str(self.project))
                before = file.read_bytes() if file.exists() else None
                try:
                    file.write_bytes(changed)
                    with self.assertRaises(cli.video_edit.VideoEditError):
                        cli._resume_project(self.state, manifest)
                finally:
                    if before is None:
                        file.unlink()
                    else:
                        file.write_bytes(before)
        self.assertTrue(self.project.is_dir())

    def test_shape_look_preset_and_exact_story_each_select_a_new_record(self):
        for options in ({"shape": "square"}, {"look": "none"}, {"preset": "other"}):
            with self.subTest(options=options):
                self.assertIsNone(cli._resume_project(self.state, cli._manifest(self.context_for(**options), self.story)))
        for changes in ({"start": 0.004}, {"end": 19.996}, {"title": "New title"}, {"reason": "New reason"}):
            changed = cli._manifest(self.context, self.story | changes)
            self.assertNotEqual(cli._digest(changed), cli._digest(self.manifest))
            self.assertIsNone(cli._resume_project(self.state, changed))
        self.assertEqual(cli._resume_project(self.state, self.manifest), str(self.project))

    def test_legacy_projects_are_kept_but_not_trusted(self):
        old = {"0.00-20.00": str(self.project)}
        self.state_file.write_text(json.dumps(old), encoding="utf-8")
        state = cli._load_resume(self.state_file)
        self.assertEqual(state["legacy"], old)
        self.assertIsNone(cli._resume_project(state, self.manifest))
        new = self.root / "new-project"
        write_project(new, self.raw, self.story)
        cli._save_project(self.state_file, state, self.manifest, str(new))
        saved = cli._load_resume(self.state_file)
        self.assertEqual(saved["legacy"], old)
        self.assertEqual(cli._resume_project(saved, self.manifest), str(new))
        self.assertTrue((self.project / "draft_content.json").is_file())

    def test_configured_font_change_and_newly_available_font_invalidate_context(self):
        preset = self.presets / "default.json"
        data = json.loads(preset.read_text(encoding="utf-8"))
        custom = self.root / "custom.ttf"
        data["font"] = str(custom)
        preset.write_text(json.dumps(data), encoding="utf-8")
        missing_context = self.context_for()
        custom.write_bytes(b"first configured font")
        self.assertNotEqual(self.context_for(), missing_context)
        manifest = cli._manifest(self.context_for(), self.story)
        cli._save_project(self.state_file, self.state, manifest, str(self.project))
        custom.write_bytes(b"different configured font")
        with self.assertRaises(cli.video_edit.VideoEditError):
            cli._resume_project(self.state, manifest)

    def test_corrupt_state_does_not_become_an_empty_resume(self):
        for payload in ("{", "[]", '{"version":2,"projects":[]}', '{"version":3}'):
            self.state_file.write_text(payload, encoding="utf-8")
            with self.subTest(payload=payload), self.assertRaises(cli.video_edit.VideoEditError):
                cli._load_resume(self.state_file)

    def test_incomplete_corrupt_or_wrong_source_project_is_not_reused(self):
        for filename, content in [("draft_content.json", b"{"), ("clipkit.json", b"{}"),
                                  ("clipkit_style.json", b'{"anim":"none","preset":"default"}')]:
            file = self.project / filename
            before = file.read_bytes()
            try:
                file.write_bytes(content)
                with self.assertRaises(cli.video_edit.VideoEditError):
                    cli._resume_project(self.state, self.manifest)
            finally:
                file.write_bytes(before)
        draft_file = self.project / "draft_content.json"
        before = draft_file.read_bytes()
        draft = json.loads(before)
        draft["materials"]["videos"][0]["path"] = str(self.root / "other.mp4")
        draft_file.write_text(json.dumps(draft), encoding="utf-8")
        with self.assertRaises(cli.video_edit.VideoEditError):
            cli._resume_project(self.state, self.manifest)
        draft_file.write_bytes(before)
        draft_file.unlink()
        with self.assertRaises(cli.video_edit.VideoEditError):
            cli._resume_project(self.state, self.manifest)

    def test_changed_canvas_or_outside_edit_seal_stops_resume(self):
        file = self.project / "draft_content.json"
        before = file.read_bytes()
        draft = json.loads(before)
        draft["canvas_config"] = {"width": 1080, "height": 1080}
        file.write_text(json.dumps(draft), encoding="utf-8")
        with self.assertRaises(cli.video_edit.VideoEditError):
            cli._resume_project(self.state, self.manifest)
        file.write_bytes(before)
        cli.capcut_edit.accept(str(self.project))
        file.write_bytes(before + b"\n")
        with self.assertRaisesRegex(cli.video_edit.VideoEditError, "ถูกแก้นอก"):
            cli._resume_project(self.state, self.manifest)

    def test_atomic_state_failure_preserves_old_manifest_and_both_projects(self):
        before = self.state_file.read_bytes()
        context = self.context_for(look="none")
        manifest = cli._manifest(context, self.story)
        new = self.root / "new-project"
        write_project(new, self.raw, self.story, look="none")
        with patch.object(cli.atomic.os, "replace", side_effect=OSError("simulated full disk")):
            with self.assertRaises(OSError):
                cli._save_project(self.state_file, self.state, manifest, str(new))
        self.assertEqual(self.state_file.read_bytes(), before)
        self.assertNotIn(cli._digest(manifest), self.state["projects"])
        self.assertTrue(self.project.is_dir() and new.is_dir())
        self.assertFalse(list(self.root.glob("*.clipkit_run.json.*.tmp")))

    def test_stale_insert_links_raise_before_any_download_moves(self):
        downloads = self.root / "downloads"
        downloads.mkdir()
        file = downloads / "stock.mp4"
        file.write_bytes(b"synthetic stock")
        (self.project / "clipkit_punch.json").write_text('{"cue:one":[["normal","word","coffee"]]}', encoding="utf-8")
        Path(str(self.raw) + ".brief.txt").write_text("new brief", encoding="utf-8")
        with self.assertRaises(cli.video_edit.VideoEditError):
            cli.place(str(self.raw), self.selected, [1], self.state, str(downloads), context=self.context)
        self.assertEqual(file.read_bytes(), b"synthetic stock")
        self.assertFalse((self.project / "clipkit_insert").exists())

    def test_inputs_changed_during_download_scan_do_not_move_files(self):
        downloads = self.root / "downloads"
        downloads.mkdir()
        file = downloads / "stock.mp4"
        file.write_bytes(b"synthetic stock")
        (self.project / "clipkit_punch.json").write_text('{"cue:one":[["normal","word","coffee"]]}', encoding="utf-8")
        original = cli._downloads
        def scan(folder):
            found = original(folder)
            Path(str(self.raw) + ".brief.txt").write_text("changed during scan", encoding="utf-8")
            return found
        with patch.object(cli, "_downloads", side_effect=scan), self.assertRaises(cli.video_edit.VideoEditError):
            cli.place(str(self.raw), self.selected, [1], self.state, str(downloads), context=self.context)
        self.assertEqual(file.read_bytes(), b"synthetic stock")
        self.assertFalse((self.project / "clipkit_insert").exists())

    def test_unbound_selection_waits_without_issuing_a_packet(self):
        story_selection.selection_file(str(self.raw)).unlink()
        old = cli.video_edit.stories_file(str(self.raw)).read_bytes()
        with patch.object(cli.video_edit, "transcribe_file", return_value=self.phrases), \
                patch.object(web_ai_identity, "issue", side_effect=AssertionError("read issued packet")):
            self.assertIsNone(cli.stories(str(self.raw), None))
        self.assertEqual(cli.video_edit.stories_file(str(self.raw)).read_bytes(), old)

    def test_external_selection_change_between_read_and_context_never_builds_old_story(self):
        before = self.state_file.read_bytes()
        def replace_after_read(*args, **kwargs):
            changed = [{**self.story, "title": "Externally changed selection"}]
            file = cli.video_edit.stories_file(str(self.raw))
            file.write_text(json.dumps(changed), encoding="utf-8")
            story_selection.selection_file(str(self.raw)).write_text(
                json.dumps(story_selection.selection_binding(str(self.raw), file)), encoding="utf-8")
            return self.selected
        args = cli._parser().parse_args([str(self.raw), "--no-ai", "--preset", "default"])
        with patch.object(cli, "stories", side_effect=replace_after_read), \
                patch.object(cli, "make_project") as make:
            with self.assertRaisesRegex(cli.video_edit.VideoEditError, "selection changed"):
                cli._pipeline(args, str(self.raw))
            make.assert_not_called()
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_source_changed_during_selection_read_is_rejected(self):
        original = story_selection.read_selection
        def read_then_change(*args, **kwargs):
            result = original(*args, **kwargs)
            Path(str(self.raw) + ".brief.txt").write_text("changed while reading", encoding="utf-8")
            return result
        with patch.object(cli.video_edit, "transcribe_file", return_value=self.phrases), \
                patch.object(story_selection, "read_selection", side_effect=read_then_change):
            with self.assertRaisesRegex(cli.video_edit.VideoEditError, "changed while reading"):
                cli.stories(str(self.raw), None)

    def test_explicit_natural_reset_no_ai_keeps_full_footage_without_semantic_claim(self):
        phrases = [{"start": n, "end": n + 100, "text": "synthetic speech"} for n in range(0, 400, 100)]
        cli.video_edit.transcript_file(str(self.raw)).write_text(json.dumps(phrases), encoding="utf-8")
        web_ai_json.save_story_target(str(self.raw), 3)
        with patch.object(cli.video_edit, "transcribe_file", return_value=phrases), \
                patch.object(cli.video_edit, "_split_long", side_effect=AssertionError("natural forced split")), \
                patch.object(web_ai_identity, "issue", wraps=web_ai_identity.issue) as issue:
            selected = cli.stories(str(self.raw), None, no_ai=True, natural=True)
            self.assertEqual(issue.call_count, 1)
            self.assertEqual([(s["start"], s["end"]) for s in selected], [(0, 400)])
            self.assertIn("ยังไม่ได้คัดเรื่อง", selected[0]["summary"])
            self.assertIsNone(web_ai_json.story_target(str(self.raw)))
        with patch.object(cli.video_edit, "transcribe_file", return_value=phrases), \
                patch.object(web_ai_identity, "issue", side_effect=AssertionError("resume issued packet")):
            self.assertEqual(cli.stories(str(self.raw), None, no_ai=True), selected)

    def test_duration_flags_are_explicit_and_mutually_exclusive(self):
        self.assertFalse(cli._parser().parse_args(["test.mp4"]).natural)
        self.assertTrue(cli._parser().parse_args(["test.mp4", "--natural"]).natural)
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            cli._parser().parse_args(["test.mp4", "--natural", "--max", "180"])

    def test_pipeline_holds_source_writer_against_another_worker(self):
        with patch.object(cli, "_pipeline_locked", return_value=7) as body:
            self.assertEqual(cli._pipeline(object(), str(self.raw)), 7)
            body.assert_called_once()
            errors = []
            def attempt():
                try:
                    cli._pipeline(object(), str(self.raw))
                except Exception as exc:
                    errors.append(exc)
            with cli.project_lock.writer(self.raw):
                worker = threading.Thread(target=attempt)
                worker.start()
                worker.join(10)
                self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], cli.hf_build.BuildBusy)
            body.assert_called_once()

    def test_pipeline_builds_once_then_reuses_same_bound_project(self):
        self.state_file.unlink()
        made = []
        def make(raw, n, story, shape, look, preset):
            path = self.root / f"made-{len(made)}"
            made.append(path)
            return write_project(path, raw, story, shape=shape, look=look, preset=preset)
        args = cli._parser().parse_args([str(self.raw), "--no-ai", "--preset", "default"])
        with patch.object(cli.video_edit, "transcribe_file", return_value=self.phrases), \
                patch.object(cli, "make_project", side_effect=make), \
                patch.object(cli.ve, "broll_fill"), \
                patch.object(cli.hf_build, "build", return_value=self.root / "preview.html"), \
                patch.object(cli.ve, "draft_timeline", return_value={"duration": 20}), \
                patch.object(cli.render, "safe_zone", return_value=[]), \
                patch.object(cli.cutlog, "log_run", return_value={"synthetic": True}), \
                patch.object(web_ai_identity, "issue", side_effect=AssertionError("resume issued packet")):
            self.assertEqual(cli._pipeline(args, str(self.raw)), 0)
            self.assertEqual(cli._pipeline(args, str(self.raw)), 0)
        self.assertEqual(len(made), 1)
        self.assertEqual(len(cli._load_resume(self.state_file)["projects"]), 1)


if __name__ == "__main__":
    unittest.main()
