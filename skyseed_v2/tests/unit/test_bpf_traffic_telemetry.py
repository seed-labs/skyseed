from __future__ import annotations

import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from skyseed_v2.backend.bpf import runner as runner_module
from skyseed_v2.backend.bpf._runner import (
    privileged_runtime_command as command_module,
)
from skyseed_v2.backend.bpf._runner import (
    traffic_telemetry_collector as collector_module,
)
from skyseed_v2.backend.bpf._runner import traffic_telemetry_abi as protocol_module
from skyseed_v2.backend.bpf._runner.traffic_telemetry import (
    _TrafficTelemetryRecorder,
)


def _ipv4_word(value: str) -> int:
    return struct.unpack("=I", socket.inet_aton(value))[0]


def test_fixed_packet_event_decodes_explicit_icmp_and_ingress_facts() -> None:
    payload = protocol_module._TELEMETRY_EVENT.pack(
        123456789,
        7,
        12,
        41,
        52,
        _ipv4_word("100.64.0.1"),
        _ipv4_word("100.64.0.3"),
        98,
        protocol_module._TELEMETRY_DECISION_REDIRECT,
        protocol_module._TELEMETRY_REASON_REDIRECT,
        1,
        1,
        8,
        0,
        socket.htons(321),
        socket.htons(9),
        0,
    )

    assert protocol_module._decode_telemetry_event(payload) == {
        "record_type": "packet",
        "observation": "ingress",
        "kernel_monotonic_ns": 123456789,
        "node_slot": 7,
        "generation": 12,
        "ingress_ifindex": 41,
        "selected_egress_ifindex": 52,
        "source_ipv4": "100.64.0.1",
        "destination_ipv4": "100.64.0.3",
        "packet_length": 98,
        "decision": "redirect",
        "reason": "route-redirect",
        "protocol": 1,
        "icmp": {
            "valid": True,
            "type": 8,
            "code": 0,
            "identifier": 321,
            "sequence": 9,
        },
    }


def test_long_lived_collector_is_separate_from_one_shot_command() -> None:
    assert not hasattr(command_module, "_collect_telemetry")
    assert callable(collector_module._collect_telemetry)
    assert protocol_module._TELEMETRY_EVENT.size == 48


def test_worker_starts_the_dedicated_telemetry_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = []
    input_lines = []

    class _Input:
        def write(self, value):
            input_lines.append(value)

        def flush(self):
            pass

    process = SimpleNamespace(stdin=_Input())
    monkeypatch.setattr(
        runner_module._PrivilegedHelper,
        "assert_current",
        lambda _self: None,
    )

    def start(command, **kwargs):
        commands.append((command, kwargs))
        return process

    monkeypatch.setattr(runner_module.subprocess, "Popen", start)
    helper = runner_module._PrivilegedHelper(
        docker_executable="docker",
        name="skyseed-helper",
        foundation_token="foundation-token",
        image_id="helper-image",
    )
    request = {"pin_root": "/sys/fs/bpf/skyseed-v2/run-test", "nodes": ()}

    assert helper.start_telemetry_collector(request) is process

    command, kwargs = commands[0]
    assert Path(command[-1]).name == "traffic_telemetry_collector.py"
    assert "privileged_runtime_command.py" not in command
    assert "collect_telemetry" not in command
    assert json.loads(input_lines[0]) == {
        "pin_root": request["pin_root"],
        "nodes": [],
    }
    assert kwargs["stdin"] == subprocess.PIPE
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.PIPE


