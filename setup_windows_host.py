"""Build a native ARM64 Win32/WebView2 window host without Python.NET or .NET."""

import hashlib
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = "1.0.4314.35"
SHA256 = "90778deb1b77e36292828b4b5833c9255c27533f40e0ddd55891ae718944edc4"


def main():
    if not shutil.which("cmake"):
        raise RuntimeError(
            "Install CMake and Visual Studio C++ build tools with ARM64 support, or use the native Windows ARM64 workflow."
        )
    archive = ROOT / "downloads" / f"microsoft.web.webview2.{VERSION}.nupkg"
    archive.parent.mkdir(exist_ok=True)

    def verified():
        if not archive.is_file():
            return False
        with archive.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == SHA256

    if not verified():
        url = f"https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/{VERSION}/microsoft.web.webview2.{VERSION}.nupkg"
        temporary = archive.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=120) as source, temporary.open(
            "wb"
        ) as stream:
            while block := source.read(1024 * 1024):
                stream.write(block)
        with temporary.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
                raise RuntimeError("WebView2 SDK checksum mismatch")
        temporary.replace(archive)
    sdk = ROOT / "downloads/webview2-sdk" / VERSION
    with zipfile.ZipFile(archive) as package:
        for member in (
            "build/native/include/WebView2.h",
            "build/native/arm64/WebView2Loader.dll",
            "LICENSE.txt",
        ):
            package.extract(member, sdk)
    build = ROOT / "build/windows-host"
    subprocess.run(
        [
            "cmake",
            "-S",
            str(ROOT / "native/windows"),
            "-B",
            str(build),
            "-A",
            "ARM64",
            f"-DWEBVIEW2_SDK={sdk}",
        ],
        check=True,
    )
    subprocess.run(
        ["cmake", "--build", str(build), "--config", "Release", "--parallel", "2"],
        check=True,
    )
    destination = ROOT / "runtime/windows-host"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(build / "Release/MeetingTranslatorHost.exe", destination)
    shutil.copy2(sdk / "build/native/arm64/WebView2Loader.dll", destination)
    shutil.copy2(sdk / "LICENSE.txt", destination / "LICENSE-WebView2.txt")
    print(f"Native ARM64 window host built: {destination}")


if __name__ == "__main__":
    main()
