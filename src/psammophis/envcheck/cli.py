import argparse

from psammophis.runtime.context import AppContext
from psammophis.runtime.events import ItemCompleted, ItemStarted

from .checks import all_checks
from .report import exit_code, format_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psammophis env-check",
        description=(
            "Audit this machine for the hardware/software prerequisites this repo's "
            "skills need, and print install hints for anything missing. Read-only -- "
            "never installs or changes anything itself."
        ),
    )
    parser.add_argument(
        "--category",
        help="Only show checks in this category (e.g. transcode, organize, mkvedit)",
    )
    parser.add_argument(
        "--required-only",
        action="store_true",
        help="Only show required checks, hiding optional/nice-to-have ones",
    )
    return parser


def main(argv: list[str] | None = None, context: AppContext | None = None) -> int:
    args = build_parser().parse_args(argv)
    emitter = (
        context.start_run(command="env-check", mode="read-only") if context is not None else None
    )
    results = all_checks()
    if args.category:
        results = [r for r in results if r.category == args.category]
    if args.required_only:
        results = [r for r in results if r.required]
    if emitter is not None:
        for index, result in enumerate(results, start=1):
            item = f"{result.category}/{result.name}"
            emitter.emit(ItemStarted, item=item, index=index, total=len(results))
            emitter.emit(
                ItemCompleted,
                item=item,
                status="succeeded" if result.found else "failed" if result.required else "skipped",
                detail=result.detail,
            )
    print(format_report(results))
    code = exit_code(results)
    if context is not None:
        errors = sum(1 for result in results if result.required and not result.found)
        context.record_outcome(errors=errors, status="failed" if code else "succeeded")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
