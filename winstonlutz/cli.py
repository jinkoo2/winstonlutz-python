"""Command-line entry: analyze, watch, validate-golden."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import analyze_image
from .logutil import configure_logging
from .pipeline import run_case
from .validate import validate_sample_data
from .watcher import WatchPathUnavailable, watch


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

    p_watch = sub.add_parser("watch", help="watch a transfer folder for trigger files (C# service replacement)")
    p_watch.add_argument(
        "--watch-path",
        default="",
        help="transfer share (default: Watcher.watch_path in settings JSON)",
    )
    p_watch.add_argument(
        "--data-root",
        default="",
        help="WinstonLutz data tree (default: Watcher.data_root in settings JSON)",
    )

    p_val = sub.add_parser("validate-golden", help="compare analysis to sample_data result.txt")
    p_val.add_argument("sample_data", nargs="?", default="sample_data")
    p_val.add_argument("--tol", type=float, default=0.1)
    p_val.add_argument("--limit", type=int, default=0, help="max images (0=all)")

    p_gui = sub.add_parser("gui", help="open the Winston-Lutz viewer / analysis app")
    p_gui.add_argument("folder", nargs="?", help="optional RI folder to open")

    p_plan = sub.add_parser("plan-beams", help="list beams from an RP RT Plan DICOM")
    p_plan.add_argument("rtplan", nargs="?", help="RP.*.dcm (default: Edge sample Plan)")

    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose, console=True)

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
        from .app_settings import watcher_settings

        cfg = watcher_settings()
        watch_path = (args.watch_path or cfg.get("watch_path") or "").strip()
        data_root = (args.data_root or cfg.get("data_root") or "").strip()
        if not watch_path or not data_root:
            print(
                "watch needs --watch-path and --data-root, or Watcher.watch_path and "
                "Watcher.data_root in winstonlutz.gui.settings.json",
                file=sys.stderr,
            )
            return 2
        try:
            watch(watch_path, data_root, poll_sec=cfg.get("poll_sec"), watcher=cfg)
        except WatchPathUnavailable as exc:
            print(exc, file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Watcher stopped.", file=sys.stderr)
            return 0
        return 0

    if args.cmd == "validate-golden":
        ok = validate_sample_data(args.sample_data, tol_mm=args.tol, limit=args.limit)
        return 0 if ok else 1

    if args.cmd == "gui":
        from .gui import run_app

        return run_app(folder=args.folder)

    if args.cmd == "plan-beams":
        from .rtplan import list_plan_beams

        path = Path(args.rtplan) if args.rtplan else (
            Path(__file__).resolve().parent.parent / "sample_data" / "Edge" / "Plan" / "RP.EdgeDryRun.WL.dcm"
        )
        if not path.is_file():
            print(f"RT Plan not found: {path}", file=sys.stderr)
            return 1
        beams = list_plan_beams(path)
        print(f"{'Beam':>4}  {'Name':<16}  {'Type':<4}  {'Gantry':>6}  {'Table':>6}  {'Coll':>6}")
        for b in beams:
            print(
                f"{b.number:4d}  {b.name:<16}  {b.kind:<4}  {b.gantry:6.0f}  {b.table:6.0f}  {b.collimator:6.0f}"
            )
        return 0 if beams else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
