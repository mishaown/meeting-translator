"""Download resume and integrity checks without network or model weights."""

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import setup_models


class DownloadTests(unittest.TestCase):
    def test_resumed_range_appends_only_missing_bytes(self):
        payload = b"model data"

        class Response:
            status = 206
            headers = {"Content-Range": "bytes 4-9/10"}

            def read(self, limit):
                self.limit = limit
                return payload[4:]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.part"
            target.write_bytes(payload[:4])
            response = Response()
            with patch(
                "setup_models.urllib.request.urlopen", return_value=response
            ) as request:
                setup_models.ranged_download(
                    "https://example.org/model", target, len(payload), workers=1
                )
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(request.call_args.args[0].get_header("Range"), "bytes=4-9")
            self.assertEqual(response.limit, 7)

    def test_range_rejection_preserves_partial_download(self):
        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.part"
            target.write_bytes(b"part")
            with patch(
                "setup_models.urllib.request.urlopen", return_value=Response()
            ), patch("setup_models.time.sleep"):
                with self.assertRaisesRegex(RuntimeError, "byte range"):
                    setup_models.ranged_download(
                        "https://example.org/model", target, 10, workers=1
                    )
            self.assertEqual(target.read_bytes(), b"part")

    def test_failed_checksum_resets_partial_before_retry(self):
        payload = b"verified model"
        calls = []

        def fetch(url, temporary, total, workers):
            calls.append(temporary.stat().st_size if temporary.exists() else 0)
            temporary.write_bytes(b"bad data" if len(calls) == 1 else payload)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.bin"
            artifacts = {
                "test-model": (
                    "https://example.org/model",
                    hashlib.sha256(payload).hexdigest(),
                    target,
                )
            }
            with patch.dict(setup_models.ARTIFACTS, artifacts), patch.dict(
                setup_models.MODEL_SIZES, {"test-model": len(payload)}
            ), patch("setup_models.ranged_download", side_effect=fetch), patch(
                "setup_models.time.sleep"
            ):
                setup_models.download("test-model", workers=1)
            self.assertEqual(calls, [0, 0])
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(target.with_suffix(".bin.part").exists())
