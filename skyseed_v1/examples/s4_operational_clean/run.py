#!/usr/bin/env python3
"""Run this example directly; use preflight/preview for cheap checks."""

from __future__ import annotations

from skyseed_v1.operations.paths import prepare_user_entry_environment

prepare_user_entry_environment()

from experiment import EXPERIMENT
from skyseed_v1.operations.example import main


if __name__ == "__main__":
    raise SystemExit(main(EXPERIMENT))
