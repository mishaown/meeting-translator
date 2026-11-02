"""Browser-facing recording jobs, session persistence and live caption processing."""

from contextlib import ExitStack
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
import uuid
import wave

import numpy as np
from capture import capture_command
from engine import ROOT, RATE, Whisper
from meeting import SHANGHAI, Segmenter, load_audio
from translator import TextTranslator
from model_catalog import model_path

DONE = {"complete", "error", "cancelled"}


class Store:
    def __init__(self, root=None):
        self.root = Path(root or ROOT / "sessions")
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def path(self, identity):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identity):
            raise ValueError("Invalid recording ID")
        path = self.root / identity
        if not path.is_dir() or not path.resolve().is_relative_to(self.root.resolve()):
            raise FileNotFoundError("Recording not found")
        return path

    @staticmethod
    def read(path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return default

    @staticmethod
    def write(path, value):
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def meta(self, identity, update=None):
        with self.lock:
            path = self.path(identity)
            legacy = self.read(path / "session.json", {})
            result = self.read(
                path / "ui-session.json",
                {
                    "title": (
                        "Imported recording"
                        if identity.endswith("import")
                        else "Meeting " + identity[:8]
                    ),
                    "created_at": legacy.get(
                        "started_at",
                        datetime.fromtimestamp(
                            path.stat().st_mtime, SHANGHAI
                        ).isoformat(),
                    ),
                    "source": legacy.get("source", "file"),
                    "state": "complete",
                },
            )
            if update:
                result.update(update)
                self.write(path / "ui-session.json", result)
            return result

    def rows(self, identity):
        path = self.path(identity)
        if (path / "ui-captions.json").exists():
            return self.read(path / "ui-captions.json", [])
        result = []
        try:
            for index, line in enumerate(
                (path / "captions.jsonl").read_text(encoding="utf-8").splitlines()
            ):
                try:
                    row = json.loads(line)
                    row.setdefault("id", f"legacy_{index}")
                    row.setdefault("speaker", "unknown")
                    result.append(row)
                except ValueError:
                    continue
        except OSError:
            pass
        return result

    def save_rows(self, identity, rows):
        with self.lock:
            self.write(self.path(identity) / "ui-captions.json", rows)

    def add_row(self, identity, row):
        with self.lock:
            rows = self.rows(identity)
            rows.append(row)
            self.save_rows(identity, rows)

    def patch_row(self, identity, row_id, update):
        with self.lock:
            rows = self.rows(identity)
            row = next((row for row in rows if row["id"] == row_id), None)
            if row is None:
                raise FileNotFoundError("Caption not found")
            row.update(update)
            self.save_rows(identity, rows)

    def profiles(self, identity, update=None):
        with self.lock:
            target = self.path(identity) / "speakers.json"
            profiles = self.read(target, {})
            if update:
                profiles.update(update)
                self.write(target, profiles)
            return profiles

    def audio_path(self, identity, track=None):
        path = self.path(identity)
        choices = (
            [track + ".wav"]
            if track in ("mixed", "microphone", "system")
            else ["mixed.wav", "microphone.wav", "system.wav"]
        )
        for name in choices:
            if (path / name).exists():
                return path / name
        raise FileNotFoundError("No recording audio is available")

    @staticmethod
    def duration(path):
        try:
            with wave.open(str(path)) as audio:
                return audio.getnframes() / audio.getframerate()
        except (OSError, wave.Error, EOFError):
            return 0

    def detail(self, identity):
        with self.lock:
            path = self.path(identity)
            result = dict(self.meta(identity), id=identity)
            result["tracks"] = [
                name
                for name in ("mixed", "microphone", "system")
                if (path / (name + ".wav")).exists()
            ]
            result["duration"] = (
                self.duration(self.audio_path(identity)) if result["tracks"] else 0
            )
            result["rows"] = self.rows(identity)
            result["speakers"] = self.profiles(identity)
            result["runs"] = self.runs(identity)
            return result

    def checkpoint(self, identity):
        """Save each result version separately; never overwrite another model's run."""
        with self.lock:
            meta = self.meta(identity)
            run_id = meta.get("active_run")
            rows = self.rows(identity)
            if not run_id and not rows:
                return
            if not run_id:
                run_id = uuid.uuid4().hex[:12]
                meta = self.meta(identity, {"active_run": run_id})
            directory = self.path(identity) / "runs"
            directory.mkdir(exist_ok=True)
            self.write(
                directory / (run_id + ".json"),
                {
                    "id": run_id,
                    "rows": rows,
                    "speakers": self.profiles(identity),
                    "options": meta.get("options", {}),
                    "created_at": meta.get("run_started_at", meta.get("created_at")),
                    "state": meta.get("state"),
                    "elapsed_seconds": meta.get("elapsed_seconds"),
                    "devices": meta.get("devices", {}),
                    "models_used": sorted(
                        {
                            row["model"] + "/" + row.get("translation_model", "")
                            for row in rows
                            if row.get("model")
                        }
                    ),
                    "inference_seconds": round(
                        sum(
                            row.get("transcription_seconds", 0)
                            + row.get("translation_seconds", 0)
                            for row in rows
                        ),
                        3,
                    ),
                },
            )

    def runs(self, identity):
        active = self.meta(identity).get("active_run")
        results = []
        for path in (self.path(identity) / "runs").glob("*.json"):
            run = self.read(path, {})
            if run.get("id"):
                results.append(
                    {
                        key: value
                        for key, value in run.items()
                        if key not in ("rows", "speakers")
                    }
                    | {
                        "caption_count": len(run.get("rows", [])),
                        "active": run["id"] == active,
                    }
                )
        return sorted(
            results, key=lambda run: run.get("created_at") or "", reverse=True
        )

    def run_detail(self, identity, run_id):
        if not re.fullmatch(r"[a-f0-9]{12}", run_id):
            raise ValueError("Invalid result version")
        if run_id == self.meta(identity).get("active_run"):
            self.checkpoint(identity)
        run = self.read(self.path(identity) / "runs" / (run_id + ".json"), None)
        if run is None:
            raise FileNotFoundError("Result version not found")
        return run

    def begin_run(self, identity, options):
        with self.lock:
            previous = self.rows(identity)
            self.checkpoint(identity)
            if previous:
                self.write(self.path(identity) / "ui-captions.previous.json", previous)
            self.save_rows(identity, [])
            # New diarization IDs belong to this run; prior manual names remain in its snapshot.
            self.write(self.path(identity) / "speakers.json", {})
            self.meta(
                identity,
                {
                    "active_run": uuid.uuid4().hex[:12],
                    "options": dict(options),
                    "run_started_at": datetime.now(SHANGHAI).isoformat(),
                    "elapsed_seconds": None,
                    "devices": {},
                },
            )
            self.checkpoint(identity)

    def list(self):
        result = []
        for path in self.root.iterdir():
            if path.is_dir() and any(
                (path / name).exists()
                for name in ("mixed.wav", "microphone.wav", "system.wav")
            ):
                try:
                    item = self.detail(path.name)
                    item["caption_count"] = len(item.pop("rows"))
                    item.pop("speakers")
                    result.append(item)
                except (OSError, ValueError):
                    continue
        return sorted(result, key=lambda row: row.get("created_at", ""), reverse=True)


class Job:
    def __init__(self, app, identity, options, mode="record"):
        self.app, self.store, self.identity = app, app.store, identity
        self.output = self.store.path(identity)
        self.options = dict(options)
        self.mode, self.state = mode, "starting"
        self.message, self.error = "Preparing your meeting…", None
        self.duration, self.level = 0.0, 0.0
        self.devices = {}
        self.started = time.monotonic()
        self.pending, self.caption_count = 0, 0
        self.stop_event = threading.Event()
        self.tasks = queue.Queue()
        self.flags_lock = threading.RLock()
        self.pipeline = threading.Thread(target=self.caption_loop, daemon=True)
        self.thread = threading.Thread(target=self.run, daemon=True)

    def set_state(self, state, message):
        self.state, self.message = state, message
        self.store.meta(
            self.identity,
            {"state": state, "message": message, "duration": self.duration},
        )

    def snapshot(self):
        return {
            "id": self.identity,
            "state": self.state,
            "mode": self.mode,
            "message": self.message,
            "error": self.error,
            "duration": self.duration,
            "level": self.level,
            "pending": self.pending,
            "caption_count": self.caption_count,
            "options": self.flags(),
            "devices": dict(self.devices),
        }

    def flags(self):
        with self.flags_lock:
            return dict(self.options)

    def update_flags(self, options):
        with self.flags_lock:
            self.options.update(
                {
                    key: options[key]
                    for key in (
                        "transcription",
                        "translation",
                        "diarization",
                        "model",
                        "translation_model",
                        "device",
                    )
                    if key in options
                }
            )
        self.store.meta(self.identity, {"options": self.flags()})

    def emit(self, start, end):
        flags = self.flags()
        if flags["transcription"] or flags["translation"]:
            self.tasks.put((start, end, flags))
            self.pending = self.tasks.qsize()

    def run(self):
        self.pipeline.start()
        try:
            if self.mode == "record":
                self.capture_loop()
            else:
                self.reprocess()
            self.set_state(
                "stopping" if self.mode == "record" else "processing",
                "Finishing remaining captions…",
            )
        except Exception as error:
            self.error = str(error)
        finally:
            self.tasks.put(None)
            self.pipeline.join()
            self.level = 0
            if self.error:
                self.set_state("error", self.error)
            else:
                self.set_state(
                    (
                        "cancelled"
                        if self.mode == "process" and self.stop_event.is_set()
                        else "complete"
                    ),
                    "Recording saved" if self.mode == "record" else "Transcript ready",
                )
            self.store.meta(
                self.identity,
                {
                    "elapsed_seconds": round(time.monotonic() - self.started, 3),
                    "devices": self.devices,
                },
            )
            self.store.checkpoint(self.identity)

    def capture_loop(self):
        source = self.options["source"]
        tracks = [
            self.output / name
            for name in (
                ["microphone.wav", "system.wav"]
                if source == "both"
                else ["microphone.wav" if source == "mic" else "system.wav"]
            )
        ]
        with (self.output / "capture.log").open("wb") as log:
            capture = subprocess.Popen(
                capture_command(self.output, source, parent_id=os.getpid()),
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                deadline = time.monotonic() + 30
                while not (self.output / "capture.ready").exists():
                    if capture.poll() is not None:
                        raise RuntimeError(
                            "Audio could not start. Check microphone permissions, device routing and capture.log."
                        )
                    if time.monotonic() > deadline:
                        raise TimeoutError("Audio device took too long to start.")
                    time.sleep(0.05)
                self.set_state("recording", "Listening for speech…")
                with (self.output / "mixed.wav").open("wb") as stream, wave.open(
                    stream, "wb"
                ) as mixed:
                    mixed.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                    mixed.writeframes(b"")
                    stream.flush()
                    segmenter = Segmenter(self.emit)
                    position, paused_before = 0, False
                    while True:
                        if self.stop_event.is_set():
                            (self.output / "capture.stop").touch()
                        finished = capture.poll() is not None
                        try:
                            capture_state = (
                                (self.output / "capture.state").read_text().strip()
                            )
                        except FileNotFoundError:
                            capture_state = "recording"
                        paused = capture_state == "paused" or (
                            not capture_state and paused_before
                        )
                        try:
                            lengths = []
                            for path in tracks:
                                with wave.open(str(path)) as audio:
                                    lengths.append(audio.getnframes())
                        except (EOFError, wave.Error):
                            if finished:
                                raise RuntimeError(
                                    "The saved audio header could not be read."
                                )
                            time.sleep(0.05)
                            continue
                        available = max(lengths) if finished else min(lengths)
                        while position < available:
                            end = min(position + 1600, available)
                            if end - position < 1600 and not finished and not paused:
                                break
                            signals = []
                            for path, length in zip(tracks, lengths):
                                samples = (
                                    load_audio(path, position, min(end, length))
                                    if position < length
                                    else np.zeros(0)
                                )
                                signals.append(
                                    np.pad(samples, (0, end - position - len(samples)))
                                )
                            samples = np.mean(signals, axis=0).astype(np.float32)
                            mixed.writeframes(
                                (np.clip(samples, -1, 1) * 32767)
                                .astype("<i2")
                                .tobytes()
                            )
                            stream.flush()
                            self.level = min(
                                1.0, float(np.sqrt(np.mean(samples * samples))) * 8
                            )
                            segmenter.feed(samples)
                            position = end
                            self.duration = position / RATE
                        if (
                            paused
                            and not paused_before
                            and not self.stop_event.is_set()
                        ):
                            segmenter.finish()
                            self.level = 0
                            self.set_state("paused", "Recording paused")
                        elif (
                            not paused
                            and paused_before
                            and not self.stop_event.is_set()
                        ):
                            self.set_state("recording", "Listening for speech…")
                        paused_before = paused
                        if finished:
                            segmenter.finish()
                            if capture.returncode:
                                raise RuntimeError(
                                    "The audio device disconnected. Your captured audio has been kept."
                                )
                            break
                        time.sleep(0.05)
            finally:
                (self.output / "capture.stop").touch()
                try:
                    capture.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    capture.terminate()
                    capture.wait()

    def reprocess(self):
        self.set_state("processing", "Reading the saved recording…")
        audio = self.store.audio_path(self.identity)
        # Always process the available source track, including legacy source-only sessions.
        self.processing_audio = audio
        with wave.open(str(audio)) as reader:
            if (
                reader.getframerate() != RATE
                or reader.getsampwidth() != 2
                or reader.getnchannels() != 1
            ):
                raise ValueError("This recording must be a 16 kHz mono PCM16 WAV.")
            self.duration = reader.getnframes() / RATE
            segmenter = Segmenter(self.emit)
            while not self.stop_event.is_set():
                raw = reader.readframes(1600)
                if not raw:
                    break
                segmenter.feed(
                    np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
                )
            segmenter.finish()

    def caption_loop(self):
        engine = translator = tracker = None
        engine_key = translator_key = None
        try:
            with ExitStack() as speech_stack, ExitStack() as text_stack:
                while True:
                    task = self.tasks.get()
                    if task is None:
                        break
                    start, end, requested = task
                    self.pending = self.tasks.qsize() + 1
                    current = self.flags() if self.mode == "record" else requested
                    transcribe = requested["transcription"] and current["transcription"]
                    translate = requested["translation"] and current["translation"]
                    if not (transcribe or translate) or (
                        self.mode == "process" and self.stop_event.is_set()
                    ):
                        self.pending = self.tasks.qsize()
                        continue
                    try:
                        self.message = "Preparing captions…"
                        audio_path = getattr(
                            self, "processing_audio", self.output / "mixed.wav"
                        )
                        samples = load_audio(audio_path, start, end)
                        # Switch at utterance boundaries, with only one model of each task loaded.
                        settings = current if self.mode == "record" else requested
                        speech_key = (settings["model"], settings["device"])
                        text_key = (settings["translation_model"], settings["device"])
                        if engine_key != speech_key:
                            if engine is not None:
                                speech_stack.close()
                            engine = speech_stack.enter_context(
                                Whisper(
                                    speech_key[0], 6, self.output, device=speech_key[1]
                                )
                            )
                            engine_key = speech_key
                        if translator is not None and (
                            translator_key != text_key or not translate
                        ):
                            text_stack.close()
                            translator = None
                        if requested["diarization"] and current["diarization"]:
                            if tracker is None:
                                from speakers import SpeakerTracker

                                tracker = SpeakerTracker()
                            turns = tracker.turns(samples)
                        else:
                            turns = [
                                {
                                    "start": 0,
                                    "end": len(samples) / RATE,
                                    "speaker": "unknown",
                                    "uncertain": False,
                                    "overlap": False,
                                }
                            ]
                        for turn in turns:
                            clip = samples[
                                int(turn["start"] * RATE) : int(turn["end"] * RATE)
                            ]
                            chinese = engine.infer(clip)
                            if not chinese["text"]:
                                continue
                            row = dict(
                                turn,
                                id=uuid.uuid4().hex[:12],
                                start=start / RATE + turn["start"],
                                end=start / RATE + turn["end"],
                                chinese=chinese["text"] if transcribe else "",
                                english="",
                                status="translating" if translate else "ready",
                                model=speech_key[0],
                                translation_model=text_key[0],
                                transcription_seconds=chinese["seconds"],
                                transcription_device=engine.device_info(),
                            )
                            self.devices["transcription"] = engine.device_info()
                            self.store.add_row(self.identity, row)
                            self.caption_count += 1
                            if translate and (
                                self.mode != "record" or self.flags()["translation"]
                            ):
                                try:
                                    if text_key[0] == "whisper":
                                        english = engine.infer(clip, translate=True)
                                        device = engine.device_info()
                                    else:
                                        if translator is None:
                                            translator = text_stack.enter_context(
                                                TextTranslator(
                                                    6,
                                                    self.output,
                                                    model=model_path(
                                                        text_key[0], "translation"
                                                    ).name,
                                                    device=text_key[1],
                                                )
                                            )
                                            translator_key = text_key
                                        english = translator.translate(chinese["text"])
                                        device = translator.device_info()
                                    self.devices["translation"] = device
                                    self.store.patch_row(
                                        self.identity,
                                        row["id"],
                                        {
                                            "english": english["text"],
                                            "status": "ready",
                                            "translation_seconds": english["seconds"],
                                            "translation_device": device,
                                        },
                                    )
                                except Exception as error:
                                    self.store.patch_row(
                                        self.identity,
                                        row["id"],
                                        {"status": "error", "error": str(error)},
                                    )
                                    raise
                            elif translate:
                                self.store.patch_row(
                                    self.identity, row["id"], {"status": "ready"}
                                )
                        self.message = (
                            "Listening for speech…"
                            if self.state == "recording"
                            else "Finishing captions…"
                        )
                        self.store.meta(self.identity, {"devices": dict(self.devices)})
                    except Exception as error:
                        self.error = "Caption processing failed: " + str(error)
                        self.message = self.error
                    self.pending = self.tasks.qsize()
        except Exception as error:
            self.error = "Caption worker failed: " + str(error)
        finally:
            self.pending = 0


class Application:
    def __init__(self, store=None):
        self.store = store or Store()
        self.lock = threading.RLock()
        self.current = None
        self.defaults = {
            "source": "mic",
            "model": "base",
            "translation_model": "qwen25-1.5b",
            "device": "auto",
            "transcription": True,
            "translation": True,
            "diarization": True,
        }

    def settings(self, options):
        settings = dict(self.defaults)
        settings.update({key: options[key] for key in settings if key in options})
        if settings["source"] not in ("mic", "system", "both") or settings[
            "device"
        ] not in ("auto", "cpu", "gpu"):
            raise ValueError("Choose a valid audio source and device.")
        model_path(settings["model"], require_installed=False)
        model_path(
            settings["translation_model"], "translation", require_installed=False
        )
        if settings["translation_model"] == "whisper" and settings["model"] == "turbo":
            raise ValueError(
                "Whisper turbo needs a Qwen model for English translation. Choose another Whisper model for direct translation."
            )
        for key in ("transcription", "translation", "diarization"):
            if not isinstance(settings[key], bool):
                raise ValueError("Caption switches must be on or off.")
        return settings

    @staticmethod
    def require_models(settings):
        if settings["transcription"] or settings["translation"]:
            model_path(settings["model"])
        if settings["translation"]:
            model_path(settings["translation_model"], "translation")

    def status(self):
        with self.lock:
            return {
                "job": self.current.snapshot() if self.current else None,
                "defaults": dict(self.defaults),
            }

    def busy(self):
        return self.current is not None and self.current.state not in DONE

    def start(self, options):
        with self.lock:
            if self.busy():
                raise ValueError("Stop the current session before starting another.")
            settings = self.settings(options)
            self.require_models(settings)
            self.defaults = settings
            path = self.store.root / (
                datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S-%f") + "-meeting"
            )
            path.mkdir(parents=True)
            self.store.meta(
                path.name,
                {
                    "title": str(options.get("title") or "Untitled meeting")[:120],
                    "source": settings["source"],
                    "created_at": datetime.now(SHANGHAI).isoformat(),
                    "options": settings,
                    "state": "starting",
                },
            )
            self.store.save_rows(path.name, [])
            self.store.begin_run(path.name, settings)
            self.current = Job(self, path.name, settings)
            self.current.thread.start()
            return self.current.snapshot()

    def control(self, action, options=None):
        with self.lock:
            if action == "options":
                if self.busy() and self.current.mode == "process":
                    raise ValueError(
                        "Finish this comparison run before changing models."
                    )
                settings = self.settings(options or {})
                values = {
                    key: settings[key]
                    for key in (
                        "transcription",
                        "translation",
                        "diarization",
                        "model",
                        "translation_model",
                        "device",
                    )
                }
                self.defaults.update(values)
                if self.busy():
                    self.current.update_flags(values)
                return self.status()
            if not self.busy():
                raise ValueError("No meeting is running.")
            job = self.current
            if action == "stop":
                job.stop_event.set()
                job.set_state("stopping", "Saving audio and finishing captions…")
                (job.output / "capture.stop").touch()
            elif (
                action == "pause" and job.mode == "record" and job.state == "recording"
            ):
                (job.output / "capture.pause").touch()
                job.set_state("pausing", "Pausing recording…")
            elif action == "resume" and job.mode == "record" and job.state == "paused":
                (job.output / "capture.pause").unlink(missing_ok=True)
                job.set_state("resuming", "Resuming recording…")
            else:
                raise ValueError("This control is not available in the current state.")
            return self.status()

    def process(self, identity, options=None):
        with self.lock:
            if self.busy():
                raise ValueError(
                    "Finish the current session before processing another recording."
                )
            self.store.audio_path(identity)
            settings = self.settings(
                dict(options or {}, transcription=True, translation=True)
            )
            self.require_models(settings)
            self.defaults.update(settings)
            self.store.begin_run(identity, settings)
            self.current = Job(self, identity, settings, "process")
            self.current.thread.start()
            return self.current.snapshot()

    def select_run(self, identity, run_id):
        with self.lock, self.store.lock:
            if self.busy():
                raise ValueError(
                    "Finish the current session before switching saved results."
                )
            run = self.store.run_detail(identity, run_id)
            self.store.checkpoint(identity)
            self.store.save_rows(identity, run["rows"])
            self.store.write(
                self.store.path(identity) / "speakers.json", run["speakers"]
            )
            self.store.meta(
                identity,
                {
                    "active_run": run_id,
                    "options": run["options"],
                    "state": run["state"],
                    "run_started_at": run["created_at"],
                    "elapsed_seconds": run.get("elapsed_seconds"),
                    "devices": run.get("devices", {}),
                },
            )
            return self.store.detail(identity)

    def shutdown(self):
        with self.lock:
            job = self.current
            if job and job.state not in DONE:
                job.stop_event.set()
                (job.output / "capture.stop").touch()
        if job and job.thread.is_alive():
            job.thread.join(timeout=200)
