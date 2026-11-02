"""Record meetings and produce local Chinese captions and English translation."""

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
import wave

import numpy as np

from capture import capture_command
from engine import ROOT, RATE, Whisper
from translator import TextTranslator

SHANGHAI = timezone(timedelta(hours=8))


def session_path(label):
    path = (
        ROOT
        / "sessions"
        / (datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S-%f") + "-" + label)
    )
    path.mkdir(parents=True)
    return path


def load_audio(path, start=0, end=None):
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2:
            raise ValueError(
                "Input must be PCM 16-bit WAV. Convert other formats before importing."
            )
        rate, channels = audio.getframerate(), audio.getnchannels()
        audio.setpos(min(start, audio.getnframes()))
        frames = audio.readframes(
            audio.getnframes() - start if end is None else max(0, end - start)
        )
    x = (
        np.frombuffer(frames, dtype="<i2")
        .astype(np.float32)
        .reshape(-1, channels)
        .mean(axis=1)
        / 32768
    )
    if rate != RATE and len(x):
        # Windowed-sinc low-pass before interpolation; no native DSP dependency.
        cutoff = 0.45 * min(rate, RATE) / rate
        positions = np.arange(-63, 64)
        taps = 2 * cutoff * np.sinc(2 * cutoff * positions) * np.hamming(127)
        taps /= taps.sum()
        x = np.convolve(x, taps, mode="full")[63 : 63 + len(x)]
        x = np.interp(
            np.arange(int(len(x) * RATE / rate)) * rate / RATE, np.arange(len(x)), x
        ).astype(np.float32)
    return x


def write_audio(path, samples):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        audio.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


class Segmenter:
    """Energy gate with preroll, silence endpointing and a maximum caption window.

    This is intentionally a baseline; it does not claim neural speech detection.
    Windows are disjoint so repeated words are never removed speculatively.
    """

    def __init__(self, emit, seconds=8, threshold=0.008):
        self.emit, self.maximum, self.threshold = emit, int(seconds * RATE), threshold
        self.position = 0
        self.start = None
        self.last_voice = 0
        self.boundary = 0

    def feed(self, samples):
        block_start = self.position
        self.position += len(samples)
        rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0
        if rms >= self.threshold:
            if self.start is None:
                self.start = max(self.boundary, block_start - int(0.25 * RATE))
            self.last_voice = self.position
        if self.start is not None:
            silent = self.position - self.last_voice >= int(0.6 * RATE)
            full = self.position - self.start >= self.maximum
            if silent or full:
                self._finish()

    def _finish(self):
        if self.start is not None:
            if self.position - self.start >= int(0.35 * RATE):
                self.emit(self.start, self.position)
            self.boundary, self.start = self.position, None

    def finish(self):
        self._finish()


def subtitle_time(seconds):
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


class CaptionWorker:
    def __init__(self, engine, audio_path, output, english_only=False, translator=None):
        self.engine, self.audio_path, self.output = engine, audio_path, output
        self.english_only = english_only
        self.translator = translator
        self.queue = queue.Queue()
        self.errors = []
        self.closing = False
        self.rows = []
        self.thread = threading.Thread(target=self.run, name="captions", daemon=True)
        self.thread.start()

    def submit(self, start, end):
        self.queue.put((start, end, time.perf_counter()))

    def run(self):
        with (self.output / "captions.jsonl").open("w", encoding="utf-8") as file, (
            self.output / "chinese.srt"
        ).open("w", encoding="utf-8") as zh_sub, (self.output / "english.srt").open(
            "w", encoding="utf-8"
        ) as en_sub:
            while True:
                task = self.queue.get()
                if task is None:
                    return
                start, end, submitted = task
                try:
                    samples = load_audio(self.audio_path, start, end)
                    chinese = {"text": "", "seconds": 0}
                    if not self.english_only or self.translator:
                        chinese = self.engine.infer(samples)
                    if self.translator and not chinese["text"]:
                        continue
                    if not self.english_only:
                        print(f"[{start/RATE:7.1f}s] ZH: {chinese['text']}", flush=True)
                    english = (
                        self.translator.translate(chinese["text"])
                        if self.translator
                        else self.engine.infer(samples, translate=True)
                    )
                    print(f"           EN: {english['text']}", flush=True)
                    pending = max(0, self.queue.qsize() - (1 if self.closing else 0))
                    row = {
                        "start": start / RATE,
                        "end": end / RATE,
                        "chinese": chinese["text"] if not self.english_only else "",
                        "english": english["text"],
                        "chinese_seconds": chinese["seconds"],
                        "english_seconds": english["seconds"],
                        "delay_after_chunk_seconds": time.perf_counter() - submitted,
                        "pending_chunks": pending,
                    }
                    self.rows.append(row)
                    file.write(json.dumps(row, ensure_ascii=False) + "\n")
                    file.flush()
                    for stream, text in [
                        (zh_sub, row["chinese"]),
                        (en_sub, english["text"]),
                    ]:
                        if text:
                            stream.write(
                                f"{len(self.rows)}\n{subtitle_time(start/RATE)} --> {subtitle_time(end/RATE)}\n{text}\n\n"
                            )
                            stream.flush()
                    if pending > 2 and not self.closing:
                        print(
                            f"Model is behind by {pending} chunks; audio is still recording.",
                            flush=True,
                        )
                except Exception as error:
                    self.errors.append(str(error))
                    file.write(
                        json.dumps(
                            {
                                "start": start / RATE,
                                "end": end / RATE,
                                "error": str(error),
                            }
                        )
                        + "\n"
                    )
                    file.flush()
                    print(
                        f"Caption error: {error}. Audio is preserved.",
                        file=sys.stderr,
                        flush=True,
                    )

    def close(self):
        self.closing = True
        self.queue.put(None)
        print(f"Finishing {self.queue.qsize()-1} queued caption chunks...", flush=True)
        self.thread.join()
        delays = [r["delay_after_chunk_seconds"] for r in self.rows]
        summary = {
            "segments": len(self.rows),
            "errors": self.errors,
            "mean_delay_after_chunk_seconds": (
                float(np.mean(delays)) if delays else None
            ),
            "p95_delay_after_chunk_seconds": (
                float(np.percentile(delays, 95)) if delays else None
            ),
            "whisper_peak_memory_mib": self.engine.peak_memory_mib(),
            "whisper_model": self.engine.model.name,
        }
        if self.translator:
            summary["translator_peak_memory_mib"] = self.translator.peak_memory_mib()
            summary["translator_model"] = self.translator.model.name
        (self.output / "summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )


def record(args):
    output = session_path("meeting")
    print(f"Session: {output}", flush=True)
    print("Loading local model before recording starts...", flush=True)
    with ExitStack() as stack:
        engine = stack.enter_context(Whisper(args.model, args.threads, output))
        translator = (
            stack.enter_context(TextTranslator(args.threads, output))
            if args.translation_backend == "text"
            else None
        )
        log = (output / "capture.log").open("wb")
        command = capture_command(output, args.source, args.seconds, os.getpid())
        capture = subprocess.Popen(
            command,
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        worker = None
        stop_requested = threading.Event()

        def request_stop(signum, frame):
            if stop_requested.is_set():
                raise KeyboardInterrupt
            stop_requested.set()
            (output / "capture.stop").touch()
            print("Stopping recording and saving...", flush=True)

        old_handler = signal.signal(signal.SIGINT, request_stop)
        (output / "session.json").write_text(
            json.dumps(
                {
                    "started_at": datetime.now(SHANGHAI).isoformat(),
                    "source": args.source,
                    "model": args.model,
                    "spoken_language": "zh",
                    "translation_language": "en",
                    "chunk_seconds": args.chunk_seconds,
                    "threshold": args.threshold,
                    "translation_backend": args.translation_backend,
                    "translator_model": translator.model.name if translator else None,
                    "beam_size": 5,
                    "vad": "silero-v6.2.0",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        tracks = [
            output / name
            for name in (
                ["microphone.wav", "system.wav"]
                if args.source == "both"
                else ["microphone.wav" if args.source == "mic" else "system.wav"]
            )
        ]
        try:
            deadline = time.monotonic() + 30
            while not (output / "capture.ready").exists():
                if capture.poll() is not None:
                    raise RuntimeError(f"Capture failed. See {output / 'capture.log'}")
                if time.monotonic() > deadline:
                    raise TimeoutError("Audio capture startup timed out")
                time.sleep(0.1)
            print(
                "RECORDING. Press Ctrl+C to stop and save. Use headphones to reduce acoustic echo.",
                flush=True,
            )
            with (output / "mixed.wav").open("wb") as stream, wave.open(
                stream, "wb"
            ) as mixed:
                mixed.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                mixed.writeframes(b"")
                stream.flush()
                worker = CaptionWorker(
                    engine, output / "mixed.wav", output, args.english_only, translator
                )
                segmenter = Segmenter(worker.submit, args.chunk_seconds, args.threshold)
                position = 0
                while True:
                    try:
                        lengths = []
                        for path in tracks:
                            with wave.open(str(path), "rb") as audio:
                                lengths.append(audio.getnframes())
                    except (EOFError, wave.Error):
                        time.sleep(0.05)
                        continue
                    finished = capture.poll() is not None
                    available = max(lengths) if finished else min(lengths)
                    while position < available:
                        end = min(position + 1600, available)
                        if end - position < 1600 and not finished:
                            break
                        signals = []
                        for path, length in zip(tracks, lengths):
                            x = (
                                load_audio(path, position, min(end, length))
                                if position < length
                                else np.zeros(0)
                            )
                            signals.append(np.pad(x, (0, end - position - len(x))))
                        samples = np.mean(signals, axis=0).astype(np.float32)
                        mixed.writeframes(
                            (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
                        )
                        stream.flush()
                        segmenter.feed(samples)
                        position = end
                    if finished:
                        segmenter.finish()
                        break
                    time.sleep(0.05)
                if capture.returncode:
                    raise RuntimeError(
                        f"Capture exited with an error. Audio preserved in {output}; check capture.log"
                    )
        except KeyboardInterrupt:
            (output / "capture.stop").touch()
            print(
                "Stop requested; original audio will be saved. Reprocess the WAV to recover any unfinished captions.",
                flush=True,
            )
        finally:
            (output / "capture.stop").touch()
            try:
                capture.wait(timeout=10)
            except subprocess.TimeoutExpired:
                capture.terminate()
                capture.wait()
                print(
                    "Capture needed forced shutdown; WAV headers may require recovery.",
                    file=sys.stderr,
                )
            log.close()
            if worker:
                worker.close()
            signal.signal(signal.SIGINT, old_handler)
        print(f"Saved: {output}", flush=True)
        if worker and worker.errors:
            raise RuntimeError(
                "Some caption chunks failed; see captions.jsonl and reprocess the saved audio"
            )
    return output


def process(args):
    output = session_path("import")
    samples = load_audio(args.file)
    write_audio(output / "mixed.wav", samples)
    with ExitStack() as stack:
        engine = stack.enter_context(Whisper(args.model, args.threads, output))
        translator = (
            stack.enter_context(TextTranslator(args.threads, output))
            if args.translation_backend == "text"
            else None
        )
        worker = CaptionWorker(
            engine, output / "mixed.wav", output, args.english_only, translator
        )
        segmenter = Segmenter(worker.submit, args.chunk_seconds, args.threshold)
        for offset in range(0, len(samples), 1600):
            segmenter.feed(samples[offset : offset + 1600])
            if args.realtime:
                time.sleep(0.1)
        segmenter.finish()
        worker.close()
        if worker.errors:
            raise RuntimeError("Some captions failed; see captions.jsonl")
    print(f"Saved: {output}", flush=True)
    return output


def benchmark(args):
    samples = load_audio(args.file)
    duration = len(samples) / RATE
    if not duration:
        raise ValueError("Cannot benchmark empty audio")
    output = session_path("benchmark")
    report = []
    with ExitStack() as stack:
        translator = (
            stack.enter_context(TextTranslator(args.threads, output))
            if args.translation_backend == "text"
            else None
        )
        for model in args.models:
            with Whisper(model, args.threads, output / model) as engine:
                chinese = engine.infer(samples)
                english = (
                    translator.translate(chinese["text"])
                    if translator
                    else engine.infer(samples, True)
                )
                row = {
                    "model": model,
                    "threads": args.threads,
                    "audio_seconds": duration,
                    "chinese_seconds": chinese["seconds"],
                    "english_seconds": english["seconds"],
                    "combined_rtf": (chinese["seconds"] + english["seconds"])
                    / duration,
                    "whisper_peak_memory_mib": engine.peak_memory_mib(),
                    "translation_backend": args.translation_backend,
                    "translator_model": translator.model.name if translator else None,
                    "beam_size": 5,
                    "vad": "silero-v6.2.0",
                    "translator_peak_memory_mib": (
                        translator.peak_memory_mib() if translator else None
                    ),
                    "chinese": chinese["text"],
                    "english": english["text"],
                }
                report.append(row)
                print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)
    (output / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Saved: {output}")


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("devices", help="List default microphone and playback device")
    for name in ("record", "process"):
        sub = commands.add_parser(name)
        sub.add_argument("--model", choices=["base", "small"], default="base")
        sub.add_argument(
            "--translation-backend", choices=["text", "whisper"], default="text"
        )
        sub.add_argument("--threads", type=int, default=6)
        sub.add_argument("--chunk-seconds", type=float, default=8)
        sub.add_argument(
            "--threshold",
            type=float,
            default=0.008,
            help="RMS energy gate, not neural VAD",
        )
        sub.add_argument(
            "--english-only",
            action="store_true",
            help="Only display/save English captions",
        )
        if name == "record":
            sub.add_argument(
                "--source", choices=["both", "mic", "system"], default="mic"
            )
            sub.add_argument(
                "--seconds", type=float, default=0, help="0 records until Ctrl+C"
            )
        else:
            sub.add_argument("file", type=Path)
            sub.add_argument(
                "--realtime",
                action="store_true",
                help="Feed imported WAV at normal speed",
            )
    bench = commands.add_parser("benchmark")
    bench.add_argument("file", type=Path)
    bench.add_argument(
        "--models", nargs="+", choices=["base", "small"], default=["base", "small"]
    )
    bench.add_argument("--threads", type=int, default=6)
    bench.add_argument(
        "--translation-backend", choices=["text", "whisper"], default="text"
    )
    args = parser.parse_args()
    if getattr(args, "chunk_seconds", 6) < 1 or getattr(args, "threshold", 0.008) <= 0:
        parser.error("chunk-seconds must be at least 1 and threshold must be positive")
    if args.command == "devices":
        subprocess.run(capture_command(list_devices=True), check=True)
    elif args.command == "record":
        record(args)
    elif args.command == "process":
        process(args)
    else:
        benchmark(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
