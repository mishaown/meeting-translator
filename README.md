# 🎙️ Meeting Translator

**Record meetings locally. Read Chinese captions and English translations side by side.**

A lightweight browser interface and Python CLI for Windows ARM64/x64 and macOS (Apple Silicon/Intel). Capture a microphone,
computer audio, or both; pause and resume; revisit recordings; compare model results;
and correct speaker labels. Audio and inference stay on your computer after setup.
No cloud API, account, frontend build step, or Node.js installation is needed to use the app.

## 🖥️ Desktop app previews

Desktop builds open in their own native window. A local web renderer displays the same
interface, so the desktop and browser versions share features and fixes. You do not need
Python installed to run a packaged desktop app.

Download previews from [GitHub Releases](https://github.com/mishaown/meeting-translator/releases)
when available. The release workflow builds these archives:

| Download | How to run |
| --- | --- |
| `meeting-translator-windows-x64.zip` | Extract the entire folder, then run `MeetingTranslator.exe`. Keep `_internal` beside it. |
| `meeting-translator-macos-arm64.zip` | Extract and open `MeetingTranslator.app` on Apple Silicon. |
| `meeting-translator-macos-x64.zip` | Extract and open `MeetingTranslator.app` on Intel Macs. |

The published **v0.1.0** Windows desktop build uses x64 Python and runs on Windows 11 ARM64
through emulation. Native ARM64 work is isolated on the `native-windows-arm64` branch.
That branch uses a small **Win32/WebView2 host**, native ARM64 Python, and native inference
runtimes, with no Python.NET or .NET window-host dependency. Its packaged interface and
native runtime startup have passed checks on the development Surface Laptop 7.

Native preview artifacts are built by the
[Native Windows ARM64 workflow](https://github.com/mishaown/meeting-translator/actions/workflows/windows-arm64.yml).
The native preview bundles CPU runtimes and optional Adreno OpenCL runtimes; Automatic can
fall back to CPU. Model weights still download separately. Recordings and models use the
same user-data location as the x64 app; close one app before using the other. Long meetings,
real capture permissions and GPU behavior still need preview testing.

**On first launch:** click **Download models**, choose Balanced or Lightweight, and wait
for verification to finish. Lightweight uses Whisper tiny and direct audio translation;
Balanced adds Qwen2.5. Speaker models and additional models are optional. Click **Open
recorder** when setup completes. Model weights are excluded from app downloads. Native
CPU runtimes are bundled, so desktop users do not need CMake or a compiler. Mac builds
also include Metal support.

Desktop models, recordings and logs are stored separately from the application:

- Windows: `%LOCALAPPDATA%\MeetingTranslator`
- macOS: `~/Library/Application Support/MeetingTranslator`

Keep that folder when replacing the application with a new version. `MEETING_DATA_DIR`
or the desktop `--data-dir` argument can override it. Closing the desktop window stops
the server and saves an active recording; closing a browser tab does not.

Preview builds are **unsigned**, and Mac apps are not notarized. Windows needs
[Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/),
normally present on Windows 11. macOS may require approving the app under **Privacy &
Security → Open Anyway**. Microphone permissions and Mac loopback routing still apply.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for local builds and release automation.

> **Status:** early working prototype. Windows ARM64 has end-to-end development testing.
> Windows x64 runtimes and macOS adapters have separate validation notes below.
> Model weights and native executables are downloaded separately and are never part of this repository.

## 🚀 Quick start

Use **64-bit Python 3.13** matching your operating-system architecture, internet access
for setup, and a working audio input. Setup detects the current interpreter architecture.

### Windows 11 · ARM64 or x64

Run in PowerShell from this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup_models.py
.\.venv\Scripts\python.exe setup_speakers.py
.\.venv\Scripts\python.exe -X utf8 local_app.py --open
```

### macOS 14+ · Apple Silicon or Intel

Install Python 3.13, CMake and Apple's Xcode Command Line Tools first. If you use Homebrew,
`brew install cmake` provides CMake; `xcode-select --install` opens Apple's developer tools
installer. Use native ARM64 Python on Apple Silicon, or x64 on Intel.

Run in Terminal from this folder:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python setup_models.py
.venv/bin/python setup_speakers.py
.venv/bin/python local_app.py --open
```

Mac setup downloads the matching llama.cpp runtime and builds pinned whisper.cpp source
with CMake. Both CPU and Metal paths are available. This first build takes extra time and
storage; use current developer tools. Allow microphone access for the Terminal/Python host
in **System Settings → Privacy & Security → Microphone**. The default source is your microphone.

**Computer audio on macOS:** install and configure a virtual loopback device separately;
the app does not install drivers or change your audio routing. For example, follow
[BlackHole's routing instructions](https://github.com/ExistentialAudio/BlackHole)
to route output to headphones/speakers and BlackHole. Then list inputs and select the loopback
input explicitly before launching:

```bash
.venv/bin/python meeting.py devices
export MEETING_SYSTEM_DEVICE="BlackHole 2ch"
# Optional: choose the physical microphone by its listed name or index
export MEETING_MIC_DEVICE="MacBook Pro Microphone"
.venv/bin/python local_app.py --open
```

Set the microphone variable only if the name matches your machine; otherwise leave it unset
for the default input. With routing in place, **Computer audio** and **Both** use CoreAudio.
Without `MEETING_SYSTEM_DEVICE`, those choices report a setup error. This release does not
use ScreenCaptureKit for driver-free system audio. Restore your usual macOS output device
when you finish using loopback routing.

### Download and launch behavior

The default setup downloads Whisper base Q5_1, Qwen2.5 1.5B Q4_K_M, Silero VAD,
and CPU capture/inference runtimes. Speaker setup adds segmentation and embedding models.
Allow roughly **1.5 GB of downloads** and extra space for extraction and recordings;
this is an estimate, not a RAM requirement. Setup checks SHA-256 hashes before using assets.

On Windows, double-click **`start_gui.cmd`** after installation. On macOS, run
`.venv/bin/python local_app.py --open`. The app opens at
**http://127.0.0.1:8765**. Leave its terminal open. Press Ctrl+C in the terminal to
stop the app and save an active recording. Closing the browser tab leaves capture running.
Use `--port 8766` if the default port is busy.

If Python reports missing packages, install them with the same `.venv` interpreter.
If a model is missing, rerun its setup command. If capture fails, check microphone
permissions, device routing and the recording folder's `capture.log`. Setup scripts do not install drivers
or change global Python packages, PATH, firewall rules, or execution policies.

## 🧭 Using the app

1. Choose **Microphone**, **Computer audio**, or **Both**, then start recording.
2. Switch Chinese transcription and English translation independently. Turn both off
   to record audio without caption inference. Translation still needs internal transcription.
3. Pause/resume as needed; **Stop** saves the recording. Pending captions may finish later.
4. Select a saved recording to play audio, seek via timestamps, rename it, or export text.
5. Use **Generate text** to process it again. **Saved result** and **Compare with** let
   you compare model runs without replacing the original audio or earlier results.
6. Rename automatic speaker labels or correct a caption's speaker manually.

Speaker labels are estimates scoped to a recording. They do not identify people or infer
gender. Short turns and overlapping speech are difficult. Use headphones when capturing
both sources: acoustic echo cancellation is not implemented.

## 💻 Hardware and acceleration

The existing development measurements used a **Surface Laptop 7**, **Snapdragon X Elite
X1E80100 (12 cores)**, **16 GB RAM**, **Adreno X1-85**, Windows 11, ARM64 Python 3.13,
and GPU driver **31.0.133.1**. This is a tested configuration, not a minimum specification
or a guarantee for other machines. The app defaults to six inference threads.

| Platform | Capture | Inference and acceleration | Validation |
| --- | --- | --- | --- |
| **Windows ARM64** | WASAPI microphone, system loopback, or both | CPU; optional Adreno OpenCL | End-to-end testing on the development device above |
| **Windows x64** | Same WASAPI recorder with x64 runtimes | CPU baseline; automatic chooses CPU | Runtime startup smoke tests under x64 emulation; native x64 audio validation pending |
| **macOS Apple Silicon** | CoreAudio microphone; system audio via configured loopback input | CPU and Metal, with CPU fallback | Adapter/unit tests locally; native recording and inference validation pending |
| **macOS Intel** | Same CoreAudio and loopback adapter | CPU and Metal if supported by the runtime/device | Platform selection tests locally; native Intel Mac validation pending |
| **Linux / 32-bit Python** | No packaged capture/setup | Not implemented | Model-free tests can run on Linux |

**CPU** disables model GPU offload. **Automatic** tries the installed accelerator and falls
back to CPU on initialization or transport failure. **GPU · strict** requires verified GPU
use; it reports an error when unsupported, including Windows x64. Mac setup includes Metal;
no `--acceleration` switch is needed there.

Install optional Adreno OpenCL runtimes on Windows ARM64 with:

```powershell
.\.venv\Scripts\python.exe setup_models.py --acceleration
```

The UI records actual devices and fallback reasons. Qwen OpenCL worked on the development
Surface; Whisper OpenCL failed to load with its installed driver and fell back to CPU.
Vulkan is excluded because an earlier translation probe returned unusable output. NVIDIA CUDA,
AMD GPU and Hexagon NPU backends are not configured by this setup. Hardware presence alone
does not guarantee acceleration. Assets are pinned to
[whisper.cpp b5454](https://github.com/ggml-org/whisper.cpp/releases/tag/b5454) and
[llama.cpp b11946](https://github.com/ggml-org/llama.cpp/releases/tag/b11946).

For low memory use, begin with base and direct Whisper translation, disable speaker labeling,
and generate captions after recording if live processing falls behind. Direct Whisper
translation avoids loading a second language model. Windows memory reports use process peak
working set; macOS reports the highest RSS observed when the measurement method is called,
which can miss transient peaks. GPU memory is not separately measured.

## 📦 Models: download only what you need

```powershell
# Show supported IDs and exact download sizes, without downloading
.\.venv\Scripts\python.exe setup_models.py --list

# Default speech + text translation
.\.venv\Scripts\python.exe setup_models.py

# Smaller speech model; choose Whisper direct translation in the UI
.\.venv\Scripts\python.exe setup_models.py --models tiny

# Add alternatives later
.\.venv\Scripts\python.exe setup_models.py --models small qwen3-0.6b

# Lower setup buffering or concurrency
.\.venv\Scripts\python.exe setup_models.py --workers 1
```

On macOS, replace `.\.venv\Scripts\python.exe` in model/CLI commands with `.venv/bin/python`.
`setup_models.py --runtimes-only` installs/builds runtimes without downloading weights.

`--models` replaces the default model selection; capture runtimes and VAD are always
installed. Existing files with matching hashes are reused. Large model downloads resume
from `.part` files; a checksum failure resets the partial download. Other archives restart
on retry. Rerunning setup also repairs extracted runtimes. Speaker downloads restart if interrupted.

| Speech model ID | Approximate download | Intended use |
| --- | ---: | --- |
| `tiny` | 31 MiB | Lowest resource use; evaluate recognition carefully |
| `base` | 57 MiB | Default starting point |
| `base-f16` | 141 MiB | Compare full precision against quantized base |
| `small` | 181 MiB | Larger recognition model |
| `medium` | 514 MiB | Higher resource use; can fall behind live audio |
| `turbo` | 547 MiB | Large-v3 turbo; requires a Qwen translator |

| Translation model ID | Approximate download | Intended use |
| --- | ---: | --- |
| `qwen25-1.5b` | 1,066 MiB | Default Chinese-to-English text translation |
| `qwen3-0.6b` | 610 MiB | Experimental smaller alternative; may omit details |
| `qwen3-1.7b` | 1,749 MiB | Larger comparison option |
| `whisper` | No extra weights | Select in the UI; direct audio translation with a non-turbo Whisper model |

File sizes are **disk downloads**, not total working memory. Larger models need additional
runtime memory. Model IDs, upstream repositories, sizes and hashes live in
[`model_catalog.py`](model_catalog.py); runtime URLs and hashes live in
[`setup_models.py`](setup_models.py). See [`THIRD_PARTY.md`](THIRD_PARTY.md) for licenses.

## ⌨️ Command line

```powershell
.\.venv\Scripts\python.exe -X utf8 meeting.py devices
.\.venv\Scripts\python.exe -X utf8 meeting.py record --source mic
.\.venv\Scripts\python.exe -X utf8 meeting.py record --source both --seconds 600
.\.venv\Scripts\python.exe -X utf8 meeting.py record --translation-backend whisper
.\.venv\Scripts\python.exe -X utf8 meeting.py process path\to\meeting.wav
```

The CLI currently offers base/small speech selection. Imports require PCM16 WAV;
MP3/MP4 import is not implemented. Mono/stereo and other sample rates are accepted by
the CLI converter. CLI import/benchmark currently loads the source WAV into memory;
use saved-recording processing in the browser for block-based processing of long recordings.
Press Ctrl+C once to finalize CLI recording and drain pending captions.

## 🔒 Local data

`sessions/<recording>/` contains original WAV tracks, a mixed track, transcripts,
metadata, diagnostics and saved result versions. Browser captions use `ui-captions.json`,
speaker names use `speakers.json`, and model runs live in `runs/`. CLI output also includes
SRT and JSONL. Session folder timestamps currently use UTC+8.

Recordings are ordinary local files, without application-level encryption. The app binds
to loopback and checks request origins; keep it local. Models, runtimes, downloads,
recordings, generated WAV fixtures and reports are ignored by Git. Review files before sharing.

You can delete `downloads/` after setup to reclaim archives. Deleting `models/` or
`runtime/` requires rerunning the setup commands. Delete `.venv/` and `node_modules/`
only when you want to recreate development dependencies. Keep `sessions/` if you want your recordings.

## 🛠️ Development and contributions

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for architecture, tests, formatting and pull requests.
The browser uses plain HTML, CSS and JavaScript; edit source files directly without bundling
or minification. Python modules are kept at the root so the setup and launch commands stay simple.

```powershell
# Tests: no model downloads, real audio devices or network required
.\.venv\Scripts\python.exe -m unittest discover -v

# Optional browser DOM tests (Node.js 22+)
npm ci
npm test
```

## 🌱 Where help is welcome

- **Quality:** evaluate representative Chinese meetings against human-checked transcripts
  and translations; report names, numbers, negation and overlap errors.
- **Long meetings:** measure 30–60 minute stability, peak memory, heat, battery use,
  device disconnections and sleep/resume behavior.
- **Latency:** improve utterance boundaries, bounded backlog handling and incremental
  captions. Current captions finalize after silence or an approximately eight-second window.
- **Efficiency:** stream CLI imports, avoid repeatedly scanning transcript history, and
  add incremental transcript/library APIs for large collections.
- **Hardware:** validate native x64, Intel Mac and Apple Silicon capture/inference; add
  Linux capture, Windows x64 GPU backends, or a measured NPU backend.
- **Audio:** add acoustic echo cancellation, per-application capture and explicit device selection.

Whisper and Qwen can invent or mistranslate content. Current short synthetic checks establish
functionality, not meeting accuracy or guaranteed real-time performance.

## 📄 License

Project source is **MIT licensed**. Downloaded models, native runtimes and dependencies
retain their own licenses; see [`THIRD_PARTY.md`](THIRD_PARTY.md).