def test_initialize_binds_one_shared_ring_and_loss_map_to_each_node_program(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    descriptors = iter(range(10, 100))
    map_calls = []
    program_calls = []
    nodes = tuple(
        {
            "key": f"65001/satellite/sat-{slot}",
            "node_slot": slot,
            "pid": 100 + slot,
            "netns_inode": 200 + slot,
            "pin_component": f"n-node-{slot}",
            "node_address": f"100.64.0.{slot + 1}",
            "active_links_capacity": 8,
            "routes_capacity": 8,
        }
        for slot in (0, 1)
    )

    monkeypatch.setattr(command_module, "_pin_root", lambda _value: tmp_path / "pins")
    monkeypatch.setattr(command_module, "_assert_netns", lambda _node: None)
    monkeypatch.setattr(
        command_module,
        "_audit_node_addresses",
        lambda _request: {"result": "passed"},
    )

    def create_map(name, **facts):
        descriptor = next(descriptors)
        map_calls.append((name, descriptor, facts))
        return descriptor

    def load_program(*maps, **facts):
        program_calls.append((maps, facts))
        return next(descriptors)

    monkeypatch.setattr(command_module, "_create_map", create_map)
    monkeypatch.setattr(command_module, "_load_program", load_program)
    monkeypatch.setattr(command_module, "_pin_object", lambda *_args: None)
    monkeypatch.setattr(command_module, "_update_map", lambda *_args: None)
    monkeypatch.setattr(
        command_module,
        "_in_netns",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(command_module.os, "close", lambda _descriptor: None)

    result = command_module._initialize(
        {
            "pin_root": "/sys/fs/bpf/skyseed-v2/run-telemetry-test",
            "nodes": nodes,
            "inactive_generation": 0xFFFFFFFF,
            "telemetry_enabled": True,
            "node_address_range": "100.64.0.0/29",
            "zone_prefixes": (),
        }
    )

    ring = next(value for value in map_calls if value[0] == "traffic_events")
    losses = next(value for value in map_calls if value[0] == "traffic_losses")
    assert ring[2]["map_type"] == command_module._BPF_MAP_TYPE_RINGBUF
    assert losses[2] == {
        "key_size": 4,
        "value_size": 8,
        "max_entries": 2,
        "map_type": command_module._BPF_MAP_TYPE_ARRAY,
    }
    assert [facts["node_slot"] for _maps, facts in program_calls] == [0, 1]
    assert all(facts["telemetry_ring_map"] == ring[1] for _, facts in program_calls)
    assert all(facts["telemetry_loss_map"] == losses[1] for _, facts in program_calls)
    assert result["traffic_telemetry"]["shared_ring_buffer"] is True


def test_ring_output_failure_path_converges_back_to_original_terminal_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_assembler = command_module._Assembler
    captured = []

    class _InspectingAssembler(real_assembler):
        def finish(self):
            captured.append(self)
            return super().finish()

    monkeypatch.setattr(command_module, "_Assembler", _InspectingAssembler)
    command_module._tc_program(
        10,
        11,
        12,
        telemetry_ring_map=13,
        telemetry_loss_map=14,
        node_slot=3,
    )
    assembler = captured[0]
    assert assembler.labels["emit_telemetry"] < assembler.labels["telemetry_emitted"]
    assert assembler.labels["telemetry_emitted"] < assembler.labels["redirect"]
    assert assembler.labels["redirect"] < assembler.labels["drop"]
    assert assembler.labels["drop"] < assembler.labels["pass"]
    jump_targets = [value[1] for value in assembler.jumps]
    assert jump_targets.count("telemetry_emitted") == 2
    after_output = jump_targets[
        jump_targets.index("telemetry_emitted") + 1 :
    ]
    assert "redirect" in after_output
    assert "drop" in after_output
    assert "pass" in after_output


def test_malformed_ipv4_ihl_leaves_icmp_metadata_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_assembler = command_module._Assembler
    captured = []

    class _InspectingAssembler(real_assembler):
        def finish(self):
            captured.append(self)
            return super().finish()

    monkeypatch.setattr(command_module, "_Assembler", _InspectingAssembler)
    command_module._tc_program(
        10,
        11,
        12,
        telemetry_ring_map=13,
        telemetry_loss_map=14,
        node_slot=3,
    )

    assert any(
        label == "after_icmp"
        and code == command_module._BPF_JMP_JLT_IMMEDIATE
        and destination == 4
        and immediate == 5
        for _index, label, code, destination, _source, immediate
        in captured[0].jumps
    )


class _CollectorHelper:
    def start_telemetry_collector(self, request):
        assert request == {
            "pin_root": "/sys/fs/bpf/skyseed-v2/run-test",
            "nodes": ({"node_slot": 0, "node_id": "node-a"},),
        }
        script = """
import json
import sys
import time
print(json.dumps({"record_type": "collector_start"}), flush=True)
print(json.dumps({"record_type": "packet", "node_slot": 0, "stage": "live"}), flush=True)
line = sys.stdin.readline()
assert json.loads(line) == {"command": "stop"}
print(json.dumps({"record_type": "packet", "node_slot": 0, "stage": "final-drain"}), flush=True)
print(json.dumps({"record_type": "telemetry_loss", "node_slot": 0, "lost_events": 2}), flush=True)
print(json.dumps({"record_type": "collector_end"}), flush=True)
"""
        return subprocess.Popen(
            (sys.executable, "-u", "-c", script),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


def test_recorder_streams_identity_and_preserves_final_drain_prefix(
    tmp_path: Path,
) -> None:
    recorder = _TrafficTelemetryRecorder(tmp_path / "inspection/traffic")
    recorder.start(
        helper=_CollectorHelper(),
        foundation={
            "foundation_token": "foundation-test",
            "pin_root": "/sys/fs/bpf/skyseed-v2/run-test",
            "ring_capacity_bytes": 1 << 20,
            "event_schema": {"name": "skyseed-tc-ingress-v1"},
            "nodes": (
                {
                    "node_slot": 0,
                    "node_id": "node-a",
                    "runtime_ipv4": "100.64.0.1",
                    "pin_component": "n-node-a",
                },
            ),
            "collector_request": {
                "pin_root": "/sys/fs/bpf/skyseed-v2/run-test",
                "nodes": ({"node_slot": 0, "node_id": "node-a"},),
            },
        },
    )
    recorder.write_generation_identity(
        (
            {
                "generation": 4,
                "node_slot": 0,
                "node_id": "node-a",
                "ifindex": 12,
                "interface": "sv2examplea",
                "connection_id": "isl:node-a--node-b",
                "peer_node_id": "node-b",
                "validity": "exact-generation",
            },
        )
    )
    recorder.write_simulation_clock_anchor(
        nominal_origin="2026-01-01T00:00:00+00:00",
        monotonic_origin_seconds=123.25,
    )

    snapshot = recorder.stop_and_drain()
    records = tuple(
        json.loads(line)
        for line in (tmp_path / "inspection/traffic/traffic-telemetry.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert records[0]["record_type"] == "telemetry_header"
    assert records[0]["source_observation"] == {
        "hook": "ingress-only",
        "source_outbound_observed": False,
        "first_link": "inferred-from-first-observed-ingress",
    }
    assert records[1]["record_type"] == "node_identity"
    assert any(value["record_type"] == "interface_identity" for value in records)
    assert next(
        value
        for value in records
        if value["record_type"] == "simulation_clock_anchor"
    ) == {
        "record_type": "simulation_clock_anchor",
        "nominal_origin": "2026-01-01T00:00:00+00:00",
        "monotonic_origin_ns": 123_250_000_000,
        "clock_domain": "host-kernel-monotonic",
        "nominal_pacing": "1:1",
    }
    assert any(
        value.get("stage") == "final-drain"
        for value in records
        if value["record_type"] == "packet"
    )
    assert records[-1]["record_type"] == "recorder_end"
    assert snapshot["complete"] is True
    assert snapshot["packet_records"] == 2
    assert snapshot["reported_losses"] == 2


def test_durable_output_failure_is_reported_without_becoming_control_failure(
    tmp_path: Path,
) -> None:
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_text("occupied", encoding="utf-8")
    recorder = _TrafficTelemetryRecorder(blocking_file / "traffic")

    recorder.start(helper=_CollectorHelper(), foundation={})
    stopped = recorder.stop_and_drain()

    assert stopped["complete"] is False
    assert stopped["failure"]["type"] in {"FileExistsError", "NotADirectoryError"}
