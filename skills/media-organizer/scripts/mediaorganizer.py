#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = ["guessit"]
# ///
"""Entrypoint: uv run mediaorganizer.py organize ...
(or: pip install -r requirements.txt && python3 mediaorganizer.py organize ...)

requires-python is pinned to >=3.14, not just "whatever guessit needs" --
this whole repo's pyproject.toml targets py314 for ruff, and `ruff format`
rewrites multi-exception `except` clauses into Python 3.14's bare-comma
grammar (`except A, B:` instead of `except (A, B):`), which is a hard
SyntaxError on anything earlier. Confirmed the hard way: `uv run` against an
under-pinned requires-python silently provisioned 3.13 and the whole package
failed to import.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mediaorganizer.cli import main

if __name__ == "__main__":
    main()
