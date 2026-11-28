"""Per-user storage for downloaded programs."""
from __future__ import annotations

import os
import sys


def local_data_dir() -> str:
    """The MathShuttle data folder on this computer."""
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME", "")
        if not os.path.isabs(base):
            base = os.path.join(home, ".local", "share")
    return os.path.join(base, "MathShuttle")
