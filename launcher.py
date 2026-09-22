"""PyInstaller entry point (GUI only; the CLI is available via `python -m equation_tool`)."""
import sys

from equation_tool.app import main

if __name__ == "__main__":
    sys.exit(main())
