"""Explicit system-audio recording smoke check for parent-process shutdown."""

import json
from pathlib import Path
import subprocess
import sys
import time
import wave

from capture import capture_command
from engine import ROOT
from meeting import session_path


def main():
    output = session_path("parent-exit-check")
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with (output / "capture.log").open("wb") as log:
        child = subprocess.Popen(
            capture_command(output, "system", parent_id=parent.pid),
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 20
            while not (output / "capture.ready").exists():
                if child.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"Capture did not start; see {output}")
                time.sleep(0.1)
            time.sleep(1)
            parent.terminate()
            parent.wait(timeout=5)
            child.wait(timeout=10)
            if child.returncode:
                raise RuntimeError("Capture failed after parent shutdown")
            with wave.open(str(output / "system.wav")) as audio:
                duration = audio.getnframes() / audio.getframerate()
            result = {
                "passed": duration > 0.5,
                "duration_seconds": duration,
                "session": str(output),
            }
            (ROOT / "reports").mkdir(exist_ok=True)
            (ROOT / "reports/capture-parent-exit.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
            print(json.dumps(result, indent=2))
            if not result["passed"]:
                raise RuntimeError("No usable recording saved")
        finally:
            (output / "capture.stop").touch()
            if parent.poll() is None:
                parent.terminate()
                parent.wait()
            if child.poll() is None:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    child.wait()


if __name__ == "__main__":
    main()
