"""Install checksum-pinned models and runtimes for Windows and macOS."""

import argparse
import hashlib
import shutil
import subprocess
import tarfile
from http.client import HTTPException
from pathlib import Path
import urllib.request
import zipfile
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from model_catalog import TRANSCRIPTION_MODELS, TRANSLATION_MODELS
from platform_support import target_platform

from app_paths import DATA_ROOT as ROOT

ARTIFACTS = {
    "whisper-opencl": (
        "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-win-opencl-adreno-arm64.zip",
        "915b48289f362c5f4cf33f3ffdd30f1f43de68f6103a3088858e346635719007",
        ROOT / "downloads/whisper-b5454-opencl-arm64.zip",
    ),
    "llama-vulkan": (
        "https://github.com/ggml-org/llama.cpp/releases/download/b11946/llama-b11946-bin-win-vulkan-arm64.zip",
        "4cc64606390bafdf47f9d325788f19a36949f08f3d45839c76c0cbff8d7e369c",
        ROOT / "downloads/llama-b11946-vulkan-arm64.zip",
    ),
    "llama-opencl": (
        "https://github.com/ggml-org/llama.cpp/releases/download/b11946/llama-b11946-bin-win-opencl-adreno-arm64.zip",
        "d21deb8273de7070b61f6c47eac7b5c05f02ab324a9f76ed382094073d6d5b9e",
        ROOT / "downloads/llama-b11946-opencl-arm64.zip",
    ),
    "runtime": (
        "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-win-cpu-arm64.zip",
        "28c37e7b598c3d9bbfef94f3bd67f2ee6f12b7c86f3da5edcf5e4308d615ff6d",
        ROOT / "downloads/whisper-b5454-cpu-arm64.zip",
    ),
    "vad": (
        "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin",
        "2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987",
        ROOT / "models/ggml-silero-v6.2.0.bin",
    ),
    "llama": (
        "https://github.com/ggml-org/llama.cpp/releases/download/b11946/llama-b11946-bin-win-cpu-arm64.zip",
        "54e78605dbca3ecf3470f3c9f48634b70b417652701a787f90c25c90bc705609",
        ROOT / "downloads/llama-b11946-cpu-arm64.zip",
    ),
    "naudio.core": (
        "https://api.nuget.org/v3-flatcontainer/naudio.core/2.2.1/naudio.core.2.2.1.nupkg",
        "794645dbfd30e4880663d52d9d9224d55c301fa54228ff623c95d572c7887347",
        ROOT / "downloads/naudio/naudio.core.zip",
    ),
    "naudio.wasapi": (
        "https://api.nuget.org/v3-flatcontainer/naudio.wasapi/2.2.1/naudio.wasapi.2.2.1.nupkg",
        "c8396b8dbab86a2619edfd9158beaf18c21cb86677099b5594950fd8651b9d1a",
        ROOT / "downloads/naudio/naudio.wasapi.zip",
    ),
}
ARM_RUNTIME_ARTIFACTS = {key: ARTIFACTS[key] for key in ("runtime", "llama")}
MODEL_SIZES = {}
for identity, specification in TRANSCRIPTION_MODELS.items():
    ARTIFACTS[identity] = (
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
        + specification["file"],
        specification["sha256"],
        ROOT / "models" / specification["file"],
    )
    MODEL_SIZES[identity] = specification["bytes"]
for identity, specification in TRANSLATION_MODELS.items():
    if specification["file"]:
        ARTIFACTS[identity] = (
            f"https://huggingface.co/{specification['repo']}/resolve/main/{specification['file']}",
            specification["sha256"],
            ROOT / "models" / specification["file"],
        )
        MODEL_SIZES[identity] = specification["bytes"]


