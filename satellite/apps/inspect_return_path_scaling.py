#!/usr/bin/env python3
# encoding: utf-8

"""Inspect G/Z return-path scaling semantics from planner preview artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()

from return_path_audit import (  # noqa: E402
    audit_planner_preview_path,
    render_return_path_audit_json,
    render_return_path_audit_summary,
)


def _planner_preview_path(input_path: Path) -> Path:
    path = input_path.expanduser().resolve()
    if path.is_dir():
        return path / "planner_preview.json"
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit whether current GW->UT return-path state still scales with "
            "per-UT/per-flow compatibility state instead of shared zone return reachability."
        ),
    )
    parser.add_argument(
        "path",
        help="Run directory or planner_preview.json path.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    parser.add_argument(
        "--write-report",
        default=None,
        help="Optional path to write the full JSON audit report.",
    )
    parser.add_argument(
        "--selected-slice-id",
        action="append",
        default=[],
        help="Optional runtime slice id to summarize as selected runtime scope; repeatable.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = audit_planner_preview_path(
        _planner_preview_path(Path(args.path)),
        selected_scope_ids=list(args.selected_slice_id),
    )
    if args.write_report:
        output_path = Path(args.write_report).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(render_return_path_audit_json(report), encoding="utf-8")
    if args.format == "json":
        print(render_return_path_audit_json(report), end="")
    else:
        print(render_return_path_audit_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
