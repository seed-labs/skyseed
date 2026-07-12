#!/usr/bin/env python3
"""Run Skyseed v1's cheap test gate directly."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TEST_ROOT.parent
SOURCE_ROOT = PROJECT_ROOT / "src"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tier",
        nargs="?",
        choices=("all", "unit", "contracts", "integration"),
        default="all",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, str(SOURCE_ROOT))
    start_dir = TEST_ROOT if args.tier == "all" else TEST_ROOT / args.tier
    suite = unittest.defaultTestLoader.discover(
        str(start_dir),
        pattern="test_*.py",
        top_level_dir=str(TEST_ROOT),
    )
    result = unittest.TextTestRunner(verbosity=1 if args.quiet else 2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
