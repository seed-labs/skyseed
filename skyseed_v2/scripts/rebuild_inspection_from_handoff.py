#!/usr/bin/env python3
"""Regenerate one Inspection Tool from a preserved detached handoff."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools._detached_inspection import rebuild_from_handoff


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handoff", type=Path)
    arguments = parser.parse_args()
    result = rebuild_from_handoff(arguments.handoff)
    print(f"{arguments.handoff}: {result['status']}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
