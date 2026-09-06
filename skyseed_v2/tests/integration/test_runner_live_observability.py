from __future__ import annotations

import os
import json
import subprocess
from uuid import uuid4

import pytest

from skyseed_v2.backend.bpf.runner import BpfRunner, _PrivilegedHelper
from skyseed_v2.backend.bpf._runner.kernel_netlink import _RETIREMENT_GROUP_BASE


def _run(*command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=check)


def _node(
    *,
    pid: int,
    incarnation: tuple[int, int],
    key: str,
    slot: int,
    address: str,
) -> dict[str, object]:
    start_time_ticks, netns_inode = incarnation
    return {
        "key": key,
        "node_slot": slot,
        "pid": pid,
        "process_start_time_ticks": start_time_ticks,
        "netns_inode": netns_inode,
        "pin_component": f"n-{key}",
        "node_address": address,
        "active_links_capacity": 8,
        "routes_capacity": 16,
        "bindings_capacity": 2,
    }


def test_main_boundary_owns_live_veth_establish_withdraw_and_recreate() -> None:
    """Opt-in live qualification using the current private helper wire contract."""
    image = os.environ.get("SKYSEED_BPF_HELPER_IMAGE")
    if not image:
        pytest.skip("set SKYSEED_BPF_HELPER_IMAGE for live Runner observability")

    token = uuid4().hex[:12]
    containers = [f"skyseed-v2-observe-{token}-{suffix}" for suffix in ("a", "b")]
    helper = _PrivilegedHelper(
        docker_executable="docker",
        name=f"skyseed-v2-observe-helper-{token}",
        foundation_token=f"live-observability-{token}",
        image_id=image,
    )
    pin_root = f"/sys/fs/bpf/skyseed-v2/live-observability-{token}"
    initialized = False
    current_connection: list[dict[str, object]] = []
    nodes: list[dict[str, object]] = []
    try:
        for container in containers:
            _run(
                "docker", "run", "--detach", "--rm", "--network", "none",
                "--name", container, "--entrypoint", "/bin/sh", image,
                "-c", "while :; do sleep 3600; done",
            )
        inspected = json.loads(
            _run("docker", "inspect", *containers).stdout
        )
        pids = tuple(int(value["State"]["Pid"]) for value in inspected)
        capture_runner = object.__new__(BpfRunner)
        capture_runner._docker = "docker"
        incarnations = capture_runner._capture_privileged_incarnations(
            image_id=image,
            pids=pids,
        )
        nodes = [
            _node(
                pid=pids[0], incarnation=incarnations[0],
                key="a", slot=0, address="100.64.0.1",
            ),
            _node(
                pid=pids[1], incarnation=incarnations[1],
                key="b", slot=1, address="100.64.0.2",
            ),
        ]
        helper.start()
        helper.execute(
            "initialize",
            {
                "pin_root": pin_root,
                "common_mac": "02:53:4b:59:00:01",
                "nodes": nodes,
                "inactive_generation": 0xFFFFFFFF,
                "telemetry_enabled": False,
                "node_address_range": "100.64.0.0/29",
                "zone_prefixes": ["198.51.100.0/24"],
                "worker_count": 2,
                "retirement_group_end": _RETIREMENT_GROUP_BASE + 4,
            },
        )
        initialized = True
        connection = {
            "connection": "a-b",
            "logical_port_a": 1,
            "logical_port_b": 2,
            "node_a": "a",
            "node_b": "b",
            "interface_a": "sv20000000000a",
            "interface_b": "sv20000000000b",
            "retirement_node": "a",
            "retirement_group": _RETIREMENT_GROUP_BASE + 2,
            "parameters_a": {
                "bandwidth_bps": 10_000_000,
                "delay_us": 100,
                "packet_loss_ppm": 0,
            },
            "parameters_b": {
                "bandwidth_bps": 10_000_000,
                "delay_us": 100,
                "packet_loss_ppm": 0,
            },
        }


        # Prewrite the complete finite experiment before creating any veth.
        for generation, present in ((0, True), (1, False), (2, True)):
            records = []
            for key, port, local, remote in (("a", 1, "100.64.0.1", "100.64.0.2"),
                                             ("b", 2, "100.64.0.2", "100.64.0.1")):
                records.append({"key": key, "active_ports": [port] if present else [],
                                "routes": [{"destination": local, "logical_port_id": 0}]
                                + ([{"destination": remote, "logical_port_id": port}] if present else [])})
            result = helper.execute("prewrite_generation", {
                "pin_root": pin_root, "generation": generation, "generation_nodes": records,
                "bpf_batch_update_supported": False,
            })
            assert result["preloaded_active_links"] == (2 if present else 0)
            assert result["preloaded_routes"] == (4 if present else 2)
        assert _run("docker", "exec", containers[0], "ip", "link", "show", "sv20000000000a", check=False).returncode != 0
        for generation, present in ((0, True), (1, False), (2, True)):
            if generation == 2:
                connection = {**connection, "retirement_group": _RETIREMENT_GROUP_BASE + 4}
            operations = []
            for key, interface, remote in (("a", connection["interface_a"], "100.64.0.2"),
                                           ("b", connection["interface_b"], "100.64.0.1")):
                operations.append({"key": key, "qdisc": [],
                    "route_replace": [[remote, interface]] if present else [],
                    "route_withdraw": [] if present else [[remote, interface, True]],
                    "zone_address_add": [], "zone_address_delete": []})
            current_connection = [connection]
            result = helper.execute("activate_boundary", {
                "pin_root": pin_root, "common_mac": "02:53:4b:59:00:01",
                "boundary_timestamp": f"2026-01-01T00:00:0{generation}+00:00",
                "expected_active_generation": 0xFFFFFFFF if generation == 0 else generation - 1,
                "generation": generation, "generation_changed": True,
                "operation_nodes": operations,
                "connections": [connection] if present else [],
                "retirement_batches": [] if present else [{
                    "key": connection["retirement_node"],
                    "group": connection["retirement_group"],
                    "connections": [connection],
                }],
            })
            assert result["target_transition_complete"] is True
            assert result["generation_publication_occurred"] is True
            assert result["created_connections"] == int(present)
            assert result["deleted_connections"] == int(not present)
            assert set(result["activation_phases"]) == {
                "pre_publication", "active_generation_publication", "post_publication",
                "withdrawal", "physical_deletion"}
            exists = _run("docker", "exec", containers[0], "ip", "link", "show", "sv20000000000a", check=False).returncode == 0
            assert exists is present
            if present:
                _run("docker", "exec", containers[0], "ping", "-c", "1", "-W", "2", "100.64.0.2")
    finally:
        if initialized:
            helper.execute("cleanup", {"pin_root": pin_root,
                "nodes": [{**node, "node_address_owned": True} for node in nodes],
                "connections": current_connection})
        helper.stop()
        for container in containers:
            _run("docker", "rm", "--force", container, check=False)
