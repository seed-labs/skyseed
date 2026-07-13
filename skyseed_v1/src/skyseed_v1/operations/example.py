"""User-facing lifecycle actions for one resolved example configuration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from skyseed_v1.configuration.models import ExperimentConfig


def execute(
    config: ExperimentConfig,
    action: str,
    *,
    run_root: Path | None = None,
    run_id: str | None = None,
    output_format: str = "text",
    ut_id: str | None = None,
    ping_count: int = 3,
    wait_seconds: float = 5.0,
    watch: bool = False,
    watch_interval: float = 5.0,
) -> int:
    if action == "inspect":
        payload = config.summary()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if not payload["validation_issues"] else 2
    if action == "preflight":
        from skyseed_v1.operations.preflight import (
            render_preflight_json,
            render_preflight_summary,
            run_skyseed_preflight,
        )

        report = run_skyseed_preflight(profile="operational-clean-bpf-owned")
        if output_format == "json":
            print(render_preflight_json(report), end="")
        else:
            print(render_preflight_summary(report))
        return 0 if report["assessment"]["ready"] is True else 1
    if action == "clean":
        from skyseed_v1.operations.preflight import (
            cleanup_skyseed_environment_residue,
            render_preflight_json,
            render_preflight_summary,
            run_skyseed_cleanup_preflight,
        )

        before = run_skyseed_cleanup_preflight(
            profile="operational-clean-bpf-owned"
        )
        active_processes = list(
            dict(before.get("checks") or {})
            .get("compose_processes", {})
            .get("processes", [])
        )
        if active_processes:
            blocked = {
                **before,
                "clean_blocked": True,
                "clean_blocking_reason": "active_skyseed_processes_present",
            }
            if output_format == "json":
                print(render_preflight_json(blocked), end="")
            else:
                print("Skyseed clean")
                print("  applied=False")
                print("  success=False")
                print("  blocked=active_skyseed_processes_present")
                print(f"  active_processes={len(active_processes)}")
            return 2
        cleanup_result = cleanup_skyseed_environment_residue(
            preflight_report=before,
            apply=True,
        )
        after = run_skyseed_cleanup_preflight(
            profile="operational-clean-bpf-owned"
        )
        report = {
            **after,
            "pre_cleanup_preflight": before,
            "cleanup_result": cleanup_result,
        }
        if output_format == "json":
            print(render_preflight_json(report), end="")
        else:
            print(render_preflight_summary(report))
        return 0 if (
            cleanup_result.get("success") is True
            and after.get("assessment", {}).get("ready") is True
        ) else 1
    if action == "demo-ping":
        from skyseed_v1.operations.demo_ping import run_demo_ping

        try:
            return run_demo_ping(
                config,
                ut_id=ut_id,
                count=ping_count,
                wait_seconds=wait_seconds,
                watch=watch,
                watch_interval=watch_interval,
            )
        except KeyboardInterrupt:
            print("\n[demo-ping] stopped by user")
            return 130

    from skyseed_v1.runtime.profile import main as run_profile

    arguments = [
        "--profile",
        config.profile_name,
        "--run-root",
        str(run_root or config.run_root),
        "--format",
        "text",
        "--progress",
        "off" if action == "preview" else "plain",
    ]
    if run_id:
        arguments.extend(["--run-id", run_id])
    if action == "preview":
        arguments.append("--dry-run")
    return run_profile(arguments)


def main(config: ExperimentConfig, argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Run {config.example_name}.")
    parser.add_argument(
        "action",
        nargs="?",
        choices=(
            "run",
            "preflight",
            "clean",
            "preview",
            "inspect",
            "demo-ping",
        ),
        default="run",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Human summary by default; use JSON for full preflight/clean evidence.",
    )
    parser.add_argument("--ut", help="Probe one UT instead of the latest available UT.")
    parser.add_argument("--count", type=int, default=3, help="Ping packets per probe.")
    parser.add_argument(
        "--wait-seconds", type=float, default=5.0,
        help="How long demo-ping waits for its UT container to start.",
    )
    parser.add_argument("--watch", action="store_true", help="Keep probing until Ctrl-C.")
    parser.add_argument(
        "--watch-interval", type=float, default=5.0,
        help="Seconds between probes in watch mode.",
    )
    args = parser.parse_args(argv)
    return execute(
        config,
        str(args.action),
        output_format=str(args.format),
        ut_id=args.ut,
        ping_count=args.count,
        wait_seconds=args.wait_seconds,
        watch=args.watch,
        watch_interval=args.watch_interval,
    )
