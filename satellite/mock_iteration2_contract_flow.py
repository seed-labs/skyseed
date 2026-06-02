#!/usr/bin/env python3
# encoding: utf-8

"""Run a minimal Iteration 2 contract skeleton flow with mock data."""

from __future__ import annotations

import json

from planner import build_mock_planner_flow


def main() -> None:
    print(json.dumps(build_mock_planner_flow(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