def configure_platform(target, acceleration=False):
    """Select native assets before downloading; models remain platform-independent."""
    if target not in ("windows-arm64", "windows-x64", "macos-arm64", "macos-x64"):
        raise ValueError("Unsupported runtime target")
    ARTIFACTS.update(ARM_RUNTIME_ARTIFACTS)
    if target == "windows-x64":
        ARTIFACTS["runtime"] = (
            "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip",
            "6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb",
            ROOT / "downloads/whisper-b5454-x64.zip",
        )
        ARTIFACTS["llama"] = (
            "https://github.com/ggml-org/llama.cpp/releases/download/b11946/llama-b11946-bin-win-cpu-x64.zip",
            "8a702bb07c873b133c56eb3acd92a48d7c5c9ad339c6435eddc5185e46e1a043",
            ROOT / "downloads/llama-b11946-cpu-x64.zip",
        )
    if target.startswith("windows"):
        if acceleration and target != "windows-arm64":
            raise ValueError(
                "Automatic GPU setup currently supports Adreno ARM64 and macOS Metal; Windows x64 uses CPU."
            )
        return ["runtime", "llama", "naudio.core", "naudio.wasapi"] + (
            ["whisper-opencl", "llama-opencl"] if acceleration else []
        )
    architecture = target.split("-")[1]
    ARTIFACTS["llama"] = (
        f"https://github.com/ggml-org/llama.cpp/releases/download/b11946/llama-b11946-bin-macos-{architecture}.tar.gz",
        {
            "arm64": "d618da5e7bbb19077f086e6a7e1d77eab55b9bfd481f7531aadada86d13db9ed",
            "x64": "6f6740aea7c018bca7bfb0da78acbbe82301ecf56f0417672cce804423cbff41",
        }[architecture],
        ROOT / f"downloads/llama-b11946-macos-{architecture}.tar.gz",
    )
    ARTIFACTS["whisper-source"] = (
        "https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/refs/tags/b5454",
        "9aa617f410d9c7f0dae952eb2b77eafdcfb310c2d622cca1f83633fd89a213bc",
        ROOT / "downloads/whisper-b5454-source.tar.gz",
    )
    if not shutil.which("cmake") or not shutil.which("clang"):
        raise RuntimeError(
            "macOS setup needs CMake and Xcode Command Line Tools. See README.md."
        )
    return ["llama", "whisper-source"]


