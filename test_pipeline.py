"""Audio correctness and native-engine integration checks (no device recording)."""

import os
import tempfile
import unittest
from pathlib import Path
import wave

import numpy as np

from engine import RATE, ROOT, Whisper
from meeting import Segmenter, load_audio
from translator import TextTranslator, normalize_numerals


class AudioTests(unittest.TestCase):
    def test_number_normalization_keeps_weekdays_and_ambiguous_amounts(self):
        self.assertEqual(
            normalize_numerals("下周二下午三点，预算三千五百元"),
            "下周二下午3点，预算3500元",
        )
        self.assertEqual(normalize_numerals("三点五元，三点五分"), "3.5元，3点5分")
        self.assertEqual(
            normalize_numerals("三千五元，三千零五元，千万不要"),
            "三千五元，3005元，千万不要",
        )

    @unittest.skipUnless(
        os.environ.get("MEETING_MODEL_TESTS") == "1",
        "Opt in with MEETING_MODEL_TESTS=1 after model setup",
    )
    def test_text_translation_preserves_meeting_details(self):
        # Regression cases that the rejected smaller translation model failed.
        with TextTranslator(log_dir=ROOT / "sessions/test-translator") as translator:
            date = translator.translate("下一次会议安排在下周二下午三点。")[
                "text"
            ].lower()
            self.assertIn("tuesday", date)
            self.assertIn("3", date)
            budget = (
                translator.translate("预算是三千五百元，不是五千元。")["text"]
                .replace(",", "")
                .lower()
            )
            self.assertIn("3500", budget)
            self.assertIn("5000", budget)
            self.assertIn("not", budget)
            names = translator.translate(
                "请王经理检查录音质量，李工程师负责中文转写和英文翻译。"
            )["text"].lower()
            self.assertIn("wang", names)
            self.assertIn("li", names)

    def test_silence_produces_no_caption_jobs(self):
        jobs = []
        gate = Segmenter(lambda a, b: jobs.append((a, b)))
        for _ in range(100):
            gate.feed(np.zeros(1600, dtype=np.float32))
        gate.finish()
        self.assertEqual(jobs, [])

    def test_downsample_filters_high_frequency_aliasing(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.wav"
            time = np.arange(48000) / 48000
            # 12kHz must be removed when reducing to 16kHz; 1kHz must remain.
            signal = 0.4 * np.sin(2 * np.pi * 1000 * time) + 0.4 * np.sin(
                2 * np.pi * 12000 * time
            )
            with wave.open(str(path), "wb") as output:
                output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                output.writeframes((signal * 32767).astype("<i2").tobytes())
            samples = load_audio(path)
            self.assertEqual(len(samples), RATE)
            spectrum = np.abs(np.fft.rfft(samples[1600:14400]))
            self.assertGreater(spectrum[800], spectrum[3200] * 100)

    @unittest.skipUnless(
        os.environ.get("MEETING_MODEL_TESTS") == "1",
        "Opt in with MEETING_MODEL_TESTS=1 after model setup",
    )
    def test_native_translation_and_no_stale_text_on_silence(self):
        samples = load_audio(ROOT / "fixtures/chinese-meeting.wav")[: 4 * RATE]
        with Whisper("base", log_dir=ROOT / "sessions/test-engine") as engine:
            chinese = engine.infer(samples)["text"]
            english = engine.infer(samples, True)["text"]
            self.assertIn("大家好", chinese)
            self.assertIn("meeting", english.lower())
            silence = engine.infer(np.zeros(2 * RATE, dtype=np.float32))
            self.assertEqual(silence["text"], "")
            self.assertGreater(engine.peak_memory_mib(), 0)


if __name__ == "__main__":
    unittest.main()
