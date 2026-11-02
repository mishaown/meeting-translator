# Contributing

Small, focused pull requests are welcome. Open an issue with the problem, expected
behavior and reproduction steps. For hardware issues, include CPU architecture, RAM,
OS version, Python architecture, model IDs, actual backend and relevant redacted logs.
Never attach private recordings, transcripts, credentials or full machine diagnostics.

## Architecture

| File | Responsibility |
| --- | --- |
| `local_app.py` | Loopback HTTP server, request checks, static assets and audio ranges |
| `desktop.py` / `app_paths.py` | Native window, frozen worker dispatch and writable user-data paths |
| `model_setup.py` | Serialized, user-initiated model downloads for browser and desktop |
| `gui_runtime.py` | Recording jobs, persistence, saved runs and caption orchestration |
| `meeting.py` | CLI, WAV conversion, segmentation and capture coordination |
| `engine.py` / `translator.py` | Speech and text inference clients |
| `inference_runtime.py` | Shared native process lifecycle and device selection |
| `model_catalog.py` | Single model catalog and installed-model lookup |
| `setup_models.py` / `setup_speakers.py` | Verified upstream downloads |
| `capture.ps1` / `Capture.cs` | NAudio WASAPI microphone and loopback capture |
| `capture.py` / `platform_support.py` | Platform dispatch, macOS CoreAudio capture and architecture detection |
| `speakers.py` | Local diarization and session-scoped speaker clustering |
| `web/` | Editable HTML, CSS and JavaScript; no build step |

Capture and inference run separately. Audio is saved while inference tasks queue frame
offsets rather than audio buffers. Model switches happen between utterances; old native
processes close before replacements load. Speech and translation share lifecycle code
without inheriting from each other's inference client. Download hashing and runtime CRC
checks use bounded reads. Default download concurrency is two 4 MiB ranges.

The app still loads transcript histories and saved-run JSON for some API calls; do not
claim constant memory use for arbitrary meeting lengths. CLI WAV conversion also remains
a whole-file operation. Performance PRs should include reproducible before/after measurements.

## Setup and checks

