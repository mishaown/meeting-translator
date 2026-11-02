"""Build and smoke-check a model-free desktop bundle for the current native platform."""

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from platform_support import target_platform


def main():
    root = Path(__file__).resolve().parent
    target = target_platform()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            str(root / "desktop.spec"),
        ],
        cwd=root,
        check=True,
    )
    executable = root / (
        "dist/MeetingTranslator/MeetingTranslator.exe"
        if target.startswith("windows")
        else "dist/MeetingTranslator.app/Contents/MacOS/MeetingTranslator"
    )
    with tempfile.TemporaryDirectory() as directory:
        try:
            subprocess.run(
                [str(executable), "--self-test", "--data-dir", directory],
                check=True,
                timeout=120,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            for path in (
                Path(directory) / "startup-error.log",
                Path(directory) / "logs/desktop.log",
            ):
                if path.is_file():
                    print(
                        path.read_text(encoding="utf-8", errors="replace"), flush=True
                    )
            raise
        assert (
            Path(directory) / "logs/self-test.json"
        ).is_file(), "Frozen self-test did not run"
    archive = root / "dist" / f"meeting-translator-{target}.zip"
    if target.startswith("macos"):
        subprocess.run(
            [
                "ditto",
                "-c",
                "-k",
                "--sequesterRsrc",
                "--keepParent",
                str(root / "dist/MeetingTranslator.app"),
                str(archive),
            ],
            check=True,
        )
    else:
        with zipfile.ZipFile(
            archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as output:
            for path in (root / "dist/MeetingTranslator").rglob("*"):
                if path.is_file():
                    output.write(path, path.relative_to(root / "dist"))
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(
        f"{checksum}  {archive.name}\n", encoding="ascii"
    )
    print(f"Desktop release archive: {archive}")


if __name__ == "__main__":
    main()
