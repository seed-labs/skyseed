#!/usr/bin/env python3
# encoding: utf-8

"""Bootstrap imports for supported runnable apps moved out of the satellite root."""

from __future__ import annotations

import sys
from pathlib import Path


SEED_EMULATOR_DEPENDENCY_DIR = Path("/home/seed/seed-emulator-upstream-dev")


def _prepend_import_path(path: Path) -> None:
    rendered = str(path)
    if rendered in sys.path:
        sys.path.remove(rendered)
    sys.path.insert(0, rendered)


def bootstrap_app_import_path() -> Path:
    apps_dir = Path(__file__).resolve().parent
    satellite_dir = apps_dir.parent
    if SEED_EMULATOR_DEPENDENCY_DIR.exists():
        _prepend_import_path(SEED_EMULATOR_DEPENDENCY_DIR)
    _prepend_import_path(satellite_dir)
    return satellite_dir
