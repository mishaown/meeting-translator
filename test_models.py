"""Model switching, version preservation and accelerator failure regression tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gui_runtime import Application, Store, Job
from inference_runtime import initialize


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        (Path(self.temp.name) / "meeting").mkdir()
        self.store.meta("meeting", {"title": "Keep my meeting", "state": "complete"})
        self.app = Application(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_model_validation_and_live_switch(self):
        self.app.current = Job(self.app, "meeting", self.app.defaults)
        self.app.current.state = "recording"
        self.app.control(
            "options",
            {"model": "tiny", "translation_model": "whisper", "device": "cpu"},
        )
        self.assertEqual(self.app.current.flags()["model"], "tiny")
        self.assertEqual(self.app.current.flags()["translation_model"], "whisper")
        with self.assertRaises(ValueError):
            self.app.control("options", {"model": "../../outside"})
        with self.assertRaises(ValueError):
            self.app.control(
                "options", {"model": "turbo", "translation_model": "whisper"}
            )
        self.assertEqual(self.app.defaults["model"], "tiny")
        self.app.current.mode = "process"
        with self.assertRaises(ValueError):
            self.app.control("options", {"model": "base"})

    def test_old_text_and_manual_names_survive_multiple_runs(self):
        original = [{"id": "one", "chinese": "Original", "english": "Original English"}]
        self.store.save_rows("meeting", original)
        self.store.profiles(
            "meeting", {"speaker_1": {"name": "Alice", "label": "Female"}}
        )
        self.store.begin_run("meeting", dict(self.app.defaults, model="tiny"))
        old_id = next(
            run["id"] for run in self.store.runs("meeting") if not run["active"]
        )
        second_id = self.store.meta("meeting")["active_run"]
        self.store.save_rows("meeting", [{"id": "two", "chinese": "New"}])
        self.store.profiles("meeting", {"speaker_1": {"name": "Bob"}})
        self.store.meta("meeting", {"state": "complete", "elapsed_seconds": 1.2})
        self.store.checkpoint("meeting")
        self.app.select_run("meeting", old_id)
        self.assertEqual(self.store.rows("meeting"), original)
        self.assertEqual(self.store.profiles("meeting")["speaker_1"]["name"], "Alice")
        self.app.select_run("meeting", second_id)
        self.assertEqual(self.store.rows("meeting")[0]["id"], "two")
        self.assertEqual(self.store.profiles("meeting")["speaker_1"]["name"], "Bob")
        self.assertEqual(len(self.store.runs("meeting")), 2)
        with self.assertRaises(ValueError):
            self.store.run_detail("meeting", "../outside")

    def test_automatic_gpu_failure_falls_back_but_strict_gpu_does_not(self):
        class Owner:
            def close(self):
                pass

        owner = Owner()
        choices = [(Path("gpu.exe"), "gpu-opencl"), (Path("cpu.exe"), "cpu")]
        with patch("inference_runtime.candidates", return_value=choices), patch(
            "inference_runtime.launch",
            side_effect=[RuntimeError("driver rejected model"), None],
        ):
            initialize(owner, "transcription", lambda backend: [], "auto")
        self.assertEqual(owner.backend, "cpu")
        self.assertIn("driver rejected model", owner.fallback_reason)
        with patch("inference_runtime.candidates", return_value=choices[:1]), patch(
            "inference_runtime.launch", side_effect=RuntimeError("GPU failed")
        ):
            with self.assertRaises(RuntimeError):
                initialize(Owner(), "transcription", lambda backend: [], "gpu")

    def test_caption_worker_reloads_models_at_utterance_boundary(self):
        calls = []
        job = Job(
            self.app,
            "meeting",
            dict(self.app.defaults, device="cpu", diarization=False),
        )
        job.state = "recording"
        store = self.store
        original_add = store.add_row

        class Speech:
            def __init__(self, model, *args, **kwargs):
                self.model = model
                calls.append(("load", model))

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

            def close(self):
                calls.append(("close", self.model))

            def infer(self, samples, translate=False):
                return {"text": "English" if translate else "Chinese", "seconds": 0.1}

            def device_info(self):
                return {"backend": "cpu", "model": self.model}

        def add(identity, row):
            original_add(identity, row)
            job.update_flags({"model": "tiny", "translation_model": "whisper"})

        class Text(Speech):
            def __init__(self, *args, model, **kwargs):
                super().__init__(model)

            def translate(self, text):
                return {"text": "English", "seconds": 0.2}

        job.tasks.put((0, 16000, job.flags()))
        job.tasks.put((16000, 32000, job.flags()))
        job.tasks.put(None)
        with patch(
            "gui_runtime.model_path", return_value=Path("translator.gguf")
        ), patch("gui_runtime.Whisper", Speech), patch(
            "gui_runtime.TextTranslator", Text
        ), patch(
            "gui_runtime.load_audio", return_value=__import__("numpy").zeros(16000)
        ), patch.object(
            store, "add_row", side_effect=add
        ):
            job.caption_loop()
        self.assertIsNone(job.error)
        rows = store.rows("meeting")
        self.assertEqual([row["model"] for row in rows], ["base", "tiny"])
        self.assertEqual(
            [row["translation_model"] for row in rows], ["qwen25-1.5b", "whisper"]
        )
        self.assertLess(calls.index(("close", "base")), calls.index(("load", "tiny")))
        self.assertEqual(rows[1]["english"], "English")


if __name__ == "__main__":
    unittest.main()
