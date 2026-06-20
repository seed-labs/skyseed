#!/usr/bin/env python3
# encoding: utf-8

"""Microbench isolated Linux control-plane operations for Skyseed feasibility."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any, Callable


PREFIX = "skyseed_cp"


def sudo_available() -> bool:
    return subprocess.run(
        ["sudo", "-n", "true"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).returncode == 0


class Runner:
    def __init__(self, *, use_sudo: bool) -> None:
        self.use_sudo = use_sudo

    def command(self, args: list[str]) -> list[str]:
        if self.use_sudo and os.geteuid() != 0:
            return ["sudo", "-n"] + args
        return args

    def run(self, args: list[str], *, timeout: float = 8.0) -> dict[str, Any]:
        command = self.command(args)
        started = time.perf_counter_ns()
        try:
            completed = subprocess.run(
                command,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
            ended = time.perf_counter_ns()
            return {
                "args": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
                "duration_ms": (ended - started) / 1_000_000.0,
            }
        except subprocess.TimeoutExpired as exc:
            ended = time.perf_counter_ns()
            return {
                "args": command,
                "returncode": 124,
                "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
                "duration_ms": (ended - started) / 1_000_000.0,
                "timeout": True,
            }

    def checked(self, args: list[str], *, timeout: float = 8.0) -> dict[str, Any]:
        result = self.run(args, timeout=timeout)
        if result["returncode"] != 0:
            raise RuntimeError(f"command failed: {result['args']} stderr={result['stderr']}")
        return result


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(values: list[float], failures: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(values),
        "failure_count": len(failures),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "max_ms": max(values) if values else None,
        "mean_ms": statistics.fmean(values) if values else None,
        "failures": failures[:5],
    }


def timed_sequence(runner: Runner, steps: list[list[str]], *, timeout: float = 8.0) -> dict[str, Any]:
    started = time.perf_counter_ns()
    step_results: list[dict[str, Any]] = []
    for step in steps:
        result = runner.run(step, timeout=timeout)
        step_results.append(result)
        if result["returncode"] != 0:
            return {
                "ok": False,
                "duration_ms": (time.perf_counter_ns() - started) / 1_000_000.0,
                "failed_step": result,
                "steps": step_results,
            }
    return {
        "ok": True,
        "duration_ms": (time.perf_counter_ns() - started) / 1_000_000.0,
        "steps": step_results,
    }


def cleanup_name(runner: Runner, name: str) -> None:
    runner.run(["ip", "link", "del", name], timeout=4.0)
    runner.run(["ip", "netns", "del", name], timeout=4.0)


def create_netns(runner: Runner, name: str) -> None:
    runner.checked(["ip", "netns", "add", name])
    runner.checked(["ip", "-n", name, "link", "set", "lo", "up"])


def run_iterations(
    iterations: int,
    body: Callable[[int], dict[str, Any]],
) -> dict[str, Any]:
    values: list[float] = []
    failures: list[dict[str, Any]] = []
    for index in range(iterations):
        try:
            result = body(index)
            if result["ok"]:
                values.append(result["duration_ms"])
            else:
                failures.append({"iteration": index, "result": result})
        except Exception as exc:  # noqa: BLE001 - bench should continue and report failures.
            failures.append({"iteration": index, "exception": repr(exc)})
    return summarize(values, failures)


def bench_veth_create_delete(runner: Runner, iterations: int, run_id: str) -> dict[str, Any]:
    def body(index: int) -> dict[str, Any]:
        a_name = f"{run_id}vc{index}a"
        b_name = f"{run_id}vc{index}b"
        try:
            return timed_sequence(
                runner,
                [
                    ["ip", "link", "add", a_name, "type", "veth", "peer", "name", b_name],
                    ["ip", "link", "del", a_name],
                ],
            )
        finally:
            runner.run(["ip", "link", "del", a_name], timeout=4.0)
            runner.run(["ip", "link", "del", b_name], timeout=4.0)

    return run_iterations(iterations, body)


def bench_veth_move_rename_updown(runner: Runner, iterations: int, run_id: str) -> dict[str, Any]:
    ns_name = f"{run_id}mvns"
    cleanup_name(runner, ns_name)
    create_netns(runner, ns_name)

    def body(index: int) -> dict[str, Any]:
        a_name = f"{run_id}mv{index}a"
        b_name = f"{run_id}mv{index}b"
        renamed = f"{run_id}e{index}"
        try:
            runner.checked(["ip", "link", "add", a_name, "type", "veth", "peer", "name", b_name])
            return timed_sequence(
                runner,
                [
                    ["ip", "link", "set", b_name, "netns", ns_name],
                    ["ip", "-n", ns_name, "link", "set", b_name, "name", renamed],
                    ["ip", "-n", ns_name, "link", "set", renamed, "up"],
                    ["ip", "-n", ns_name, "link", "set", renamed, "down"],
                ],
            )
        finally:
            runner.run(["ip", "link", "del", a_name], timeout=4.0)
            runner.run(["ip", "-n", ns_name, "link", "del", renamed], timeout=4.0)

    try:
        return run_iterations(iterations, body)
    finally:
        cleanup_name(runner, ns_name)


def bench_tc_netem(runner: Runner, iterations: int, run_id: str) -> dict[str, Any]:
    add_values: list[float] = []
    change_values: list[float] = []
    delete_values: list[float] = []
    failures: list[dict[str, Any]] = []
    for index in range(iterations):
        a_name = f"{run_id}tc{index}a"
        b_name = f"{run_id}tc{index}b"
        try:
            runner.checked(["ip", "link", "add", a_name, "type", "veth", "peer", "name", b_name])
            runner.checked(["ip", "link", "set", a_name, "up"])
            add = runner.run(["tc", "qdisc", "add", "dev", a_name, "root", "netem", "delay", "5ms"])
            change = runner.run(
                ["tc", "qdisc", "change", "dev", a_name, "root", "netem", "delay", "7ms", "loss", "0.1%"]
            )
            delete = runner.run(["tc", "qdisc", "del", "dev", a_name, "root"])
            if add["returncode"] == 0:
                add_values.append(add["duration_ms"])
            else:
                failures.append({"iteration": index, "operation": "add", "result": add})
            if change["returncode"] == 0:
                change_values.append(change["duration_ms"])
            else:
                failures.append({"iteration": index, "operation": "change", "result": change})
            if delete["returncode"] == 0:
                delete_values.append(delete["duration_ms"])
            else:
                failures.append({"iteration": index, "operation": "delete", "result": delete})
        except Exception as exc:  # noqa: BLE001
            failures.append({"iteration": index, "exception": repr(exc)})
        finally:
            runner.run(["tc", "qdisc", "del", "dev", a_name, "root"], timeout=4.0)
            runner.run(["ip", "link", "del", a_name], timeout=4.0)
            runner.run(["ip", "link", "del", b_name], timeout=4.0)
    return {
        "add": summarize(add_values, [f for f in failures if f.get("operation") == "add"]),
        "change": summarize(change_values, [f for f in failures if f.get("operation") == "change"]),
        "delete": summarize(delete_values, [f for f in failures if f.get("operation") == "delete"]),
        "all_failures": failures[:10],
    }


def bench_ip_route(runner: Runner, iterations: int, run_id: str) -> dict[str, Any]:
    ns_name = f"{run_id}rtns"
    cleanup_name(runner, ns_name)
    create_netns(runner, ns_name)
    add_values: list[float] = []
    replace_values: list[float] = []
    delete_values: list[float] = []
    failures: list[dict[str, Any]] = []
    try:
        for index in range(iterations):
            prefix = f"198.51.{index // 255}.{index % 255}/32"
            add = runner.run(["ip", "-n", ns_name, "route", "add", prefix, "dev", "lo"])
            replace = runner.run(["ip", "-n", ns_name, "route", "replace", prefix, "dev", "lo"])
            delete = runner.run(["ip", "-n", ns_name, "route", "delete", prefix])
            if add["returncode"] == 0:
                add_values.append(add["duration_ms"])
            else:
                failures.append({"iteration": index, "operation": "add", "result": add})
            if replace["returncode"] == 0:
                replace_values.append(replace["duration_ms"])
            else:
                failures.append({"iteration": index, "operation": "replace", "result": replace})
            if delete["returncode"] == 0:
                delete_values.append(delete["duration_ms"])
            else:
                failures.append({"iteration": index, "operation": "delete", "result": delete})
        return {
            "add": summarize(add_values, [f for f in failures if f.get("operation") == "add"]),
            "replace": summarize(replace_values, [f for f in failures if f.get("operation") == "replace"]),
            "delete": summarize(delete_values, [f for f in failures if f.get("operation") == "delete"]),
            "all_failures": failures[:10],
        }
    finally:
        cleanup_name(runner, ns_name)


def bench_mpls_route(runner: Runner, iterations: int, run_id: str) -> dict[str, Any]:
    route_show = runner.run(["ip", "-f", "mpls", "route", "show"])
    if route_show["returncode"] != 0:
        return {"skipped": True, "reason": "ip_mpls_route_show_failed", "probe": route_show}
    ns_name = f"{run_id}mpns"
    cleanup_name(runner, ns_name)
    create_netns(runner, ns_name)
    add_values: list[float] = []
    delete_values: list[float] = []
    failures: list[dict[str, Any]] = []
    try:
        for index in range(iterations):
            label = str(20000 + index)
            add = runner.run(["ip", "-n", ns_name, "-f", "mpls", "route", "add", label, "dev", "lo"])
            delete = runner.run(["ip", "-n", ns_name, "-f", "mpls", "route", "delete", label])
            if add["returncode"] == 0:
                add_values.append(add["duration_ms"])
            else:
                failures.append({"iteration": index, "operation": "add", "result": add})
            if delete["returncode"] == 0:
                delete_values.append(delete["duration_ms"])
            else:
                failures.append({"iteration": index, "operation": "delete", "result": delete})
        return {
            "add": summarize(add_values, [f for f in failures if f.get("operation") == "add"]),
            "delete": summarize(delete_values, [f for f in failures if f.get("operation") == "delete"]),
            "all_failures": failures[:10],
        }
    finally:
        cleanup_name(runner, ns_name)


def bench_quarantine_move(runner: Runner, iterations: int, run_id: str) -> dict[str, Any]:
    q_ns = f"{run_id}q"
    a_ns = f"{run_id}n1"
    b_ns = f"{run_id}n2"
    for name in [q_ns, a_ns, b_ns]:
        cleanup_name(runner, name)
        create_netns(runner, name)
    activation_values: list[float] = []
    deactivation_values: list[float] = []
    failures: list[dict[str, Any]] = []
    try:
        for index in range(iterations):
            qa = f"{run_id}qa{index}"
            qb = f"{run_id}qb{index}"
            ea = f"{run_id}ea{index}"
            eb = f"{run_id}eb{index}"
            try:
                runner.checked(["ip", "-n", q_ns, "link", "add", qa, "type", "veth", "peer", "name", qb])
                activation = timed_sequence(
                    runner,
                    [
                        ["ip", "-n", q_ns, "link", "set", qa, "netns", a_ns],
                        ["ip", "-n", a_ns, "link", "set", qa, "name", ea],
                        ["ip", "-n", a_ns, "link", "set", ea, "up"],
                        ["ip", "-n", q_ns, "link", "set", qb, "netns", b_ns],
                        ["ip", "-n", b_ns, "link", "set", qb, "name", eb],
                        ["ip", "-n", b_ns, "link", "set", eb, "up"],
                    ],
                )
                if activation["ok"]:
                    activation_values.append(activation["duration_ms"])
                else:
                    failures.append({"iteration": index, "operation": "activation", "result": activation})
                deactivation = timed_sequence(
                    runner,
                    [
                        ["ip", "-n", a_ns, "link", "set", ea, "down"],
                        ["ip", "-n", a_ns, "link", "set", ea, "netns", q_ns],
                        ["ip", "-n", q_ns, "link", "set", ea, "name", qa],
                        ["ip", "-n", b_ns, "link", "set", eb, "down"],
                        ["ip", "-n", b_ns, "link", "set", eb, "netns", q_ns],
                        ["ip", "-n", q_ns, "link", "set", eb, "name", qb],
                    ],
                )
                if deactivation["ok"]:
                    deactivation_values.append(deactivation["duration_ms"])
                else:
                    failures.append({"iteration": index, "operation": "deactivation", "result": deactivation})
            except Exception as exc:  # noqa: BLE001
                failures.append({"iteration": index, "exception": repr(exc)})
            finally:
                runner.run(["ip", "-n", q_ns, "link", "del", qa], timeout=4.0)
                runner.run(["ip", "-n", q_ns, "link", "del", qb], timeout=4.0)
                runner.run(["ip", "-n", a_ns, "link", "del", ea], timeout=4.0)
                runner.run(["ip", "-n", b_ns, "link", "del", eb], timeout=4.0)
        return {
            "activation": summarize(activation_values, [f for f in failures if f.get("operation") == "activation"]),
            "deactivation": summarize(deactivation_values, [f for f in failures if f.get("operation") == "deactivation"]),
            "all_failures": failures[:10],
        }
    finally:
        for name in [q_ns, a_ns, b_ns]:
            cleanup_name(runner, name)


def pyroute2_state() -> dict[str, Any]:
    try:
        import pyroute2  # type: ignore[import-not-found]

        return {"available": True, "version": getattr(pyroute2, "__version__", "unknown")}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": repr(exc), "comparison": "skipped"}


def residue_report(runner: Runner, run_id: str) -> dict[str, Any]:
    netns = runner.run(["ip", "netns", "list"], timeout=4.0)
    links = runner.run(["ip", "-o", "link", "show"], timeout=4.0)
    matching_netns = [line for line in netns["stdout"].splitlines() if run_id in line or PREFIX in line]
    matching_links = [line for line in links["stdout"].splitlines() if run_id in line or PREFIX in line]
    return {
        "matching_netns_count": len(matching_netns),
        "matching_link_count": len(matching_links),
        "matching_netns": matching_netns,
        "matching_links": matching_links,
        "clean": len(matching_netns) == 0 and len(matching_links) == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--no-sudo", action="store_true", help="Do not use sudo for netns/veth operations.")
    args = parser.parse_args()

    run_id = f"scp{os.getpid() % 100000}_"
    use_sudo = not args.no_sudo and os.geteuid() != 0 and sudo_available()
    runner = Runner(use_sudo=use_sudo)
    if not shutil.which("ip") or not shutil.which("tc"):
        raise SystemExit("ip and tc are required for this benchmark")

    report: dict[str, Any] = {
        "schema_version": 1,
        "bench": "skyseed_control_plane_ops",
        "timestamp_unix": time.time(),
        "iterations": args.iterations,
        "run_id": run_id,
        "uid": os.getuid(),
        "euid": os.geteuid(),
        "used_sudo": use_sudo,
        "subprocess_iproute2": {},
        "direct_netlink_pyroute2": pyroute2_state(),
    }

    benches: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("veth_create_delete", lambda: bench_veth_create_delete(runner, args.iterations, run_id)),
        ("veth_move_rename_updown", lambda: bench_veth_move_rename_updown(runner, args.iterations, run_id)),
        ("tc_netem", lambda: bench_tc_netem(runner, args.iterations, run_id)),
        ("ip_route", lambda: bench_ip_route(runner, args.iterations, run_id)),
        ("mpls_route", lambda: bench_mpls_route(runner, args.iterations, run_id)),
        ("quarantine_veth_activation", lambda: bench_quarantine_move(runner, args.iterations, run_id)),
    ]
    for name, bench in benches:
        started = time.perf_counter()
        try:
            result = bench()
        except Exception as exc:  # noqa: BLE001
            result = {"failure": repr(exc)}
        result["bench_wall_seconds"] = time.perf_counter() - started
        report["subprocess_iproute2"][name] = result

    report["residue"] = residue_report(runner, run_id)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["residue"]["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
