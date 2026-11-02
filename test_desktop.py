"""Writable desktop paths, setup safety and frozen worker dispatch without GUI dependencies."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app_paths
import capture
from gui_runtime import Application, Store
from model_setup import ModelSetup
from platform_support import target_platform


class DesktopTests(unittest.TestCase):
    def test_frozen_data_is_outside_the_application_bundle(self):
        with patch.dict(
            os.environ,
            {"MEETING_DATA_DIR": "", "LOCALAPPDATA": "C:/Users/test/AppData/Local"},
        ), patch("app_paths.sys.frozen", True, create=True), patch(
            "app_paths.sys.platform", "win32"
        ):
            self.assertEqual(
                app_paths.data_directory(),
                Path("C:/Users/test/AppData/Local/MeetingTranslator"),
            )
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"MEETING_DATA_DIR": directory}
        ):
            self.assertEqual(app_paths.data_directory(), Path(directory).resolve())

    def test_windows_x64_executable_on_arm_uses_x64_runtimes(self):
        with patch(
            "platform_support.sysconfig.get_platform", return_value="win-amd64"
        ), patch("platform_support.platform.machine", return_value="ARM64"):
            self.assertEqual(target_platform("Windows"), "windows-x64")

    def test_frozen_mac_capture_dispatches_back_to_executable(self):
        with patch("capture.target_platform", return_value="macos-arm64"), patch(
            "capture.sys.frozen", True, create=True
        ):
            command = capture.capture_command("session", "mic")
            self.assertEqual(command[1], "--capture-worker")
            self.assertNotIn(str(capture.ROOT / "capture.py"), command)

    def test_lightweight_setup_does_not_download_qwen_or_replace_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application(Store(directory))
            installer = ModelSetup(app)
            with patch(
                "model_setup.candidates", return_value=[(Path("server"), "cpu")]
            ), patch("setup_models.download") as download:
                installer.install("lightweight", False, [])
            self.assertEqual(
                [call.args[0] for call in download.call_args_list], ["vad", "tiny"]
            )
            self.assertEqual(installer.snapshot()["state"], "complete")
            self.assertEqual(app.defaults["translation_model"], "whisper")
            self.assertFalse(app.defaults["diarization"])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_setup_failure_preserves_previous_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application(Store(directory))
            defaults = dict(app.defaults)
            installer = ModelSetup(app)
            with patch(
                "model_setup.candidates", return_value=[(Path("server"), "cpu")]
            ), patch(
                "setup_models.download", side_effect=RuntimeError("checksum mismatch")
            ):
                installer.install("lightweight", False, [])
            self.assertEqual(installer.snapshot()["state"], "error")
            self.assertIn("checksum mismatch", installer.snapshot()["error"])
            self.assertEqual(app.defaults, defaults)

    def test_setup_rejects_unknown_models_and_concurrent_work(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = ModelSetup(Application(Store(directory)))
            with self.assertRaises(ValueError):
                installer.start({"models": ["../../model"]})
            with patch.object(installer.app, "busy", return_value=True):
                with self.assertRaisesRegex(ValueError, "Finish the current"):
                    installer.start({})
            installer.update(state="running")
            with self.assertRaises(ValueError):
                installer.start({})
