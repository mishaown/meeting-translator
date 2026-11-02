# Third-party components

These components keep their upstream licenses; this project does not relicense them.

| Component | Source | License |
| --- | --- | --- |
| Whisper model weights | https://github.com/openai/whisper | MIT |
| whisper.cpp b5454 Windows ARM64/x64 runtimes and macOS source build | https://github.com/ggml-org/whisper.cpp/releases/tag/b5454 | MIT |
| Whisper GGML quantizations | https://huggingface.co/ggerganov/whisper.cpp | MIT |
| Silero VAD GGML | https://huggingface.co/ggml-org/whisper-vad | MIT |
| NAudio Core and WASAPI 2.2.1 | https://github.com/naudio/NAudio | MIT |
| llama.cpp b11946 Windows ARM64/x64 and macOS ARM64/x64 runtimes | https://github.com/ggml-org/llama.cpp/releases/tag/b11946 | MIT |
| Qwen3 0.6B Q8_0 | https://huggingface.co/Qwen/Qwen3-0.6B-GGUF | Apache 2.0 |
| Qwen3 1.7B Q8_0 | https://huggingface.co/Qwen/Qwen3-1.7B-GGUF | Apache 2.0 |
| Qwen2.5 1.5B Instruct Q4_K_M | https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF | Apache 2.0 |
| NumPy | https://numpy.org/ | BSD 3-clause |
| pywebview (desktop window) | https://github.com/r0x0r/pywebview | BSD 3-clause |
| Python.NET (Windows desktop) | https://github.com/pythonnet/pythonnet | MIT |
| PyObjC (macOS desktop) | https://github.com/ronaldoussoren/pyobjc | MIT |
| PyInstaller (packaging/bootloader) | https://github.com/pyinstaller/pyinstaller | GPL-2.0-or-later with bootloader distribution exception |
| CPython (bundled desktop interpreter) | https://www.python.org/ | PSF License |
| python-sounddevice (macOS capture) | https://github.com/spatialaudio/python-sounddevice | MIT |
| PortAudio (included in macOS sounddevice wheels) | https://www.portaudio.com/ | MIT-style PortAudio license |
| Sherpa-ONNX 2.0.1 | https://github.com/k2-fsa/sherpa-onnx | Apache 2.0 |
| Pyannote segmentation 3.0 (Sherpa conversion) | https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models | MIT (included with model) |
| WeSpeaker Chinese CN-Celeb ResNet34 LM | https://github.com/wenet-e2e/wespeaker | Apache 2.0 |

The optional local test fixture is synthesized with the Windows-installed Microsoft Huihui voice.
Microsoft's installed software and voice components retain their own terms.

Pinned runtime URLs and checksums are recorded in `setup_models.py`; speaker assets are pinned in `setup_speakers.py`.
The ARM64 OpenCL GPU variants of whisper.cpp and llama.cpp retain the same
upstream MIT licenses. Model specifications and hashes also live in `model_catalog.py`.

macOS whisper.cpp builds enable the upstream Metal backend. BlackHole is an optional,
separately installed audio-routing driver, not bundled or downloaded by this project.
It retains its upstream GPL-3.0 license: https://github.com/ExistentialAudio/BlackHole.
