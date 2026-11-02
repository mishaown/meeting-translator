"""Local whisper.cpp process with one loaded model and serialized requests."""

import io
import json
from pathlib import Path
import time
import urllib.request
import uuid
import wave

import numpy as np
from inference_runtime import NativeServer, initialize
from model_catalog import model_path

from app_paths import DATA_ROOT as ROOT

RATE = 16000


def wav_bytes(samples):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        audio.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


class Whisper(NativeServer):
    def __init__(self, model="base", threads=6, log_dir=None, device="auto"):
        self.model = model_path(model)
        self.vad = ROOT / "models/ggml-silero-v6.2.0.bin"
        if not self.vad.exists():
            raise FileNotFoundError(
                "Run setup_models.py first: runtime or model is missing."
            )
        self.log_dir = Path(log_dir or ROOT / "sessions/engine")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._arguments = lambda backend: [
            "-m",
            str(self.model),
            "-t",
            str(threads),
            "-l",
            "zh",
            *(["-ng"] if backend == "cpu" else []),
            "-bo",
            "1",
            "-bs",
            "5",
            "-nlp",
            "--vad",
            "-vm",
            str(self.vad),
        ]
        initialize(self, "transcription", self._arguments, device)

    def infer(self, samples, translate=False):
        try:
            return self._infer(samples, translate)
        except OSError as error:
            if self.requested_device != "auto" or self.backend == "cpu":
                raise
            self.close()
            initialize(self, "transcription", self._arguments, "cpu")
            self.fallback_reason = str(error)
            return self._infer(samples, translate)

    def _infer(self, samples, translate=False):
        boundary = uuid.uuid4().hex
        parts = []
        fields = {
            "language": "zh",
            "translate": str(translate).lower(),
            "temperature": "0",
            "temperature_inc": "0",
            "best_of": "1",
            "beam_size": "5",
            "response_format": "verbose_json",
            "no_language_probabilities": "true",
            "vad": "true",
        }
        for key, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="chunk.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
        )
        parts.extend([wav_bytes(samples), f"\r\n--{boundary}--\r\n".encode()])
        request = urllib.request.Request(
            self.url + "/inference",
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        start = time.perf_counter()
        with self.http.open(request, timeout=180) as response:
            result = json.load(response)
        return {
            "text": result.get("text", "").strip(),
            "segments": result.get("segments", []),
            "seconds": time.perf_counter() - start,
        }