Follow README setup for your platform. On macOS use `.venv/bin/python` instead of the
Windows interpreter paths below. Core Python tests need only NumPy, not downloaded
models or Sherpa-ONNX. They exercise API safety, seeking, version preservation, model switching,
number normalization and resampling. They generate temporary silent WAVs and mock native clients.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m pip install black
.\.venv\Scripts\python.exe -m black --check .
npm ci
npm test
```

Python formatting uses Black's defaults. Keep source readable; do not commit minified files,
model weights, binaries, recordings, caches, research scratch files or generated reports.
Node is needed only for DOM tests. `package-lock.json` pins their dependency tree.
CI runs model-free tests on Linux, Windows and macOS, plus DOM checks. Separate Windows/macOS
jobs install/build runtimes and run server `--help` without weights. These jobs are configured
but have not been executed on GitHub yet; recording and real-model accelerator validation
still need the actual hardware.

## Native integration (explicit opt-in)

Download default models, `tiny`, speaker models and runtimes first. Generate a local
Chinese fixture with the Windows Huihui/Chinese voice installed. Nothing is played or recorded
by fixture generation. The generator requires Windows; on a Mac provide an equivalent
PCM16 Chinese speech WAV at `fixtures/chinese-meeting.wav`. These checks are separate from
the default suite:

```powershell
.\.venv\Scripts\python.exe setup_models.py --models base tiny qwen25-1.5b
.\.venv\Scripts\python.exe setup_speakers.py
powershell -NoProfile -File .\make_test_audio.ps1
$env:MEETING_MODEL_TESTS = '1'
.\.venv\Scripts\python.exe -m unittest discover -v
Remove-Item Env:MEETING_MODEL_TESTS
```

Real device tests separately require `$env:MEETING_CAPTURE_TESTS = '1'`.
They record the current system output device, so opt in only when appropriate.
`verify_capture_lifecycle.py` checks parent-process exit behavior and also starts device capture.
`benchmark_models.py --device auto` compares installed models on the generated fixture and
writes local reports. Timing excludes accuracy assessment; include input duration and hardware
when sharing measurements. Generated fixture audio and reports stay outside Git.

## Pull requests

Explain the user-visible problem, the resulting behavior and how you checked it. Add
regression coverage for meaningful behavior changes. Preserve session compatibility, offline
operation, origin/path checks and audio safety. For a model addition, update the catalog,
verified SHA-256, download size, documentation and third-party license notices together.

Fork the repository, create a branch from `main`, and open a pull request with your
changes. Review `git status` before committing and use your own Git identity.

## Platform changes

Keep capture behind `capture_command()` and preserve the `capture.ready`, `capture.state`,
`capture.pause`, `capture.stop`, and `capture.finished` protocol. Audio tracks remain mono
16 kHz PCM16 WAVs. The Mac callback uses a bounded packet queue and excludes paused time;
overflow or device failure finalizes saved files and reports an error. Test adapters with
fake devices, then validate on real hardware before claiming end-to-end support.

Runtime selection follows the interpreter architecture. Pin new archive hashes, keep CPU
and strict GPU behavior distinct, and preserve the legacy Windows ARM64 runtime layout.

## Desktop builds and releases

Build on Windows x64, Apple Silicon, or Intel macOS with a matching Python 3.13 interpreter.
The published v0.1.0 Windows desktop build uses x64 Python. The `native-windows-arm64`
branch adds a separate native host and a Windows ARM64 build; keep its preview status
distinct from the published x64 release's emulation path.

```powershell
python -m pip install -r requirements-desktop.txt
python setup_models.py --runtimes-only
python build_desktop.py
```

`desktop.spec` bundles editable web assets, Python dependencies, capture helpers and only
the native servers/libraries needed at runtime. It excludes model weights, recordings,
downloads and native build intermediates. Builds are directories/app bundles rather than
single-file self-extractors, to avoid unpacking native libraries on every launch.

`build_desktop.py` checks bundled imports, resources, the local HTTP server and native
server startup without opening an audio device or downloading weights. For a native-window
load check, run the resulting executable with `--smoke-test --data-dir <temporary-folder>`.
That check loads the interface and exits automatically without recording. On Mac, start
the executable inside `MeetingTranslator.app/Contents/MacOS` for command-line checks.

Run the **Desktop release** workflow in GitHub Actions to build all three architectures.
It uploads ZIPs and SHA-256 files. Once every build passes, it creates a **draft prerelease**
for review. Check a native window, microphone/loopback permissions, stop/close behavior,
exports and model installation before publishing the draft. Current packages have no
Windows signing certificate or Apple Developer ID/notarization credentials configured.

### Native Windows ARM64 development

Use native ARM64 Python 3.13, CMake, and Visual Studio C++ build tools with the ARM64
compiler and Windows SDK. The dedicated GitHub workflow supplies these on `windows-11-arm`.

```powershell
python -m pip install -r requirements.txt pyinstaller==6.22.3
python setup_models.py --runtimes-only --acceleration
python setup_windows_host.py
python build_desktop.py
```

`setup_windows_host.py` verifies a pinned Microsoft WebView2 NuGet SDK, then compiles
`native/windows/host.cpp`. The small host uses the system ARM64 WebView2 Runtime and
keeps its SDK redistributable license beside its loader. It embeds the existing loopback
interface and watches the backend process so its window closes if the backend exits.
The Python backend still owns capture, model setup, inference and saving recordings.

The ARM64 bundle excludes pywebview/Python.NET, preserves the same native WASAPI capture
helper, and checks PE machine types for the frontend, host, loader and model servers.
`--smoke-test` additionally loads the interface in a hidden native window. Passing startup
checks does not replace real microphone/system capture and long-meeting validation.
