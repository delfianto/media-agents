#!/usr/bin/env python3
"""Entrypoint: python3 .mediatools/mediatools.py <scan|stats|plan|apply|purge-backups> ..."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mediatools.cli import main

if __name__ == "__main__":
    main()
