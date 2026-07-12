#!/usr/bin/env python3
# encoding: utf-8

"""Summarize non-blocking runtime UT->selected-GW ping observations."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _bucket(row: dict[str, Any]) -> str:
    if row.get("superseded") is True or str(row.get("observation_status") or "") == "superseded":
        return "superseded"
    stderr = str(row.get("stderr") or "")
    stdout = str(row.get("stdout") or "")
    if row.get("success") is True or int(row.get("returncode") or -1) == 0:
        return "success"
    if " 0% packet loss" in stdout or ", 0% packet loss" in stdout:
        return "icmp_success_observer_timeout"
    if "Network is unreachable" in stderr:
        return "ut_namespace_no_route_to_selected_gateway_access"
    if "100% packet loss" in stdout:
        return "icmp_request_or_gw_local_reply_not_observed"
    if str(row.get("error") or ""):
        return "observer_command_error"
    return "failed_unclassified"


def _sample(rows: list[dict[str, Any]], bucket: str) -> dict[str, Any]:
    for row in rows:
        if _bucket(row) == bucket:
            return dict(row)
    return {}


def audit_runtime_ping_observer(path: Path) -> dict[str, Any]:
    rows = _read_jsonl(path)
    valid_rows = [row for row in rows if _bucket(row) != "superseded"]
    superseded_rows = [row for row in rows if _bucket(row) == "superseded"]
    buckets = Counter(_bucket(row) for row in rows)
    target_roles = Counter(str(row.get("target_role") or "") for row in rows)
    returncodes = Counter(str(row.get("returncode")) for row in rows)
    ut_ids = sorted({str(row.get("ut_id") or "") for row in rows if row.get("ut_id")})
    target_ids = sorted(
        {
            str(row.get("target_node_id") or row.get("selected_gateway_id") or "")
            for row in rows
            if row.get("target_node_id") or row.get("selected_gateway_id")
        }
    )
    sample_unreachable = _sample(
        rows,
        "ut_namespace_no_route_to_selected_gateway_access",
    )
    sample_loss = _sample(rows, "icmp_request_or_gw_local_reply_not_observed")
    path_success_count = int(buckets.get("success", 0)) + int(
        buckets.get("icmp_success_observer_timeout", 0)
    )
    live_probe_plan: list[dict[str, Any]] = []
    if sample_unreachable:
        live_probe_plan.append(
            {
                "classification": "ut_namespace_no_route_to_selected_gateway_access",
                "purpose": "prove whether UT default route/access peer exists before BPF path diagnosis",
                "commands": [
                    f"docker exec {sample_unreachable.get('ut_id')} ip route get {sample_unreachable.get('target_ip')}",
                    f"docker exec {sample_unreachable.get('ut_id')} ip addr",
                ],
            }
        )
    if sample_loss:
        ut_id = str(sample_loss.get("ut_id") or "<ut>")
        gw_id = str(
            sample_loss.get("target_node_id")
            or sample_loss.get("selected_gateway_id")
            or "<gateway>"
        )
        target_ip = str(sample_loss.get("target_ip") or "<gateway_access_ip>")
        live_probe_plan.append(
            {
                "classification": "icmp_request_or_gw_local_reply_not_observed",
                "purpose": "separate forward delivery from GW-local Z->G return import",
                "commands": [
                    f"docker exec {ut_id} ping -n -c 1 -W 2 {target_ip}",
                    f"docker exec {gw_id} ip route get <ut_access_ip>",
                    f"docker exec {gw_id} tc -s filter show dev <gateway_access_interface> ingress",
                    f"docker exec {gw_id} tc -s filter show dev <gateway_access_interface> egress",
                    f"docker exec {gw_id} tc -s filter show dev <gateway_nat_interface> ingress",
                ],
            }
        )
    return {
        "schema_version": "skyseed-runtime-ping-observer-audit-v1",
        "source": str(path),
        "observer_semantics": "non_blocking_raw_observation_not_runtime_gate",
        "observation_count": len(rows),
        "valid_observation_count": len(valid_rows),
        "superseded_observation_count": len(superseded_rows),
        "success_count": int(buckets.get("success", 0)),
        "path_success_count": path_success_count,
        "path_failure_count": len(valid_rows) - path_success_count,
        "observer_timeout_after_path_success_count": int(
            buckets.get("icmp_success_observer_timeout", 0)
        ),
        "failure_count": len(valid_rows) - int(buckets.get("success", 0)),
        "bucket_counts": dict(sorted(buckets.items())),
        "returncode_counts": dict(sorted(returncodes.items())),
        "target_role_counts": dict(sorted(target_roles.items())),
        "unique_ut_count": len(ut_ids),
        "unique_target_node_count": len(target_ids),
        "policy_scope_counts": dict(
            sorted(
                Counter(
                    str(row.get("traffic_scope") or "unknown")
                    for row in valid_rows
                ).items()
            )
        ),
        "sample_unreachable": sample_unreachable,
        "sample_packet_loss": sample_loss,
        "semantic_notes": [
            "Network-is-unreachable rows classify before BPF/GW return diagnosis: the UT namespace did not choose a route to the explicit gateway service IP.",
            "100%-loss rows are compatible with a missing GW-local service reply or return import into the shared Z->G FEC, but require matching BPF path evidence to prove.",
            "Superseded rows crossed a session/generation boundary and are excluded from path success/failure counts.",
            "Do not add NAT/FORWARD/Mini-host fallback to make this observer pass.",
        ],
        "live_probe_plan": live_probe_plan,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_runtime_ping_observer(args.path)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
