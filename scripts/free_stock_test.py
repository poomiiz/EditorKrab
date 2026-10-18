"""Stock downloads reject deceptive hosts and redirects before opening an external URL."""
import io
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
        import free_stock as stock


class Downloads(unittest.TestCase):
    def test_exact_domain_and_https_are_required(self):
        for url in ("https://evilpixabay.com/file", "https://pixabay.com.attacker.test/file",
                    "http://cdn.pixabay.com/file", "https://user@pixabay.com/file",
                    "https://pixabay.com:444/file", "https://[invalid/file"):
            with self.subTest(url=url), self.assertRaises(stock.VideoEditError):
                stock._file_url(url)
        stock._file_url("https://cdn.pixabay.com/video.mp4")

    def test_redirect_is_rejected_before_following(self):
        request = stock.urllib.request.Request("https://cdn.pixabay.com/file")
        with self.assertRaises(stock.VideoEditError):
            stock._StockRedirect().redirect_request(request, None, 302, "found", {}, "https://127.0.0.1/private")

    def test_failed_download_does_not_publish_or_leave_partial(self):
        class Response(io.BytesIO):
            def geturl(self):
                return "https://cdn.pixabay.com/file.mp4"

            def read(self, size=-1):
                if self.tell():
                    raise OSError("connection interrupted")
                return super().read(2)

        with tempfile.TemporaryDirectory() as folder, patch.object(stock.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = Response(b"abcd")
            with self.assertRaisesRegex(stock.VideoEditError, "connection interrupted"):
                stock.download(folder, "pixabay", "12", "https://cdn.pixabay.com/file.mp4")
            self.assertEqual(list((Path(folder) / "free").iterdir()), [])

    def test_success_and_cache_do_not_request_again(self):
        class Response(io.BytesIO):
            def geturl(self):
                return "https://cdn.pixabay.com/file.mp4"

        with tempfile.TemporaryDirectory() as folder, patch.object(stock.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = Response(b"verified later by asset probe")
            output = stock.download(folder, "pixabay", "12", "https://cdn.pixabay.com/file.mp4")
            self.assertEqual(Path(output).read_bytes(), b"verified later by asset probe")
            self.assertEqual(stock.download(folder, "pixabay", "12", "https://cdn.pixabay.com/file.mp4"), output)
            opener.assert_called_once()


if __name__ == "__main__":
    unittest.main()
