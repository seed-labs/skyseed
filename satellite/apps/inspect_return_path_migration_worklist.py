#!/usr/bin/env python3
# encoding: utf-8

"""Combine return-path blocker counts with Linux install-point ownership."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Sequence

from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()

from return_path_install_audit import (  # noqa: E402
    render_return_path_migration_worklist_json,
    render_return_path_migration_worklist_summary,
    summarize_return_path_migration_worklist,
)


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _return_path_audit_path(input_path: Path) -> Path:
    path = input_path.expanduser().resolve()
    if path.is_dir():
        return path / "return_path_audit.json"
    return path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a migration worklist by combining return_path_audit.json item "
            "counts with the Linux runtime install points that currently realize "
            "those blockers."
        ),
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Run directory or return_path_audit.json path.",
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
        help="Optional path to write the full JSON worklist.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = summarize_return_path_migration_worklist(
        _load_json(_return_path_audit_path(args.path))
    )
    rendered_json = render_return_path_migration_worklist_json(report)
    if args.write_report:
        path = args.write_report.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered_json, encoding="utf-8")
    if args.format == "json":
        print(rendered_json, end="")
    else:
        print(render_return_path_migration_worklist_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
