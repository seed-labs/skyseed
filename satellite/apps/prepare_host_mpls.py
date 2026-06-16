#!/usr/bin/env python3
# encoding: utf-8

"""Public app boundary for host MPLS readiness and preparation."""

from __future__ import annotations

import argparse
import sys

from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()

from mpls_host import (  # noqa: E402
    HostMplsPreparationError,
    ensure_host_mpls_ready,
    probe_host_mpls_readiness,
    render_host_mpls_json,
    render_host_mpls_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify and, if needed, prepare host MPLS support for real runtime use.",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Only probe readiness; do not prepare the host automatically.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    return parser.parse_args()


def _emit(payload: dict, *, output_format: str) -> None:
    if output_format == "json":
        print(render_host_mpls_json(payload))
        return
    print(render_host_mpls_summary(payload))


def main() -> int:
    args = parse_args()
    try:
        if args.probe_only:
            readiness = probe_host_mpls_readiness()
            _emit(
                {
                    "prepared": False,
                    "prepared_now": False,
                    "command_results": [],
                    "readiness": readiness,
                    "wall_seconds": 0.0,
                },
                output_format=args.format,
            )
            return 0 if readiness["assessment"]["ready"] is True else 1

        summary = ensure_host_mpls_ready(auto_prepare=True)
        _emit(summary, output_format=args.format)
        return 0
    except HostMplsPreparationError as exc:
        report = dict(exc.report)
        report["error"] = str(exc)
        if args.format == "json":
            print(render_host_mpls_json(report))
        else:
            print(str(exc), file=sys.stderr)
            print(render_host_mpls_summary(report))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
