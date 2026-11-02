"""Small, dependency-free platform detection shared by setup and capture."""

import platform
import sysconfig
from pathlib import Path


def target_platform(system=None, machine=None):
    system = system or platform.system()
    if machine is None and system == "Windows":
        # An x64 Python/executable under Windows ARM emulation still needs x64 DLLs.
        machine = {"win-amd64": "amd64", "win-arm64": "arm64"}.get(
            sysconfig.get_platform()
        )
    machine = (machine or platform.machine()).lower()
    architecture = {
        "amd64": "x64",
        "x86_64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine)
    if system not in ("Windows", "Darwin") or architecture is None:
        raise RuntimeError(
            "Supported platforms: Windows ARM64/x64 and macOS Apple Silicon/Intel. Use native 64-bit Python."
        )
    return ("windows" if system == "Windows" else "macos") + "-" + architecture


def require_arm64_binary(path):
    """Reject an accidental x64 executable or DLL in a native Windows ARM64 build."""
    path = Path(path)
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise RuntimeError(f"Not a Windows binary: {path.name}")
        stream.seek(0x3C)
        offset = int.from_bytes(stream.read(4), "little")
        stream.seek(offset)
        header = stream.read(6)
    if header != b"PE\0\0\x64\xaa":
        raise RuntimeError(f"Native ARM64 binary required: {path.name}")