def build_mac_whisper():
    source = ROOT / "runtime/whisper-source/whisper.cpp-b5454"
    build = ROOT / "runtime/whisper-macos"
    architecture = "arm64" if target_platform() == "macos-arm64" else "x86_64"
    subprocess.run(
        [
            "cmake",
            "-S",
            str(source),
            "-B",
            str(build),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0",
            f"-DCMAKE_OSX_ARCHITECTURES={architecture}",
            "-DWHISPER_BUILD_TESTS=OFF",
            "-DWHISPER_BUILD_SERVER=ON",
            "-DGGML_METAL=ON",
            "-DGGML_METAL_EMBED_LIBRARY=ON",
        ],
        check=True,
    )
    subprocess.run(
        [
            "cmake",
            "--build",
            str(build),
            "--config",
            "Release",
            "--target",
            "whisper-server",
            "-j",
            "2",
        ],
        check=True,
    )


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def ranged_download(url, target, total, workers=2):
    """Resume large downloads in bounded, individually validated HTTP ranges."""
    offset = target.stat().st_size if target.exists() else 0
    if offset > total:
        offset = 0
    chunk = 4 * 1024 * 1024

    def fetch(bounds):
        start, end = bounds
        for attempt in range(4):
            try:
                request = urllib.request.Request(
                    url + f"?download=true&chunk={start}&end={end}",
                    headers={
                        "Range": f"bytes={start}-{end}",
                        "User-Agent": "meeting-translator/0.1",
                    },
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    if (
                        response.status != 206
                        or response.headers.get("Content-Range")
                        != f"bytes {start}-{end}/{total}"
                    ):
                        raise RuntimeError("Download server did not honor byte range")
                    data = response.read(end - start + 2)
                if len(data) != end - start + 1:
                    raise RuntimeError("Incomplete byte range")
                return data
            except (OSError, RuntimeError, HTTPException):
                if attempt == 3:
                    raise
                time.sleep(1)

    with target.open("ab" if offset else "wb") as output, ThreadPoolExecutor(
        max_workers=workers
    ) as pool:
        while offset < total:
            bounds = [
                (start, min(start + chunk, total) - 1)
                for start in range(offset, min(offset + workers * chunk, total), chunk)
            ]
            for data in pool.map(fetch, bounds):
                output.write(data)
                offset += len(data)
            output.flush()
            print(f"Downloaded {offset // 1024**2}/{total // 1024**2} MiB", flush=True)


def crc32(path):
    value = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value = zlib.crc32(block, value)
    return value


def download(name, workers=2):
    url, expected, target = ARTIFACTS[name]
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or digest(target) != expected:
        temporary = target.with_suffix(target.suffix + ".part")
        print(f"Downloading {name}...", flush=True)
        for attempt in range(3):
            try:
                if name in MODEL_SIZES:
                    ranged_download(url, temporary, MODEL_SIZES[name], workers)
                else:
                    request = urllib.request.Request(
                        url, headers={"User-Agent": "meeting-translator/0.1"}
                    )
                    with urllib.request.urlopen(
                        request, timeout=120
                    ) as source, temporary.open("wb") as output:
                        while block := source.read(1024 * 1024):
                            output.write(block)
                if digest(temporary) != expected:
                    temporary.write_bytes(b"")
                    raise RuntimeError(
                        f"Checksum mismatch or incomplete download: {temporary}"
                    )
                break
            except (OSError, RuntimeError, HTTPException):
                if attempt == 2:
                    raise
                print(f"Retrying {name} after incomplete download...", flush=True)
                time.sleep(1)
        temporary.replace(target)
    print(f"Verified {target.name}", flush=True)
    if target.name.endswith(".tar.gz"):
        destination = ROOT / "runtime" / name
        with tarfile.open(target) as archive:
            archive.extractall(destination, filter="data")
    elif name in (
        "runtime",
        "llama",
        "whisper-opencl",
        "llama-vulkan",
        "llama-opencl",
    ) or name.startswith("naudio."):
        destination = ROOT / "runtime" / ("whisper" if name == "runtime" else name)
        with zipfile.ZipFile(target) as archive:
            for item in archive.infolist():
                if (
                    not (destination / item.filename)
                    .resolve()
                    .is_relative_to(destination.resolve())
                ):
                    raise RuntimeError("Unsafe archive path")
            for item in archive.infolist():
                existing = destination / item.filename
                if item.is_dir():
                    existing.mkdir(parents=True, exist_ok=True)
                elif not existing.exists() or crc32(existing) != item.CRC:
                    archive.extract(item, destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        nargs="+",
        choices=[
            *TRANSCRIPTION_MODELS,
            *(key for key in TRANSLATION_MODELS if key != "whisper"),
        ],
        default=["base", "qwen25-1.5b"],
    )
    parser.add_argument(
        "--acceleration",
        action="store_true",
        help="Install Adreno runtimes on Windows ARM64; macOS builds already include Metal",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List model IDs and download sizes without installing",
    )
    parser.add_argument(
        "--runtimes-only",
        action="store_true",
        help="Install or build native runtimes without downloading model weights",
    )
    parser.add_argument(
        "--workers",
        type=int,
        choices=range(1, 9),
        default=2,
        help="Parallel 4 MiB download chunks (default: 2)",
    )
    args = parser.parse_args()
    if args.list:
        for identity, size in MODEL_SIZES.items():
            print(f"{identity:16} {size / 1024**2:8.1f} MiB")
        raise SystemExit(0)
    try:
        target = target_platform()
        runtimes = configure_platform(target, args.acceleration)
    except (ValueError, RuntimeError) as error:
        parser.error(str(error))
    print(f"Setting up {target}", flush=True)
    for artifact in runtimes + (
        [] if args.runtimes_only else ["vad", *dict.fromkeys(args.models)]
    ):
        download(artifact, args.workers)
    if target.startswith("macos"):
        build_mac_whisper()
