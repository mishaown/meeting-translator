"""Native desktop bundle. Models and user recordings are deliberately excluded."""

from pathlib import Path
import sys
from importlib.metadata import distributions

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
datas = [(str(root / name), name) for name in ("web", "licenses")]
datas += [
    (str(root / name), ".")
    for name in ("Capture.cs", "capture.ps1", "LICENSE", "THIRD_PARTY.md")
]
runtime_packages = {
    "numpy",
    "sherpa-onnx",
    "sherpa-onnx-core",
    "pywebview",
    "pythonnet",
    "clr-loader",
    "cffi",
    "pycparser",
    "proxy-tools",
    "bottle",
    "typing-extensions",
    "sounddevice",
    "pyinstaller",
}
for distribution in distributions():
    name = distribution.metadata["Name"].lower().replace("_", "-")
    if name not in runtime_packages and not name.startswith("pyobjc"):
        continue
    for entry in distribution.files or []:
        if (
            entry.name.lower().startswith(("license", "copying", "notice"))
            and distribution.locate_file(entry).is_file()
        ):
            datas.append(
                (
                    str(distribution.locate_file(entry)),
                    str(Path("third-party-licenses") / name / entry.parent.name),
                )
            )
folders = (
    (
        "whisper",
        "llama",
        "naudio.core/lib/netstandard2.0",
        "naudio.wasapi/lib/netstandard2.0",
    )
    if sys.platform == "win32"
    else ("whisper-macos/bin", "llama")
)
for folder in folders:
    base = root / "runtime" / folder
    if not base.is_dir():
        raise RuntimeError(
            f"Runtime missing: {base}. Run setup_models.py --runtimes-only before building."
        )
    for path in base.rglob("*"):
        if path.is_file() and (
            path.suffix in (".dll", ".dylib", ".metallib", ".metal")
            or path.name
            in (
                "whisper-server.exe",
                "llama-server.exe",
                "whisper-server",
                "llama-server",
            )
            or path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
        ):
            datas.append((str(path), str(path.parent.relative_to(root))))

speaker_data, speaker_binaries, speaker_imports = collect_all("sherpa_onnx")
a = Analysis(
    [str(root / "desktop.py")],
    pathex=[str(root)],
    binaries=speaker_binaries,
    datas=datas + speaker_data,
    hiddenimports=speaker_imports
    + (
        ["webview.platforms.winforms", "webview.platforms.edgechromium"]
        if sys.platform == "win32"
        else ["webview.platforms.cocoa", "sounddevice"]
    ),
    excludes=[
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "matplotlib",
        "pytest",
        "IPython",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MeetingTranslator",
    console=False,
    upx=False,
)
bundle = COLLECT(exe, a.binaries, a.datas, name="MeetingTranslator", upx=False)
if sys.platform == "darwin":
    app = BUNDLE(
        bundle,
        name="MeetingTranslator.app",
        bundle_identifier="io.github.mishaown.meeting-translator",
        info_plist={
            "CFBundleDisplayName": "Meeting Translator",
            "CFBundleShortVersionString": "0.1.0",
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": "Record your microphone locally to create meeting captions and translations.",
        },
    )
