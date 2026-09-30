"""PyInstaller entry: GUI by default; ``service`` / ``watch`` for the watcher."""

from winstonlutz.logutil import ensure_stdio

ensure_stdio()

from winstonlutz.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
