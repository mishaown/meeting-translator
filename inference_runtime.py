"""Native server lifecycle and verified accelerator selection."""

from pathlib import Path
import re
import ctypes
from ctypes import wintypes
import socket
import subprocess
import time
import urllib.request
from platform_support import target_platform
from app_paths import ASSET_ROOT

from app_paths import DATA_ROOT as ROOT


def runtime_roots():
    return list(dict.fromkeys((ROOT / "runtime", ASSET_ROOT / "runtime")))


def find_runtime(folder, name):
    return next(
        (
            path
            for root in runtime_roots()
            for path in (root / folder).rglob(name)
            if path.is_file()
        ),
        None,
    )


def candidates(task, requested):
    if requested not in ("auto", "cpu", "gpu"):
        raise ValueError("Choose Automatic, CPU or GPU")
    target = target_platform()
    if target.startswith("macos"):
        folder, name = (
            ("whisper-macos", "whisper-server")
            if task == "transcription"
            else ("llama", "llama-server")
        )
        executable = find_runtime(folder, name)
        if executable:
            return [
                (executable, backend)
                for backend in (
                    ("gpu-metal", "cpu")
                    if requested == "auto"
                    else (("cpu",) if requested == "cpu" else ("gpu-metal",))
                )
            ]
        raise FileNotFoundError(
            "The native macOS runtime is missing. Run setup_models.py."
        )
    layouts = (
        [("whisper-opencl", "whisper-server.exe", "gpu-opencl")]
        if task == "transcription"
        else [("llama-opencl", "llama-server.exe", "gpu-opencl")]
    )
    result = []
    if requested != "cpu" and target == "windows-arm64":
        for folder, name, backend in layouts:
            executable = find_runtime(folder, name)
            if executable:
                result.append((executable, backend))
    if requested != "gpu":
        folder, name = (
            ("whisper", "whisper-server.exe")
            if task == "transcription"
            else ("llama", "llama-server.exe")
        )
        cpu = find_runtime(folder, name)
        if cpu is None and task == "transcription":
            cpu = find_runtime("Release", "whisper-server.exe")
        if cpu is not None:
            result.append((cpu, "cpu"))
    if not result:
        raise FileNotFoundError(
            "The selected inference runtime is missing or unsupported. Run setup_models.py; Windows x64 supports CPU, ARM64 optionally supports --acceleration."
        )
    return result


def launch(owner, executable, arguments, log_name):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    owner.url = f"http://127.0.0.1:{port}"
    owner.port, owner.exe = port, executable
    owner.log_path = owner.log_dir / log_name
    owner.log = owner.log_path.open("wb")
    try:
        owner.process = subprocess.Popen(
            [str(executable), *arguments, "--host", "127.0.0.1", "--port", str(port)],
            cwd=executable.parent,
            stdout=owner.log,
            stderr=owner.log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except BaseException:
        owner.log.close()
        raise
    owner.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if owner.process.poll() is not None:
                raise RuntimeError(
                    f"Inference server exited. See {owner.log_path.name}."
                )
            try:
                with owner.http.open(owner.url + "/health", timeout=1) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.2)
        raise TimeoutError("Model startup timed out")
    except BaseException:
        owner.close()
        raise


def verify_gpu(owner, task):
    log = owner.log_path.read_text(encoding="utf-8", errors="replace")
    if task == "translation":
        match = re.search(r"offloaded\s+(\d+)/(\d+)\s+layers to GPU", log)
        if not match or int(match[1]) == 0:
            raise RuntimeError("The runtime did not offload model layers to the GPU")
    elif not re.search(
        r"using\s+"
        + (r"(?:Metal|MTL\d+)" if owner.backend == "gpu-metal" else r"OpenCL\d*")
        + r"\s+backend",
        log,
        flags=re.I,
    ):
        raise RuntimeError(
            "The speech model did not initialize the selected GPU backend"
        )
    if task == "transcription" and re.search(
        r"failed to initialize .* backend", log, flags=re.I
    ):
        raise RuntimeError("The speech runtime reported a GPU initialization failure")
    devices = [
        line.strip()
        for line in log.splitlines()
        if re.search(
            r"Adreno|device.*Qualcomm|MTL\d+|Metal.*device|device.*Apple", line, re.I
        )
    ]
    return " · ".join(devices[:2])[:500] or owner.backend


def initialize(owner, task, arguments, device):
    owner.requested_device = device
    owner.fallback_reason = None
    for executable, backend in candidates(task, device):
        owner.backend = backend
        try:
            launch(owner, executable, arguments(backend), f"{task}-{backend}.log")
            owner.device_description = (
                verify_gpu(owner, task) if backend != "cpu" else "CPU"
            )
            return
        except Exception as error:
            if hasattr(owner, "process") and owner.process.poll() is None:
                owner.close()
            if device != "auto" or backend == "cpu":
                raise
            owner.fallback_reason = str(error)
    raise RuntimeError("No inference device could start")


class NativeServer:
    """Shared native process ownership for speech and text inference."""

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.log.close()

    def device_info(self):
        return {
            "backend": self.backend,
            "description": self.device_description,
            "fallback_reason": self.fallback_reason,
            "model": self.model.name,
        }

    def peak_memory_mib(self):
        if target_platform().startswith("macos"):
            # ps reports current RSS; retain the maximum of explicitly observed samples.
            rss = subprocess.check_output(
                ["/bin/ps", "-o", "rss=", "-p", str(self.process.pid)], text=True
            )
            self._observed_memory_mib = max(
                getattr(self, "_observed_memory_mib", 0), int(rss.strip()) / 1024
            )
            return self._observed_memory_mib

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "peak_working_set",
                    "working_set",
                    "peak_paged",
                    "paged",
                    "peak_nonpaged",
                    "nonpaged",
                    "pagefile",
                    "peak_pagefile",
                )
            ]

        counter = Counters()
        counter.cb = ctypes.sizeof(counter)
        query = ctypes.WinDLL("psapi").GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        query.restype = wintypes.BOOL
        if not query(
            wintypes.HANDLE(int(self.process._handle)),
            ctypes.byref(counter),
            counter.cb,
        ):
            raise ctypes.WinError()
        return counter.peak_working_set / 1024**2

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
