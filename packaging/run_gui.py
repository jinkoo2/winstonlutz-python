"""PyInstaller entry: GUI with no args; CLI subcommands otherwise (e.g. ``watch``)."""

import sys

from winstonlutz.cli import main
from winstonlutz.gui import run_app

if __name__ == "__main__":
    if len(sys.argv) <= 1:
        raise SystemExit(run_app())
    raise SystemExit(main())
