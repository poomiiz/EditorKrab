"""Chosen music/SFX and native audio references are frozen before consumption/publication."""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))
import motion_cut_test as fixture
import capcut_build
import capcut_build_safety_test as native_fixture
import delivery_manifest
import hf_build
import web_ai_identity as identity

render = fixture.render


def sound(path, duration=.2):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * round(8000 * duration))
    return path


class AudioIdentityTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.MotionCutTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.project = self.f.folder
        self.root = self.project.parent
        self.music = sound(self.root / "music" / "selected.wav", 8)
        self.pop = sound(self.root / "sounds" / "pop.wav")
        self.whoosh = sound(self.root / "sounds" / "whoosh.wav")
        config = self.root / "config.json"
        config.write_text(json.dumps({"simple": False, "render_encoder": "libx264",
                                     "stock_music": str(self.music.parent), "sfx": str(self.pop.parent)}))
        self.enterContext(patch.object(render.video_edit.kitconfig, "simple", return_value=False))
        self.enterContext(patch.dict(os.environ, {"MUSIC_DIRS": str(self.music.parent)}))
        self.enterContext(patch.object(render, "SFX", self.root / "builtin"))

    def guard(self):
        return identity.export_check(self.project)

    def test_changed_or_missing_registered_audio_blocks_even_with_same_size_and_mtime(self):
        for file in (self.music, self.pop, self.whoosh):
            before = file.read_bytes()
            with self.subTest(file=file.name):
                check = self.guard()
                check.bind_media({str(file): file})
                metadata = file.stat()
                changed = bytearray(before)
                changed[-1] ^= 1
                file.write_bytes(changed)
                os.utime(file, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
                with self.assertRaises(render.VideoEditError):
                    check()
                file.write_bytes(before)
                check = self.guard()
                check.bind_media({str(file): file})
                file.unlink()
                with self.assertRaises(render.VideoEditError):
                    check()
                file.write_bytes(before)

    def test_rebinding_does_not_erase_the_original_guard(self):
        check = self.guard()
        check.bind_media({str(self.music): self.music})
        self.music.write_bytes(b"replaced")
        with self.assertRaisesRegex(render.VideoEditError, "Resolved media changed"):
            check.bind_media({str(self.music): self.music})

    def test_stage_alias_readback_uses_published_path_and_missing_media_is_stale(self):
        final_audio = self.root / "published.wav"
        output = self.root / "output.mp4"
        output.write_bytes(b"encoded fixture")
        check = self.guard()
        check.bind_media({str(final_audio): self.music})
        record = delivery_manifest.prepare(self.project, output, output, "mp4", check(), options={}, checks={})
        self.music.rename(final_audio)
        self.assertTrue(delivery_manifest.status(self.project, record, output)["current"])
        final_audio.unlink()
        with patch.object(render, "_sfx", side_effect=AssertionError("Readback must not generate audio")):
            result = delivery_manifest.status(self.project, record, output)
        self.assertEqual(result["status"], "stale")
        self.assertTrue(result["artifact_matches"])

    def test_disabled_audio_binds_an_empty_set_without_resolving_libraries(self):
        check = self.guard()
        with patch.object(render, "_sfx", side_effect=AssertionError("unused SFX")), \
                patch.object(render, "_music", side_effect=AssertionError("unused music")):
            check.bind_media({})
            self.assertEqual(check()["resolved_media"], {})

    def test_first_use_generated_builtin_is_captured_after_generation(self):
        check = self.guard()
        with patch.object(render, "_own_sfx", return_value=None):
            file = render._sfx("pop")
        self.assertTrue(file.is_file())
        check.bind_media({str(file.resolve()): file})
        self.assertEqual(check()["resolved_media"][str(identity._canonical(file))], hashlib.sha256(file.read_bytes()).hexdigest())

    def test_cpu_explicit_default_music_and_custom_sfx_changes_keep_old_output(self):
        for mode in ("explicit music", "default music", "sfx"):
            with self.subTest(mode=mode):
                style = {"anim": "pair", "sfx": mode == "sfx"}
                if mode != "default music":
                    style["music"] = str(self.music) if mode == "explicit music" else ""
                self.f.f.write("clipkit_style.json", style)
                changed_file = self.pop if mode == "sfx" else self.music
                original = changed_file.read_bytes()
                out = self.root / (mode + " exports")
                out.mkdir()
                target = out / (self.project.name + ".mp4")
                target.write_bytes(b"previous accepted export")
                def encode(args, **kwargs):
                    self.assertIn(str(changed_file), args)
                    Path(args[-1]).write_bytes(b"new encoded fixture")
                    data = bytearray(original)
                    data[-1] ^= 1
                    changed_file.write_bytes(data)
                    return SimpleNamespace(returncode=0, stderr="")
                try:
                    with patch.object(render.subprocess, "run", side_effect=encode) as process:
                        with self.assertRaises(render.VideoEditError):
                            render.render_draft(str(self.project), str(out))
                    self.assertEqual(process.call_count, 1)
                    self.assertEqual(target.read_bytes(), b"previous accepted export")
                    self.assertFalse((out / ".clipkit-deliveries").exists())
                finally:
                    changed_file.write_bytes(original)

    def test_hf_registers_before_copy_and_uses_the_registered_files(self):
        self.f.f.write("clipkit_style.json", {"anim": "pair", "sfx": True})
        check = self.guard()
        original_local = hf_build._local
        registered = []
        def register(files):
            registered.extend(files)
            check.bind_media(files)
        def copy_audio(out, file):
            if Path(file) == self.music:
                self.assertIn(str(self.music.resolve()), registered)
                result = original_local(out, file)
                self.music.write_bytes(b"changed after copy")
                return result
            return original_local(out, file)
        with patch.object(hf_build, "_proxy", return_value="fixture.mp4"), \
                patch.object(hf_build, "_local", side_effect=copy_audio), \
                patch.object(render.capcut_edit, "_music_meta", return_value={"duration": .2}):
            hf_build._build(str(self.project), final=True, audio_check=register)
        self.assertTrue({str(file.resolve()) for file in (self.music, self.pop, self.whoosh)}.issubset(registered))
        with self.assertRaises(render.VideoEditError):
            check()

    def test_native_material_audio_is_recorded_and_changes_invalidate_it(self):
        native = native_fixture.NativeBuildSafetyTests()
        native.setUp()
        self.addCleanup(native.doCleanups)
        original_build = capcut_build._build
        def with_audio(path, stage, final):
            warnings = original_build(path, stage, final)
            draft = capcut_build.capcut_edit._load(stage)
            draft["materials"].setdefault("audios", []).append({"id": "audio-fixture", "path": str(self.music)})
            draft["tracks"].append({"type": "audio", "segments": [{"id": "audio-segment", "material_id": "audio-fixture",
                "source_timerange": {"start": 0, "duration": 1_000_000},
                "target_timerange": {"start": 0, "duration": 1_000_000}}]})
            capcut_build.capcut_edit._save(stage, draft, "audio_fixture")
            return warnings
        with patch.object(capcut_build, "_build", side_effect=with_audio), \
                patch.object(render.capcut_edit, "_music_meta", return_value={"duration": 8}):
            output, _ = capcut_build.build(str(native.project))
        manifest = next((native.base / ".clipkit-deliveries").glob("*.private.json"))
        record = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertIn(str(identity._canonical(self.music)), record["inputs"]["resolved_media"])
        self.assertTrue(delivery_manifest.status(native.project, manifest, output)["current"])
        self.music.write_bytes(b"new external music")
        self.assertFalse(delivery_manifest.status(native.project, manifest, output)["current"])
        native.assert_preserved()

    def test_relative_published_reference_is_refused(self):
        with self.assertRaisesRegex(render.VideoEditError, "absolute published path"):
            self.guard().bind_media({"relative.wav": self.music})


if __name__ == "__main__":
    unittest.main()
