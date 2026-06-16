#!/usr/bin/env python3
# encoding: utf-8

"""Inspect Linux runtime install points for GW->UT return-path migration."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()

from return_path_install_audit import (  # noqa: E402
    render_return_path_install_points_json,
    render_return_path_install_points_summary,
    summarize_return_path_install_points,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "List the current Linux runtime install points that still realize "
            "GW->UT return state as per-UT/per-flow compatibility state."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    parser.add_argument(
        "--write-report",
        type=Path,
        default=None,
        help="Optional path to write the full JSON report.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = summarize_return_path_install_points()
    rendered_json = render_return_path_install_points_json(report)
    if args.write_report:
        path = args.write_report.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered_json, encoding="utf-8")
    if args.format == "json":
        print(rendered_json, end="")
    else:
        print(render_return_path_install_points_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
