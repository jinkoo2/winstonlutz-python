"""Command-line entry: analyze, watch, validate-golden."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .analysis import analyze_image
from .pipeline import run_case
from .validate import validate_sample_data
from .watcher import watch


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="winstonlutz")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_one = sub.add_parser("analyze-image", help="analyze one RI DICOM")
    p_one.add_argument("dcm")
    p_one.add_argument("out_dir", nargs="?")
    p_one.add_argument("--field-search", default="field_search_yes")
    p_one.add_argument("--bb-search", default="bb_search_ConnectedComponent")
    p_one.add_argument("--match", default="")
    p_one.add_argument("--preprocess", action="store_true")

    p_case = sub.add_parser("analyze", help="analyze one case folder")
    p_case.add_argument("case_dir")
    p_case.add_argument("--data-root")
    p_case.add_argument("--machine")
    p_case.add_argument("--email", action="store_true")
    p_case.add_argument("--preprocess", action="store_true")

    p_watch = sub.add_parser("watch", help="watch a transfer folder for RE.*.dcm")
    p_watch.add_argument("--watch-path", required=True)
    p_watch.add_argument("--data-root", required=True)

    p_val = sub.add_parser("validate-golden", help="compare analysis to sample_data result.txt")
    p_val.add_argument("sample_data", nargs="?", default="sample_data")
    p_val.add_argument("--tol", type=float, default=0.1)
    p_val.add_argument("--limit", type=int, default=0, help="max images (0=all)")

    p_gui = sub.add_parser("gui", help="open the Winston-Lutz viewer / analysis app")
    p_gui.add_argument("folder", nargs="?", help="optional RI folder to open")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.cmd == "analyze-image":
        result = analyze_image(
            args.dcm,
            args.out_dir,
            field_search=args.field_search,
            bb_search=args.bb_search,
            match_criteria=args.match,
            preprocess=args.preprocess,
        )
        print(f"field center={result.field_center}")
        print(f"bb_cetner={result.bb_center}")
        print(f"bb offset={result.bb_offset}")
        return 0

    if args.cmd == "analyze":
        items = run_case(
            args.case_dir,
            data_root=args.data_root,
            machine=args.machine,
            send_email=args.email,
            preprocess=args.preprocess,
        )
        print(f"analyzed {len(items)} images")
        return 0 if items else 1

    if args.cmd == "watch":
        watch(args.watch_path, args.data_root)
        return 0

    if args.cmd == "validate-golden":
        ok = validate_sample_data(args.sample_data, tol_mm=args.tol, limit=args.limit)
        return 0 if ok else 1

    if args.cmd == "gui":
        from .gui import run_app

        return run_app(folder=args.folder)

    return 2


if __name__ == "__main__":
    sys.exit(main())
