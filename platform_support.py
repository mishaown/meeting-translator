"""Small, dependency-free platform detection shared by setup and capture."""

import platform


def target_platform(system=None, machine=None):
    system = system or platform.system()
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
