"""PyInstaller entry for WinstonLutz.service.exe (headless watch)."""

import sys

from winstonlutz.cli import main

if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        argv = ["watch", *argv]
    raise SystemExit(main(argv))
