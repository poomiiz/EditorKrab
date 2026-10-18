"""Actual generated MP4 validation is read-only and refuses incomplete/corrupt delivery."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
with tempfile.TemporaryDirectory() as folder:
    config = Path(folder) / "config.json"
    config.write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"CLIP_KIT_CONFIG": str(config)}):
        import render
import video_edit


class RealOutputValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.valid = cls.root / "generated.mp4"
        subprocess.run([render.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x96:r=30:d=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(cls.valid)],
                       capture_output=True, check=True, timeout=30)

    def test_valid_file_is_probed_and_decoded_without_changing_bytes(self):
        before = self.valid.read_bytes()
        log = self.root / "valid-decode.log"
        result = render.validate_output(self.valid, 64, 96, 2, decode_log=log)
        self.assertEqual((result["width"], result["height"]), (64, 96))
        self.assertEqual(result["audio_codec"], "aac")
        self.assertTrue(log.is_file())
        self.assertEqual(self.valid.read_bytes(), before)

    def test_missing_audio_and_missing_video_are_rejected(self):
        for kind in ("video", "audio"):
            file = self.root / (kind + "-only.mp4")
            subprocess.run([render.FFMPEG, "-v", "error", "-i", str(self.valid), "-map", "0:" + kind[0] + ":0",
                            "-c", "copy", str(file)], capture_output=True, check=True, timeout=30)
            with self.subTest(stream=kind), self.assertRaises(video_edit.VideoEditError):
                render.validate_output(file, 64, 96, 2)

    def test_wrong_dimensions_and_truncated_expected_duration_are_rejected(self):
        for width, height, duration in ((96, 64, 2), (64, 96, 3), (64, 96, 1)):
            with self.subTest(expected=(width, height, duration)), self.assertRaises(video_edit.VideoEditError):
                render.validate_output(self.valid, width, height, duration)

    def test_preview_accepts_matching_ratio_but_rejects_wrong_ratio(self):
        render.validate_output(self.valid, 128, 192, 2, preview=True)
        with self.assertRaises(video_edit.VideoEditError):
            render.validate_output(self.valid, 128, 128, 2, preview=True)

    def test_truncated_faststart_file_with_valid_metadata_fails_full_decode(self):
        file = self.root / "truncated.mp4"
        original = self.valid.read_bytes()
        file.write_bytes(original[:len(original) // 2])
        before = hashlib.sha256(file.read_bytes()).hexdigest()
        metadata = video_edit.probe(str(file))
        self.assertAlmostEqual(metadata["duration"], 2, delta=.1)
        self.assertEqual((metadata["width"], metadata["height"]), (64, 96))
        log = self.root / "truncated-decode.log"
        with self.assertRaisesRegex(video_edit.VideoEditError, "ตรวจไฟล์ส่งออกไม่ผ่าน"):
            render.validate_output(file, 64, 96, 2, decode_log=log)
        self.assertTrue(log.is_file())
        self.assertTrue(log.read_text(encoding="utf-8").strip())
        self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
