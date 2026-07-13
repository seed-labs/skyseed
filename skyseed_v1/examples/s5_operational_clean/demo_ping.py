#!/usr/bin/env python3
"""Demonstrate this running example's fixed UT-to-gateway policies."""

from __future__ import annotations

import sys

from skyseed_v1.operations.paths import prepare_user_entry_environment

prepare_user_entry_environment()

from experiment import EXPERIMENT
from skyseed_v1.operations.example import main


if __name__ == "__main__":
    raise SystemExit(main(EXPERIMENT, ["demo-ping", *sys.argv[1:]]))
