"""Checkpoint failure, retry, final-write failure and cache identity without GPU/network."""
import hashlib
import json
import sys
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import video_edit as ve


class Checkpoints(unittest.TestCase):
    def test_last_word_cannot_extend_beyond_audio(self):
        phrases = [{"start": 8.1, "end": 10.92, "text": "สวัสดี", "words":
                    [[8.1, 9.5, "ส"], [9.5, 10.72, "ว"], [10.72, 10.92, "ัสดี"]]}]
        ve._bound_phrase_times(phrases, 10.25)
        self.assertEqual(phrases[0]["end"], 10.25)
        self.assertEqual(phrases[0]["words"][-1], [10.25, 10.25, "ัสดี"])
        self.assertEqual("".join(w[2] for w in phrases[0]["words"]), "สวัสดี")

    def test_quiet_boundary_in_real_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "pauses.wav"
            subprocess.run([ve.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                            r"aevalsrc=sin(2000*t)*lt(mod(t\,2)\,1.5):s=16000:d=70", str(source)], check=True)
            for near in (20, 40):
                seam = ve._quiet_point(str(source), near)
                self.assertTrue(1.5 < seam % 2 < 2, seam)

    def test_cpu_fallback_is_part_of_identity(self):
        with patch.object(ve, "_has_cuda", return_value=False), \
             patch.object(ve, "WHISPER_CPU_FALLBACK", "cpu-model"):
            self.assertEqual(ve._model_settings(), ("cpu-model", "cpu", "int8"))
        with patch.object(ve, "_has_cuda", return_value=True), \
             patch.object(ve, "whisper_model", return_value="gpu-model"):
            self.assertEqual(ve._model_settings(), ("gpu-model", "cuda", "int8_float16"))

    def test_replaced_source_with_legacy_transcript_is_backed_up_and_retranscribed(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "legacy.mp4"
            source.write_bytes(b"replaced source")
            transcript = ve.transcript_file(str(source))
            legacy = '[{"start":0,"end":1,"text":"unproven old words"}]'
            transcript.write_text(legacy, encoding="utf-8")
            lines = ve.lines_file(str(source))
            lines.write_text(legacy, encoding="utf-8")
            recognized = [{"start": 0, "end": 1, "text": "new actual words", "words": [[0, 1, "new actual words"]]}]
            with patch.object(ve, "probe", return_value={"duration": 1}), \
                 patch.object(ve, "transcribe", return_value=recognized) as recognize:
                self.assertEqual(ve.transcribe_file(str(source)), recognized)
                self.assertEqual(ve.transcribe_file(str(source)), recognized)
                self.assertEqual(recognize.call_count, 1)
            self.assertEqual(next(Path(folder).glob("*.transcript.json.before-retranscribe-*")).read_text(), legacy)
            self.assertEqual(next(Path(folder).glob("*.lines.json.before-retranscribe-*")).read_text(), legacy)
            self.assertEqual(len(list(Path(folder).glob("*.before-retranscribe-*"))), 2)
            self.assertEqual(json.loads(transcript.with_suffix(".meta.json").read_text())["sha256"],
                             hashlib.sha256(source.read_bytes()).hexdigest())

    def test_interrupted_retranscription_preserves_originals_and_reuses_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "talk.mp4"
            source.write_bytes(b"new source")
            tf = ve.transcript_file(str(source))
            meta = tf.with_suffix(".meta.json")
            lines = ve.lines_file(str(source))
            originals = {tf: b'[{"start":0,"end":1,"text":"old words"}]', meta: b'{"sha256":"other source"}', lines: b"[]"}
            for file, content in originals.items():
                file.write_bytes(content)
            calls = []
            def recognize(path, start, end, language, model_size=None):
                calls.append((start, end))
                if len(calls) == 1:
                    raise RuntimeError("before first chunk")
                if len(calls) == 3:
                    raise RuntimeError("after completed chunk")
                return [{"start": 0, "end": 1, "text": "actual words", "words": [[0, 1, "actual words"]]}]
            with patch.object(ve, "probe", return_value={"duration": 31}), \
                 patch.object(ve, "CHUNK_S", 10), \
                 patch.object(ve, "_quiet_point", side_effect=lambda p, t: t), \
                 patch.object(ve, "_file_language", return_value="th"), \
                 patch.object(ve, "transcribe", side_effect=recognize):
                for expected_end in (0, 10):
                    with self.assertRaises(RuntimeError):
                        ve.transcribe_file(str(source))
                    self.assertEqual(json.loads(tf.with_suffix(".part.json").read_text())["end"], expected_end)
                    self.assertEqual({file: file.read_bytes() for file in originals}, originals)
                    self.assertEqual(len(list(Path(folder).glob("*.before-retranscribe-*"))), 3)
                result = ve.transcribe_file(str(source))
            self.assertEqual(calls, [(0, 10), (0, 10), (10, 20), (10, 20), (20, 31)])
            self.assertEqual([phrase["start"] for phrase in result], [0, 10, 20])
            self.assertEqual(len(list(Path(folder).glob("*.before-retranscribe-*"))), 3)
            for file, content in originals.items():
                self.assertEqual(next(Path(folder).glob(file.name + ".before-retranscribe-*")).read_bytes(), content)

    def test_resume_and_cache_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "talk.mp4"
            source.write_bytes(b"source")
            calls = []

            def recognize(path, start, end, language, model_size=None):
                calls.append((start, end, language))
                if len(calls) == 2:
                    raise RuntimeError("interrupted")
                return [{"start": 0, "end": 1, "text": "speech", "words": [[0, 1, "speech"]]}]

            with patch.object(ve, "probe", return_value={"duration": 31}), \
                 patch.object(ve, "CHUNK_S", 10), \
                 patch.object(ve, "_quiet_point", side_effect=lambda p, t: t), \
                 patch.object(ve, "_file_language", return_value="th"), \
                 patch.object(ve, "transcribe", side_effect=recognize):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    ve.transcribe_file(str(source))
                self.assertEqual(json.loads(ve.transcript_file(str(source)).with_suffix(".part.json").read_text())["end"], 10)
                phrases = ve.transcribe_file(str(source))
                self.assertEqual(calls, [(0, 10, "th"), (10, 20, "th"), (10, 20, "th"), (20, 31, "th")])
                self.assertEqual([p["start"] for p in phrases], [0, 10, 20])
                self.assertEqual(phrases[-1]["words"], [[20, 21, "speech"]])
                self.assertEqual(ve.transcribe_file(str(source)), phrases)
                self.assertEqual(len(calls), 4)
                source.write_bytes(b"change")  # same length; content hash must invalidate it
                ve.transcribe_file(str(source))
                self.assertEqual(len(calls), 7)
                with patch.object(ve, "_model_settings", return_value=("other", "cpu", "int8")):
                    ve.transcribe_file(str(source))
                self.assertEqual(len(calls), 10)
                self.assertEqual(set(json.loads(ve.lines_file(str(source)).read_text())[0]), {"start", "end", "text"})

    def test_completed_checkpoint_survives_failed_final_write(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "short.mp4"
            source.write_bytes(b"source")
            transcript = ve.transcript_file(str(source))
            old = '[{"start":0,"end":1,"text":"unverified original"}]'
            transcript.write_text(old, encoding="utf-8")
            original = ve.atomic.write_text

            def write(path, *args, **kwargs):
                if path == ve.transcript_file(str(source)):
                    raise OSError("disk failure")
                return original(path, *args, **kwargs)

            with patch.object(ve, "probe", return_value={"duration": 0.25}), \
                 patch.object(ve, "transcribe", return_value=[]) as recognize:
                with patch.object(ve.atomic, "write_text", side_effect=write):
                    with self.assertRaisesRegex(OSError, "disk failure"):
                        ve.transcribe_file(str(source))
                self.assertEqual(transcript.read_text(encoding="utf-8"), old)
                self.assertEqual(json.loads(transcript.with_suffix(".part.json").read_text())["end"], .25)
                self.assertEqual(ve.transcribe_file(str(source)), [])
                self.assertEqual(recognize.call_count, 1)
                backups = list(Path(folder).glob("*.before-retranscribe-*"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_text(encoding="utf-8"), old)

    def test_missing_metadata_is_not_legacy_when_checkpoint_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "short.mp4"
            source.write_bytes(b"source")
            original = ve.atomic.write_text

            def write(path, *args, **kwargs):
                if path == ve.transcript_file(str(source)).with_suffix(".meta.json"):
                    raise OSError("metadata failure")
                return original(path, *args, **kwargs)

            with patch.object(ve, "probe", return_value={"duration": 1}), \
                 patch.object(ve, "transcribe", return_value=[]) as recognize:
                with patch.object(ve.atomic, "write_text", side_effect=write):
                    with self.assertRaisesRegex(OSError, "metadata failure"):
                        ve.transcribe_file(str(source))
                source.write_bytes(b"change")
                ve.transcribe_file(str(source))
                self.assertEqual(recognize.call_count, 2)

    def test_failed_first_chunk_blocks_old_transcript_export_and_pending_answer(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(ve.kitconfig, "_load", return_value={}):
            import web_ai_json as web
            import web_ai_identity as packets
            source = Path(folder) / "talk.mp4"
            source.write_bytes(b"source")
            tf = ve.transcript_file(str(source))
            old = [{"start": 0, "end": 1, "text": "unproven old words"}]
            tf.write_text(json.dumps(old), encoding="utf-8")
            pending = {"clipkit_web_ai": 2, **packets.issue("stories", source), "step": "stories",
                       "video": source.name, "stories": [{"title": "Old", "start": 0, "end": 1}]}
            with patch.object(ve, "probe", return_value={"duration": 1}), \
                 patch.object(ve, "transcribe", side_effect=RuntimeError("interrupted before first chunk")):
                with self.assertRaises(RuntimeError):
                    ve.transcribe_file(str(source))
            self.assertEqual(json.loads(tf.read_text()), old)
            for operation in (lambda: web.export_stories(str(source)), lambda: packets.issue("stories", source),
                              lambda: web.import_stories(str(source), pending)):
                with self.assertRaisesRegex(ve.VideoEditError, "ยังไม่เสร็จ"):
                    operation()
            self.assertFalse(ve.stories_file(str(source)).exists())
            with patch.object(ve, "probe", return_value={"duration": 1}), \
                 patch.object(ve, "transcribe", return_value=old):
                ve.transcribe_file(str(source))
            self.assertTrue(web.export_stories(str(source)).is_file())

    def test_source_replacement_mid_asr_cannot_bind_cli_selection(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(ve.kitconfig, "_load", return_value={}):
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import run_clip as cli
            import web_ai_identity as packets
            source = Path(folder) / "talk.mp4"
            source.write_bytes(b"old source")
            tf = ve.transcript_file(str(source))
            original = '[{"start":0,"end":1,"text":"original unproven words"}]'
            tf.write_text(original, encoding="utf-8")
            def replaced(*args, **kwargs):
                source.write_bytes(b"new source")
                return [{"start": 0, "end": 1, "text": "words from old source"}]
            with patch.object(ve, "probe", return_value={"duration": 1}), \
                 patch.object(ve, "transcribe", side_effect=replaced), patch.object(cli, "say"):
                with self.assertRaisesRegex(ve.VideoEditError, "ฟุตเปลี่ยน"):
                    cli.stories(str(source), None, no_ai=True, natural=True)
            self.assertEqual(tf.read_text(), original)
            self.assertFalse(tf.with_suffix(".meta.json").exists())
            self.assertFalse(ve.stories_file(str(source)).exists())
            self.assertEqual(json.loads(tf.with_suffix(".part.json").read_text())["end"], 0)
            with self.assertRaisesRegex(ve.VideoEditError, "ยังไม่เสร็จ"):
                packets.issue("stories", source)

    def test_source_change_during_initial_hash_leaves_pending_marker(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(ve.kitconfig, "_load", return_value={}):
            source = Path(folder) / "talk.mp4"
            source.write_bytes(b"source before hash")
            tf = ve.transcript_file(str(source))
            original = '[{"start":0,"end":1,"text":"original words"}]'
            tf.write_text(original, encoding="utf-8")
            digest = ve.hashlib.file_digest
            def replaced(*args, **kwargs):
                result = digest(*args, **kwargs)
                source.write_bytes(b"source after hash")
                return result
            with patch.object(ve.hashlib, "file_digest", side_effect=replaced), \
                 patch.object(ve, "transcribe", side_effect=AssertionError("must not start ASR")):
                with self.assertRaisesRegex(ve.VideoEditError, "ฟุตเปลี่ยน"):
                    ve.transcribe_file(str(source))
            self.assertEqual(tf.read_text(), original)
            self.assertTrue(tf.with_suffix(".part.json").is_file())
            self.assertFalse(tf.with_suffix(".meta.json").exists())

    def test_completed_checkpoint_unlink_failure_retries_without_recognition(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(ve.kitconfig, "_load", return_value={}):
            import web_ai_identity as packets
            source = Path(folder) / "talk.mp4"
            source.write_bytes(b"source")
            tf = ve.transcript_file(str(source))
            part = tf.with_suffix(".part.json")
            original_unlink = Path.unlink
            def fail_part(path, *args, **kwargs):
                if path == part:
                    raise OSError("checkpoint unlink interrupted")
                return original_unlink(path, *args, **kwargs)
            phrases = [{"start": 0, "end": 1, "text": "actual words", "words": [[0, 1, "actual words"]]}]
            with patch.object(ve, "probe", return_value={"duration": 1}), \
                 patch.object(ve, "transcribe", return_value=phrases) as recognize:
                with patch.object(Path, "unlink", fail_part), self.assertRaisesRegex(OSError, "unlink interrupted"):
                    ve.transcribe_file(str(source))
                self.assertTrue(tf.is_file() and tf.with_suffix(".meta.json").is_file() and part.is_file())
                with self.assertRaisesRegex(ve.VideoEditError, "ยังไม่เสร็จ"):
                    packets.issue("stories", source)
                self.assertEqual(ve.transcribe_file(str(source)), phrases)
                self.assertEqual(recognize.call_count, 1)
            self.assertFalse(part.exists())
            packets.issue("stories", source)


if __name__ == "__main__":
    unittest.main()
