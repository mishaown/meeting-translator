"""Native desktop window and frozen capture-worker entry point."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import traceback
import urllib.request


def main():
    if sys.argv[1:2] == ["--capture-worker"]:
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        if getattr(sys, "frozen", False) and "--output" in sys.argv:
            output = Path(sys.argv[sys.argv.index("--output") + 1])
            sys.stdout = sys.stderr = (output / "capture.log").open(
                "a", encoding="utf-8", buffering=1
            )
        from capture import main as capture_main

        try:
            capture_main()
        except Exception:
            traceback.print_exc()
            raise
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, help="Store models and recordings in this directory"
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Check packaged resources and native runtime startup without recording",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Also load the interface in a hidden native window",
    )
    args = parser.parse_args()
    if args.data_dir:
        os.environ["MEETING_DATA_DIR"] = str(args.data_dir.expanduser().resolve())
    from app_paths import ASSET_ROOT, DATA_ROOT

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    os.environ["MEETING_DATA_DIR"] = str(DATA_ROOT)
    logs = DATA_ROOT / "logs"
    logs.mkdir(exist_ok=True)
    if getattr(sys, "frozen", False):
        sys.stdout = sys.stderr = (logs / "desktop.log").open(
            "a", encoding="utf-8", buffering=1
        )

    from local_app import LocalServer

    server = LocalServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    result = {"ok": False, "window_loaded": False}
    watchdog = None
    try:
        from platform_support import target_platform

        native_arm = sys.platform == "win32" and target_platform() == "windows-arm64"
        if sys.platform == "win32" and not native_arm:
            import webview.platforms.winforms
        if args.self_test or args.smoke_test:
            import sherpa_onnx  # Ensure the packaged diarization extension and its DLLs load.
            from inference_runtime import candidates
            from platform_support import target_platform

            if target_platform().startswith("macos"):
                import sounddevice  # Ensure PortAudio is present, without opening a device.
            for name in ("index.html", "app.js", "setup.js", "style.css"):
                assert (ASSET_ROOT / "web" / name).is_file(), name
            assert (ASSET_ROOT / "Capture.cs").is_file()
            for task in ("transcription", "translation"):
                executable, _ = candidates(task, "cpu")[0]
                if native_arm:
                    from platform_support import require_arm64_binary

                    require_arm64_binary(executable)
                subprocess.run(
                    [str(executable), "--help"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=30,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with http.open(url + "/api/health", timeout=5) as response:
                assert json.load(response)["app"] == "meeting-desk"
            if native_arm:
                import ctypes
                from platform_support import require_arm64_binary

                for binary in (
                    Path(sys.executable),
                    ASSET_ROOT / "runtime/windows-host/MeetingTranslatorHost.exe",
                    ASSET_ROOT / "runtime/windows-host/WebView2Loader.dll",
                ):
                    require_arm64_binary(binary)
                ctypes.WinDLL(
                    str(ASSET_ROOT / "runtime/windows-host/WebView2Loader.dll")
                )
                result["architecture"] = "windows-arm64"
            result["ok"] = True
            if args.self_test and not args.smoke_test:
                return 0

        if native_arm:
            host = ASSET_ROOT / "runtime/windows-host/MeetingTranslatorHost.exe"
            if not host.is_file():
                raise RuntimeError(
                    "Build the native window host with setup_windows_host.py first."
                )
            command = [
                str(host),
                url,
                str(DATA_ROOT / "webview-arm64"),
                str(os.getpid()),
            ]
            if args.smoke_test:
                command.append("--smoke-test")
            with (logs / "native-window.log").open("wb") as output:
                process = subprocess.run(
                    command,
                    stdout=output,
                    stderr=output,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            result["window_loaded"] = process.returncode == 0
            if process.returncode != 0:
                raise RuntimeError(
                    f"Native ARM64 window exited with code {process.returncode}. See {logs / 'native-window.log'}."
                )
            return 0

        import webview

        webview.settings["ALLOW_DOWNLOADS"] = True
        window = webview.create_window(
            "Meeting Translator",
            url,
            width=1280,
            height=850,
            min_size=(900, 650),
            hidden=args.smoke_test,
        )
        if args.smoke_test:

            def check_window():
                try:
                    result["window_loaded"] = bool(
                        window.evaluate_js(
                            "!!document.getElementById('start') && !!document.getElementById('setup-open')"
                        )
                    )
                finally:
                    window.destroy()

            window.events.loaded += check_window
            watchdog = threading.Timer(40, window.destroy)
            watchdog.daemon = True
            watchdog.start()
        webview.start(
            gui="edgechromium" if sys.platform == "win32" else "cocoa",
            storage_path=str(DATA_ROOT / "webview"),
        )
        return 0 if not args.smoke_test or result["window_loaded"] else 1
    finally:
        if watchdog:
            watchdog.cancel()
        server.app.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if args.self_test or args.smoke_test:
            (logs / "self-test.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        from app_paths import DATA_ROOT

        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        path = DATA_ROOT / "startup-error.log"
        path.write_text(traceback.format_exc(), encoding="utf-8")
        if (
            sys.platform == "win32"
            and "--self-test" not in sys.argv
            and "--smoke-test" not in sys.argv
            and "--capture-worker" not in sys.argv
        ):
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                f"{error}\n\nDetails: {path}",
                "Meeting Translator could not start",
                0x10,
            )
        raise SystemExit(1)
