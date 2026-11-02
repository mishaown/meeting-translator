"""Local API, file safety, caption persistence and real recording control tests."""

import json
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave

from engine import ROOT
from gui_runtime import Application, Store, DONE
from local_app import LocalServer


def wait_for(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.08)
    raise AssertionError("Operation did not finish within the test timeout")


class BrowserApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.fixture = Path(self.temp.name) / "test_recording"
        self.fixture.mkdir()
        with wave.open(str(self.fixture / "mixed.wav"), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 16000)
        self.store.meta("test_recording", {"title": "中文 meeting"})
        self.store.save_rows(
            "test_recording",
            [
                {
                    "id": "row1",
                    "start": 0,
                    "end": 4,
                    "speaker": "speaker_1",
                    "chinese": "大家好",
                    "english": "Hello everyone",
                }
            ],
        )
        self.app = Application(self.store)
        self.server = LocalServer(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.app.shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None, method=None):
        headers = dict(headers or {})
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-Local-App": "1"})
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        return self.http.open(request, timeout=30)

    def test_library_unicode_and_audio_seeking(self):
        with self.request("/") as response:
            self.assertIn(b"Start recording", response.read())
        with self.request("/api/sessions") as response:
            self.assertEqual(
                json.load(response)["sessions"][0]["title"], "中文 meeting"
            )
        with self.request(
            "/api/sessions/test_recording/audio/mixed",
            headers={"Range": "bytes=100-299"},
        ) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(len(response.read()), 200)
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
        with self.request(
            "/api/sessions/test_recording/audio/mixed", method="HEAD"
        ) as response:
            self.assertGreater(int(response.headers["Content-Length"]), 0)
            self.assertEqual(response.read(), b"")
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(
                "/api/sessions/test_recording/audio/mixed",
                headers={"Range": "bytes=99999999-"},
            )
        self.assertEqual(error.exception.code, 416)

    def test_only_one_app_can_bind_the_same_port(self):
        with self.assertRaises(OSError):
            LocalServer(("127.0.0.1", self.server.server_port), self.app)

    def test_models_and_saved_versions_api(self):
        with self.request("/api/models") as response:
            models = json.load(response)
        self.assertTrue(
            any(
                item["id"] == "tiny" and isinstance(item["installed"], bool)
                for item in models["transcription"]
            )
        )
        self.store.begin_run("test_recording", dict(self.app.defaults, model="tiny"))
        versions = self.store.runs("test_recording")
        previous = next(run["id"] for run in versions if not run["active"])
        with self.request(f"/api/sessions/test_recording/runs/{previous}") as response:
            self.assertEqual(json.load(response)["rows"][0]["id"], "row1")
        with self.request(
            "/api/sessions/test_recording/select-run", {"run_id": previous}
        ) as response:
            result = json.load(response)
        self.assertEqual(result["rows"][0]["id"], "row1")
        self.assertEqual(result["active_run"], previous)

    def test_manual_names_labels_and_speaker_corrections_persist(self):
        with self.request(
            "/api/sessions/test_recording/speakers",
            {"speakers": {"speaker_1": {"name": "王老师", "label": "Female"}}},
        ) as response:
            self.assertEqual(
                json.load(response)["speakers"]["speaker_1"]["label"], "Female"
            )
        with self.request(
            "/api/sessions/test_recording/speaker",
            {"row_id": "row1", "speaker": "speaker_2"},
        ):
            pass
        self.store.patch_row(
            "test_recording", "row1", {"english": "Updated translation"}
        )
        self.assertEqual(self.store.rows("test_recording")[0]["speaker"], "speaker_2")
        self.assertEqual(
            Store(self.temp.name).profiles("test_recording")["speaker_1"]["name"],
            "王老师",
        )

    def test_rejects_path_escape_and_cross_origin_mutations(self):
        with self.assertRaises(ValueError):
            self.store.path("../outside")
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(
                "/api/options",
                {"translation": False},
                {"Origin": "https://example.org"},
            )
        self.assertEqual(error.exception.code, 403)
        request = urllib.request.Request(
            self.url + "/api/start",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.http.open(request)
        self.assertEqual(error.exception.code, 403)

    @unittest.skipUnless(
        os.environ.get("MEETING_CAPTURE_TESTS") == "1",
        "Opt in to real device capture with MEETING_CAPTURE_TESTS=1",
    )
    def test_actual_pause_resume_and_independent_processing_switches(self):
        # System-only capture avoids capturing microphone speech in this control test.
        with self.request(
            "/api/start",
            {
                "source": "system",
                "title": "Pause test",
                "transcription": False,
                "translation": False,
                "diarization": False,
            },
        ) as response:
            identity = json.load(response)["id"]
        job = self.app.current
        wait_for(lambda: job.state == "recording")
        wait_for(lambda: job.duration > 0.5)
        with self.request("/api/pause", {}):
            pass
        wait_for(lambda: job.state == "paused")
        duration = job.duration
        time.sleep(0.7)
        self.assertLess(abs(job.duration - duration), 0.15)
        with self.request("/api/options", {"translation": True}):
            pass
        self.assertFalse(job.options["transcription"])
        self.assertTrue(job.options["translation"])
        with self.request("/api/options", {"translation": False}):
            pass
        with self.request("/api/resume", {}):
            pass
        wait_for(lambda: job.state == "recording" and job.duration > duration + 0.5)
        with self.request("/api/stop", {}):
            pass
        wait_for(lambda: job.state in DONE)
        self.assertEqual(job.state, "complete", job.error)
        self.assertEqual(self.store.rows(identity), [])
        with wave.open(str(self.store.audio_path(identity))) as audio:
            self.assertGreater(audio.getnframes(), 16000)
        self.assertTrue((job.output / "system.wav").exists())

    @unittest.skipUnless(
        os.environ.get("MEETING_MODEL_TESTS") == "1",
        "Opt in to downloaded models with MEETING_MODEL_TESTS=1",
    )
    def test_saved_audio_processing_uses_real_models_and_speaker_labels(self):
        shutil.copyfile(
            ROOT / "fixtures/chinese-meeting.wav", self.fixture / "mixed.wav"
        )
        # Local synthetic fixture: real recognition, translation and diarization.
        with wave.open(str(self.fixture / "mixed.wav")) as source:
            samples = source.readframes(4 * 16000)
        with wave.open(str(self.fixture / "mixed.wav"), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(samples)
        self.app.process("test_recording")
        job = self.app.current
        wait_for(lambda: job.state in DONE, 90)
        self.assertEqual(job.state, "complete", job.error)
        rows = self.store.rows("test_recording")
        self.assertTrue(any(row.get("chinese") for row in rows))
        self.assertTrue(any(row.get("english") for row in rows))
        self.assertTrue(
            any(row.get("speaker", "").startswith("speaker_") for row in rows)
        )
        self.assertTrue((self.fixture / "ui-captions.previous.json").exists())
        first_run = self.store.meta("test_recording")["active_run"]
        self.store.profiles("test_recording", {"speaker_1": {"name": "Alice"}})
        with self.request(
            "/api/sessions/test_recording/process",
            {
                "model": "tiny",
                "translation_model": "whisper",
                "device": "cpu",
                "diarization": False,
            },
        ):
            pass
        wait_for(lambda: self.app.current.state in DONE, 90)
        self.assertEqual(self.app.current.state, "complete", self.app.current.error)
        alternate = self.store.rows("test_recording")
        self.assertTrue(any(row["english"] for row in alternate))
        self.assertTrue(
            all(
                row["model"] == "tiny" and row["translation_model"] == "whisper"
                for row in alternate
            )
        )
        self.assertNotEqual(first_run, self.store.meta("test_recording")["active_run"])
        self.app.select_run("test_recording", first_run)
        self.assertEqual(self.store.rows("test_recording"), rows)
        self.assertEqual(
            self.store.profiles("test_recording")["speaker_1"]["name"], "Alice"
        )


if __name__ == "__main__":
    unittest.main()
