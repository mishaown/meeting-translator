"""Platform routing, runtime selection and the macOS capture protocol without devices."""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import numpy as np

import capture
import inference_runtime
import setup_models
from platform_support import target_platform


class PlatformTests(unittest.TestCase):
    def test_supported_architectures_and_unsupported_platforms(self):
        for system, machine, expected in (
            ("Windows", "AMD64", "windows-x64"),
            ("Windows", "ARM64", "windows-arm64"),
            ("Darwin", "x86_64", "macos-x64"),
            ("Darwin", "arm64", "macos-arm64"),
        ):
            with self.subTest(system=system, machine=machine):
                self.assertEqual(target_platform(system, machine), expected)
        with self.assertRaisesRegex(RuntimeError, "Supported platforms"):
            target_platform("Linux", "x86_64")
        with self.assertRaises(RuntimeError):
            target_platform("Windows", "x86")

    def test_setup_selects_native_assets_and_only_windows_needs_naudio(self):
        with patch.dict(
            setup_models.ARTIFACTS, setup_models.ARTIFACTS.copy(), clear=True
        ), patch("setup_models.shutil.which", return_value="tool"):
            self.assertIn("naudio.core", setup_models.configure_platform("windows-x64"))
            self.assertIn("whisper-bin-x64.zip", setup_models.ARTIFACTS["runtime"][0])
            self.assertIn("cpu-x64.zip", setup_models.ARTIFACTS["llama"][0])
            setup_models.configure_platform("windows-arm64")
            self.assertIn("cpu-arm64.zip", setup_models.ARTIFACTS["runtime"][0])
            for architecture in ("arm64", "x64"):
                assets = setup_models.configure_platform("macos-" + architecture)
                self.assertEqual(assets, ["llama", "whisper-source"])
                self.assertIn(
                    f"macos-{architecture}.tar.gz", setup_models.ARTIFACTS["llama"][0]
                )
            with self.assertRaisesRegex(ValueError, "Windows x64 uses CPU"):
                setup_models.configure_platform("windows-x64", acceleration=True)

    def test_mac_build_requires_tools_before_downloads(self):
        with patch("setup_models.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Command Line Tools"):
                setup_models.configure_platform("macos-arm64")

    def test_mac_build_matches_interpreter_architecture_and_bounds_parallelism(self):
        for target, architecture in (("macos-arm64", "arm64"), ("macos-x64", "x86_64")):
            with patch("setup_models.target_platform", return_value=target), patch(
                "setup_models.subprocess.run"
            ) as run:
                setup_models.build_mac_whisper()
                self.assertIn(
                    f"-DCMAKE_OSX_ARCHITECTURES={architecture}",
                    run.call_args_list[0].args[0],
                )
                self.assertEqual(run.call_args_list[1].args[0][-2:], ["-j", "2"])

    def test_native_runtime_lookup_and_gpu_policy(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "inference_runtime.ROOT", Path(directory)
        ):
            root = Path(directory)
            for path in (
                "whisper/Release/whisper-server.exe",
                "llama/llama-server.exe",
                "whisper-macos/bin/whisper-server",
                "llama/bin/llama-server",
            ):
                target = root / "runtime" / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
            with patch("inference_runtime.target_platform", return_value="windows-x64"):
                self.assertEqual(
                    inference_runtime.candidates("transcription", "auto")[0][1], "cpu"
                )
                with self.assertRaises(FileNotFoundError):
                    inference_runtime.candidates("transcription", "gpu")
            with patch("inference_runtime.target_platform", return_value="macos-arm64"):
                for task in ("transcription", "translation"):
                    self.assertEqual(
                        [
                            backend
                            for _, backend in inference_runtime.candidates(task, "auto")
                        ],
                        ["gpu-metal", "cpu"],
                    )
                    self.assertEqual(
                        inference_runtime.candidates(task, "gpu")[0][1], "gpu-metal"
                    )
                    self.assertEqual(
                        inference_runtime.candidates(task, "cpu")[0][1], "cpu"
                    )

    def test_capture_commands_route_to_native_workers(self):
        with patch("capture.target_platform", return_value="windows-x64"):
            self.assertIn("-ListDevices", capture.capture_command(list_devices=True))
            command = capture.capture_command(Path("meeting"), "both", 5, 123)
            self.assertEqual(command[0], "powershell.exe")
            self.assertEqual(command[-2:], ["-ParentProcessId", "123"])
        with patch("capture.target_platform", return_value="macos-x64"):
            command = capture.capture_command(Path("meeting"), "mic", parent_id=123)
            self.assertIn("--parent-id", command)
            self.assertNotIn("powershell.exe", command)
            self.assertIn("--list-devices", capture.capture_command(list_devices=True))

    def test_metal_verification_uses_upstream_device_names_and_rejects_failed_init(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "native.log"
            owner = SimpleNamespace(log_path=log, backend="gpu-metal")
            log.write_text(
                "whisper_backend_init_gpu: using MTL0 backend\nGPU name: MTL0 (Apple GPU)\n"
            )
            self.assertIn("MTL0", inference_runtime.verify_gpu(owner, "transcription"))
            log.write_text(
                "whisper_backend_init_gpu: using MTL0 backend\nfailed to initialize MTL0 backend\n"
            )
            with self.assertRaisesRegex(RuntimeError, "initialization failure"):
                inference_runtime.verify_gpu(owner, "transcription")
            log.write_text("offloaded 28/29 layers to GPU\n")
            self.assertEqual(
                inference_runtime.verify_gpu(owner, "translation"), "gpu-metal"
            )
            log.write_text("offloaded 0/29 layers to GPU\n")
            with self.assertRaises(RuntimeError):
                inference_runtime.verify_gpu(owner, "translation")

    def test_macos_system_capture_requires_explicit_loopback(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "MEETING_SYSTEM_DEVICE"):
                capture.selected_devices("both")
            self.assertEqual(capture.selected_devices("mic"), {"microphone": None})
        with patch.dict(
            os.environ,
            {"MEETING_SYSTEM_DEVICE": "BlackHole 2ch", "MEETING_MIC_DEVICE": "4"},
        ):
            self.assertEqual(
                capture.selected_devices("both"),
                {"microphone": 4, "system": "BlackHole 2ch"},
            )

    def test_paused_time_is_excluded_from_audio_clock(self):
        with patch("capture.time.monotonic", return_value=10) as now:
            clock = capture.CaptureClock()
            now.return_value = 12
            clock.pause(True)
            now.return_value = 30
            self.assertEqual(clock.elapsed(), 2)
            clock.pause(False)
            now.return_value = 31
            self.assertEqual(clock.elapsed(), 3)

    def test_mac_worker_writes_readable_aligned_protocol_files(self):
        class Input:
            active = True

            def __init__(self, **options):
                self.callback = options["callback"]
                self.channels = options["channels"]

            def __enter__(self):
                self.callback(
                    np.full((1600, self.channels), 0.1, dtype=np.float32),
                    1600,
                    None,
                    None,
                )
                return self

            def __exit__(self, *args):
                self.close()

            def close(self):
                self.active = False

        sd = SimpleNamespace(
            query_devices=lambda *args: {"max_input_channels": 2},
            CoreAudioSettings=lambda: None,
            InputStream=Input,
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"MEETING_SYSTEM_DEVICE": "BlackHole"}
        ), patch("capture.parent_alive", side_effect=[True, True, False]):
            output = Path(directory)
            capture.record_mac(output, "both", 0, 123, sd)
            self.assertTrue((output / "capture.ready").exists())
            self.assertTrue((output / "capture.finished").exists())
            for track in ("microphone", "system"):
                with wave.open(str(output / (track + ".wav"))) as audio:
                    self.assertEqual(
                        (
                            audio.getframerate(),
                            audio.getsampwidth(),
                            audio.getnchannels(),
                        ),
                        (16000, 2, 1),
                    )
                    self.assertGreaterEqual(audio.getnframes(), 1600)
