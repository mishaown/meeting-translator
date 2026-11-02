"""Small, dependency-free platform detection shared by setup and capture."""

import platform
import sysconfig


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
