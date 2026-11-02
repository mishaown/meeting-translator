"""Platform capture command and macOS CoreAudio worker using the shared file protocol."""

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import queue
import sys
import threading
import time
import wave

from platform_support import target_platform

ROOT = Path(__file__).resolve().parent
RATE = 16000


def capture_command(
    output=None, source="mic", seconds=0, parent_id=0, *, list_devices=False
):
    if target_platform().startswith("windows"):
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "capture.ps1"),
        ]
        return command + (
            ["-ListDevices"]
            if list_devices
            else [
                "-OutputDirectory",
                str(output),
                "-Source",
                source,
                "-Seconds",
                str(seconds),
                "-ParentProcessId",
                str(parent_id),
            ]
        )
    command = [sys.executable, str(ROOT / "capture.py")]
    if not list_devices:
        selected_devices(source)
    return command + (
        ["--list-devices"]
        if list_devices
        else [
            "--output",
            str(output),
            "--source",
            source,
            "--seconds",
            str(seconds),
            "--parent-id",
            str(parent_id),
        ]
    )


class CaptureClock:
    """Thread-safe audio clock excluding paused time."""

    def __init__(self):
        self.lock = threading.RLock()
        self.started = time.monotonic()
        self.paused_at = None
        self.paused_seconds = 0

    def elapsed(self):
        with self.lock:
            return (
                (self.paused_at if self.paused_at is not None else time.monotonic())
                - self.started
                - self.paused_seconds
            )

    def pause(self, requested):
        with self.lock:
            if requested and self.paused_at is None:
                self.paused_at = time.monotonic()
            elif not requested and self.paused_at is not None:
                self.paused_seconds += time.monotonic() - self.paused_at
                self.paused_at = None


def parent_alive(pid):
    if not pid:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def selected_devices(source):
    def value(name):
        device = os.environ.get(name)
        return int(device) if device is not None and device.isdecimal() else device

    result = {}
    if source in ("mic", "both"):
        result["microphone"] = value("MEETING_MIC_DEVICE")
    if source in ("system", "both"):
        device = value("MEETING_SYSTEM_DEVICE")
        if device is None:
            raise ValueError(
                "macOS computer audio needs a routed loopback input. Set MEETING_SYSTEM_DEVICE to its device name or index; see README.md."
            )
        result["system"] = device
    return result


def record_mac(output, source, seconds, parent_id, sd):
    import numpy as np

    devices = selected_devices(source)
    output.mkdir(parents=True, exist_ok=True)
    packets = queue.Queue(maxsize=64)
    failed = threading.Event()
    clock = CaptureClock()
    tracks = {}

    def callback(name):
        def receive(data, frames, timing, status):
            if status:
                failed.set()
                return
            with clock.lock:
                if clock.paused_at is not None:
                    return
                try:
                    packets.put_nowait((name, data.copy(), clock.elapsed()))
                except queue.Full:
                    failed.set()

        return receive

    def write_packet(packet):
        name, data, elapsed = packet
        audio, stream, frames = tracks[name]
        samples = (np.clip(data.mean(axis=1), -1, 1) * 32767).astype("<i2")
        expected = max(0, round(elapsed * RATE) - len(samples))
        missing = expected - frames
        if (frames == 0 and missing > 0) or missing > 3200:
            while missing > 0:
                count = min(missing, RATE)
                audio.writeframes(b"\0\0" * count)
                frames += count
                missing -= count
        audio.writeframes(samples.tobytes())
        stream.flush()
        tracks[name] = (audio, stream, frames + len(samples))

    with ExitStack() as files, ExitStack() as streams:
        inputs = []
        for name, device in devices.items():
            info = sd.query_devices(device, "input")
            channels = min(2, int(info["max_input_channels"]))
            if channels < 1:
                raise ValueError(f"{name} is not an input device")
            stream = files.enter_context((output / (name + ".wav")).open("w+b"))
            audio = files.enter_context(wave.open(stream, "wb"))
            audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            audio.writeframes(b"")
            stream.flush()
            tracks[name] = (audio, stream, 0)
            inputs.append(
                streams.enter_context(
                    sd.InputStream(
                        device=device,
                        samplerate=RATE,
                        blocksize=1600,
                        channels=channels,
                        dtype="float32",
                        extra_settings=sd.CoreAudioSettings(),
                        callback=callback(name),
                    )
                )
            )
        (output / "capture.state").write_text("recording")
        (output / "capture.ready").touch()
        try:
            while (
                not (output / "capture.stop").exists()
                and parent_alive(parent_id)
                and (seconds <= 0 or clock.elapsed() < seconds)
            ):
                if failed.is_set() or any(not stream.active for stream in inputs):
                    raise RuntimeError(
                        "Audio capture overflowed or the device disconnected. Captured audio has been saved; see capture.log."
                    )
                paused = (output / "capture.pause").exists()
                clock.pause(paused)
                (output / "capture.state").write_text(
                    "paused" if paused else "recording"
                )
                try:
                    write_packet(packets.get(timeout=0.05))
                except queue.Empty:
                    pass
        finally:
            streams.close()
            while not packets.empty():
                write_packet(packets.get_nowait())
            (output / "capture.finished").write_text(str(clock.elapsed()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source", choices=("mic", "system", "both"), default="mic")
    parser.add_argument("--seconds", type=float, default=0)
    parser.add_argument("--parent-id", type=int, default=0)
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args()
    if not target_platform().startswith("macos"):
        parser.error("This worker uses macOS CoreAudio; Windows uses capture.ps1.")
    import sounddevice as sd

    if args.list_devices:
        print(sd.query_devices())
        return
    if args.output is None:
        parser.error("--output is required")
    record_mac(args.output, args.source, args.seconds, args.parent_id, sd)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Capture failed: {error}", file=sys.stderr)
        sys.exit(1)
