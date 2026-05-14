#!/usr/bin/env python3
"""Compatibility wrapper for ``python packages/studio/embed.py``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from constellation_studio.embed import main

if __name__ == "__main__":
    raise SystemExit(main())
