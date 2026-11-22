"""Double-click launcher for the GUI (no console window)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mathshuttle.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
