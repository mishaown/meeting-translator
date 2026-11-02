"""Keep bundled read-only resources separate from writable user data."""

import os
from pathlib import Path
import sys

ASSET_ROOT = Path(__file__).resolve().parent


def data_directory():
    if override := os.environ.get("MEETING_DATA_DIR"):
        return Path(override).expanduser().resolve()
    if not getattr(sys, "frozen", False):
        return ASSET_ROOT
    if sys.platform == "win32":
        return (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
            / "MeetingTranslator"
        )
    return Path.home() / "Library/Application Support/MeetingTranslator"


DATA_ROOT = data_directory()
