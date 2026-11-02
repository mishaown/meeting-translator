Desktop preview: a dedicated application window, with no Python installation required.

- Windows x64 portable app: extract the complete ZIP, then run `MeetingTranslator.exe`.
  Windows 11 ARM64 can use this x64 app through Windows emulation. Native ARM64 inference
  remains available through the source/browser app; there is no native ARM64 desktop bundle yet.
- macOS Apple Silicon and Intel: extract the matching ZIP and open `MeetingTranslator.app`.
- Python, speaker-label dependencies and CPU inference/capture runtimes are bundled.
  The Mac runtimes also include Metal. Models are not included.
- Use **Download models** on first launch. The lightweight profile downloads Whisper tiny
  and uses direct audio translation. The balanced profile downloads base and Qwen2.5.
- Recordings and downloaded models live in a writable user data folder, separately from
  the installed application. Closing the desktop window stops the app and saves active audio.

These preview apps are unsigned and Mac builds are not notarized. Windows needs the
Microsoft Edge WebView2 Runtime (normally present on Windows 11). Mac system audio requires
separately configured loopback routing; microphone recording uses CoreAudio.

Build automation checks unit tests, bundled imports, local HTTP resources and native server
startup. Those checks do not validate microphone permissions, long meetings or translation
accuracy on every device. See README.md for setup, hardware details and limitations.
