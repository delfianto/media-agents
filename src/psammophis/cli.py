"""Top-level command dispatcher for the psammophis application.

Owns only global concerns and feature dispatch. Feature packages keep their
own flags and nested subcommands; this module lazily imports the selected
handler so importing `psammophis.cli` never probes hardware or credentials.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from psammophis import __version__
from psammophis.runtime.context import AppContext
from psammophis.runtime.reporters import select_reporter

# Public command name -> (module path, callable name).
_COMMANDS: dict[str, tuple[str, str]] = {
    "analyze": ("psammophis.analyze.cli", "main"),
    "artwork": ("psammophis.artwork.cli", "main"),
    "transcode": ("psammophis.transcode.cli", "main"),
    "env-check": ("psammophis.envcheck.cli", "main"),
    "mkvedit": ("psammophis.mkvedit.cli", "main"),
    "organize": ("psammophis.organize.cli", "main"),
    "compare": ("psammophis.compare.cli", "main"),
    "runs": ("psammophis.runtime.runs_cli", "main"),
    "subtitle": ("psammophis.subtitle.cli", "main"),
    "track-strip": ("psammophis.trackstrip.cli", "main"),
}


def _load_handler(command: str) -> Callable[..., Any]:
    module_name, attr = _COMMANDS[command]
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psammophis",
        description="Self-hosted Plex media library maintenance toolkit.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"psammophis {__version__}",
    )
    parser.add_argument(
        "--reporter",
        choices=("auto", "tty", "plain", "jsonl", "quiet"),
        default="auto",
        help="Progress reporter (default: auto)",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="Minimum seconds between non-interactive progress lines (default: 10)",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Psammophis state directory for run journals",
    )
    journal = parser.add_mutually_exclusive_group()
    journal.add_argument(
        "--journal",
        action="store_true",
        default=None,
        help="Force durable run journaling on",
    )
    journal.add_argument(
        "--no-journal",
        action="store_true",
        help="Force durable run journaling off",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=sorted(_COMMANDS),
        help="Feature command to run",
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to the selected command",
    )
    return parser


def _split_globals(raw: list[str]) -> tuple[list[str], list[str]]:
    """Split global options from the feature command and its args.

    Global flags must precede the feature command. Anything after the
    command name is forwarded verbatim.
    """
    if not raw:
        return [], []
    # Use parse_known_args on a copy that stops at the first positional command.
    # argparse with REMAINDER on the full parser is awkward for mixed globals,
    # so we walk tokens manually for the small global option set.
    globals_tokens: list[str] = []
    i = 0
    while i < len(raw):
        tok = raw[i]
        # First non-global token is the command (or unknown).
        if tok in _COMMANDS or (
            not tok.startswith("-") and tok not in ("auto", "tty", "plain", "jsonl", "quiet")
        ):
            return globals_tokens, raw[i:]
        if tok in ("-h", "--help", "--version", "-V"):
            return raw, []
        if tok in ("--reporter", "--progress-interval", "--state-dir"):
            globals_tokens.append(tok)
            if i + 1 < len(raw):
                globals_tokens.append(raw[i + 1])
                i += 2
                continue
            return globals_tokens, []
        if (
            tok.startswith("--reporter=")
            or tok.startswith("--progress-interval=")
            or tok.startswith("--state-dir=")
        ):
            globals_tokens.append(tok)
            i += 1
            continue
        if tok in ("--journal", "--no-journal"):
            globals_tokens.append(tok)
            i += 1
            continue
        # Unknown global-looking flag: let the top-level parser report it.
        globals_tokens.append(tok)
        i += 1
    return globals_tokens, []


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw or raw[0] in ("-h", "--help"):
        _build_parser().print_help()
        return 0
    if raw[0] in ("--version", "-V"):
        print(f"psammophis {__version__}")
        return 0

    global_tokens, rest = _split_globals(raw)
    # If the first token is already a command, rest is the full raw list.
    if rest and rest[0] in _COMMANDS and not global_tokens:
        command = rest[0]
        feature_args = rest[1:]
        global_ns = argparse.Namespace(
            reporter="auto",
            progress_interval=10.0,
            state_dir=None,
            journal=None,
            no_journal=False,
        )
    else:
        parser = _build_parser()
        # Rebuild argv for argparse: globals + command + remainder as args
        if not rest:
            # only globals / incomplete
            try:
                parser.parse_args(global_tokens)
            except SystemExit as exc:
                code = exc.code
                return 0 if code is None else int(code) if isinstance(code, int) else 1
            parser.print_help(sys.stderr)
            return 2
        command = rest[0]
        feature_args = rest[1:]
        if command not in _COMMANDS:
            parser.print_help(sys.stderr)
            print(f"\nerror: unknown command {command!r}", file=sys.stderr)
            return 2
        # Parse globals only (inject command so positional is satisfied)
        try:
            global_ns = parser.parse_args([*global_tokens, command])
        except SystemExit as exc:
            code = exc.code
            return 0 if code is None else int(code) if isinstance(code, int) else 1

    if feature_args[:1] == ["--"]:
        feature_args = feature_args[1:]

    journal: bool | None
    if getattr(global_ns, "no_journal", False):
        journal = False
    elif getattr(global_ns, "journal", None):
        journal = True
    else:
        journal = None

    context = AppContext(
        reporter=getattr(global_ns, "reporter", "auto"),
        progress_interval=float(getattr(global_ns, "progress_interval", 10.0)),
        state_dir=getattr(global_ns, "state_dir", None),
        journal=journal,
        command=command,
    )
    # Reporter is constructed here; features may replace the composite sink.
    context.sink = select_reporter(context.reporter, progress_interval=context.progress_interval)
    context.attach_emitter(command)

    handler = _load_handler(command)
    try:
        # Prefer context-aware handlers; fall back to argv-only for gradual migration.
        try:
            result = handler(feature_args, context)  # type: ignore[misc]
        except TypeError:
            result = handler(feature_args)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    if result is None:
        return 0
    if isinstance(result, int):
        return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
