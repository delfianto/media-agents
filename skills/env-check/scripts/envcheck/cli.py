import argparse
import sys

from .checks import all_checks
from .report import exit_code, format_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="envcheck",
        description=(
            "Audit this machine for the hardware/software prerequisites this repo's "
            "skills need, and print install hints for anything missing. Read-only -- "
            "never installs or changes anything itself."
        ),
    )
    parser.add_argument(
        "--category",
        help="Only show checks in this category (e.g. av1-transcode, media-organizer)",
    )
    parser.add_argument(
        "--required-only",
        action="store_true",
        help="Only show required checks, hiding optional/nice-to-have ones",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    results = all_checks()
    if args.category:
        results = [r for r in results if r.category == args.category]
    if args.required_only:
        results = [r for r in results if r.required]
    print(format_report(results))
    sys.exit(exit_code(results))


if __name__ == "__main__":
    main()
