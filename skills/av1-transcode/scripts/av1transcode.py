#!/usr/bin/env python3
"""Entrypoint: python3 av1transcode.py <probe|list-presets|run|purge-backups> ..."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from av1transcode.cli import main

if __name__ == "__main__":
    main()
