#!/usr/bin/env python3
"""Thin executable entry for the underlying experiment orchestrator."""

from __future__ import annotations

from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    from skyseed_v1.runtime.experiment import main as run_experiment

    return run_experiment(argv)


if __name__ == "__main__":
    raise SystemExit(main())
