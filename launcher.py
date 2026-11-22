"""PyInstaller entry point (GUI only; the CLI is available via `python -m mathshuttle`)."""
import sys

from mathshuttle.app import main

if __name__ == "__main__":
    sys.exit(main())
