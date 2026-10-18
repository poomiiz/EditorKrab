"""Immutable local evidence binds outputs to exact input bytes; never grants human approval."""
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import motion_cut_test as fixture
import delivery_manifest as delivery
import web_ai_identity as identity


class DeliveryManifestTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.MotionCutTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.project = self.f.folder
        self.output = self.project.parent / "export.mp4"
        self.stage = self.project.parent / "staged.mp4"
        self.stage.write_bytes(b"encoded fixture")
        self.enterContext(patch.object(delivery, "commit", return_value="a" * 40))

    def prepare(self, kind="mp4", stage=None, output=None):
        return delivery.prepare(self.project, stage or self.stage, output or self.output, kind,
                                identity.export_check(self.project)(), options={"backend": "fixture"},
                                checks={"full_av_decode": "pending"})

    def test_publication_manifest_and_readback_preserve_original_project(self):
        before = {p: p.read_bytes() for p in self.project.rglob("*") if p.is_file()}
        manifest = self.prepare()
        self.assertFalse(self.output.exists())
        self.stage.replace(self.output)
        status = delivery.status(self.project, manifest, self.output)
        self.assertTrue(status["current"])
        self.assertEqual(status["status"], "pending_checks")
        self.assertEqual(status["human_audio_sync"], "pending")
        self.assertEqual(status["human_editorial_review"], "pending")
        self.assertEqual(status["capcut_open"], "pending")
        self.assertEqual({p: p.read_bytes() for p in self.project.rglob("*") if p.is_file()}, before)
        record = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(record["artifact"]["sha256"], identity._file(self.output))
        self.assertIn("code:draft_quality.py", record["inputs"]["snapshot"]["files"])
        self.assertIn("code:delivery_manifest.py", record["inputs"]["snapshot"]["files"])
        self.assertTrue(manifest.name.endswith(".private.json"))

    def test_output_change_invalidates_old_record_even_same_size(self):
        manifest = self.prepare()
        self.stage.replace(self.output)
        self.output.write_bytes(b"changed fixture")
        result = delivery.status(self.project, manifest, self.output)
        self.assertEqual(result["status"], "stale")
        self.assertFalse(result["artifact_matches"])
        self.assertTrue(result["inputs_match"])

    def test_source_metadata_font_and_quality_code_changes_invalidate(self):
        manifest = self.prepare()
        self.stage.replace(self.output)
        for name in ("source.mp4", "clipkit_style.json", "draft_meta_info.json"):
            file = self.project / name
            original = file.read_bytes() if file.exists() else None
            with self.subTest(file=name):
                if name.endswith(".json"):
                    file.write_text('{"review_change":true}', encoding="utf-8")
                else:
                    file.write_bytes(b"new source")
                self.assertFalse(delivery.status(self.project, manifest, self.output)["current"])
            if original is None:
                file.unlink()
            else:
                file.write_bytes(original)
        original = identity._file
        for name in ("Kanit-Bold.ttf", "draft_quality.py"):
            def changed(path, required=False):
                return "b" * 64 if Path(path).name == name else original(path, required)
            with self.subTest(file=name), patch.object(identity, "_file", side_effect=changed):
                self.assertEqual(delivery.status(self.project, manifest, self.output)["status"], "stale")

    def test_record_tampering_and_foreign_output_are_refused(self):
        manifest = self.prepare()
        self.stage.replace(self.output)
        with self.assertRaises(identity.VideoEditError):
            delivery.status(self.project, manifest, self.project / "different.mp4")
        record = json.loads(manifest.read_text(encoding="utf-8"))
        record["human_audio_sync"] = "approved"
        manifest.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(identity.VideoEditError, "changed or belongs"):
            delivery.status(self.project, manifest, self.output)

    def test_native_edit_and_added_file_invalidate_native_artifact(self):
        stage = self.project.parent / "native-stage"
        stage.mkdir()
        (stage / "draft_content.json").write_text('{"id":"generated"}')
        output = self.project.parent / "native-output"
        manifest = self.prepare("capcut", stage, output)
        stage.rename(output)
        self.assertTrue(delivery.status(self.project, manifest, output)["current"])
        file = output / "draft_content.json"
        original = file.read_bytes()
        file.write_text('{"id":"edited-in-native"}')
        self.assertFalse(delivery.status(self.project, manifest, output)["current"])
        file.write_bytes(original)
        (output / "new-metadata.json").write_text('{}')
        self.assertFalse(delivery.status(self.project, manifest, output)["current"])

    def test_records_remain_immutable_across_repeated_exports(self):
        first = self.prepare()
        original = first.read_bytes()
        second = self.prepare()
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), original)

    def test_missing_commit_stays_pending_and_wrong_source_snapshot_is_refused(self):
        with patch.object(delivery, "commit", return_value=None):
            manifest = self.prepare()
        self.stage.replace(self.output)
        self.assertEqual(delivery.status(self.project, manifest, self.output)["commit_provenance"], "pending")
        inputs = identity.export_check(self.project)()
        inputs["snapshot"]["target_id"] = "foreign"
        with self.assertRaises(identity.VideoEditError):
            delivery.prepare(self.project, self.output, self.output, "mp4", inputs, options={}, checks={})

    def test_cli_checks_current_and_stale_artifact_without_granting_approval(self):
        import quality_gate
        manifest = self.prepare()
        self.stage.replace(self.output)
        args = ["quality_gate", str(self.project), "--delivery", str(manifest), "--output", str(self.output)]
        for expected in (0, 2):
            stream = io.StringIO()
            with patch.object(sys, "argv", args), redirect_stdout(stream):
                code = quality_gate.main()
            self.assertEqual(code, expected)
            report = json.loads(stream.getvalue())
            self.assertEqual(report["delivery"]["current"], expected == 0)
            self.assertEqual(report["delivery"]["human_editorial_review"], "pending")
            self.output.write_bytes(b"externally edited output")

    def test_linked_evidence_directory_is_refused_before_writing(self):
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path.name == ".clipkit-deliveries" or original(path)):
            with self.assertRaisesRegex(identity.VideoEditError, "folder must not be a link"):
                self.prepare()
        self.assertFalse((self.output.parent / ".clipkit-deliveries").exists())

    def test_manifest_write_failure_does_not_change_artifact_or_old_manifest(self):
        first = self.prepare()
        before = first.read_bytes()
        self.output.write_bytes(b"previous accepted output")
        with patch.object(delivery.atomic, "write_text", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(first.read_bytes(), before)
        self.assertEqual(self.output.read_bytes(), b"previous accepted output")
        self.assertEqual(self.stage.read_bytes(), b"encoded fixture")


class CommitProvenanceTests(unittest.TestCase):
    def test_missing_git_failed_git_and_timeout_are_explicitly_pending(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("git", 10)):
            with patch.object(delivery.subprocess, "run", side_effect=error):
                self.assertIsNone(delivery.commit())
        for result in (SimpleNamespace(returncode=1, stdout=""), SimpleNamespace(returncode=0, stdout="not a hash")):
            with patch.object(delivery.subprocess, "run", return_value=result):
                self.assertIsNone(delivery.commit())
        with patch.object(delivery.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="a"*40+"\n")):
            self.assertEqual(delivery.commit(), "a"*40)


if __name__ == "__main__":
    unittest.main()
