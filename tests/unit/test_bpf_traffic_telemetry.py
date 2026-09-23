from __future__ import annotations

import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from skyseed.backend.runner import command_channel as runner_module
from skyseed.backend.bpf.dataplane import runtime_command as command_module
from skyseed.backend.bpf.runner import (
    traffic_telemetry_collector as collector_module,
)
from skyseed.backend.bpf.dataplane import telemetry_abi as protocol_module
from skyseed.backend.bpf.runner.traffic_telemetry import _TrafficTelemetryRecorder


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
        7,
        1,
    )

    assert protocol_module._decode_telemetry_event(payload) == {
        "record_type": "packet",
        "observation": "ingress",
        "local_origin": False,
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
        "tc_action": 7,
        "reason_code": 1,
        "metadata_source": "handler",
        "ipv4_observed": True,
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
    assert protocol_module._TELEMETRY_EVENT.size == 56


def test_local_origin_flag_is_distinct_from_a_network_ingress_observation():
    payload = protocol_module._TELEMETRY_EVENT.pack(
        123, 7, 12, 41, 52, _ipv4_word('100.64.0.1'), _ipv4_word('100.64.0.3'),
        98, 1, 1, 1, 1, 8, 0, socket.htons(321), socket.htons(9), 0, 7, 3)
    result = protocol_module._decode_telemetry_event(payload)
    assert result['local_origin'] is True and result['observation'] == 'local-output'
    assert result['ipv4_observed'] is True and result['selected_egress_ifindex'] == 52


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


def test_initialize_binds_one_shared_ring_and_loss_map_to_each_node_program(monkeypatch, tmp_path):
    from tests.unit.test_bpf_local_ingress import FoundationKernel
    from tests.unit.test_dataplane_preparation import _node_foundation
    foundation, _ = _node_foundation(2)
    kernel = FoundationKernel(monkeypatch, tmp_path / 'pins')
    map_calls, program_calls = [], []
    create, load = command_module._create_map, command_module._load_object
    def create_map(name, **facts):
        fd = create(name, **facts)
        map_calls.append((name, fd, facts))
        return fd
    def load_program(*args, **facts):
        program_calls.append((args, facts))
        return load(*args, **facts)
    monkeypatch.setattr(command_module, '_create_map', create_map)
    monkeypatch.setattr(command_module, '_load_object', load_program)
    nodes = [{**n, 'pid':100+i, 'netns_inode':200+i, 'process_start_time_ticks':1,
              'active_links_capacity':8, 'routes_capacity':8, 'bindings_capacity':4,
              'table_capacities':[{'program':0, 'active':8, 'routes':8}]} for i,n in enumerate(foundation['nodes'])]
    request = {'pin_root':'/pins', 'nodes':nodes, 'dataplane':foundation, 'inactive_generation':0xffffffff,
               'retirement_group_end':0x53530002, 'resident_generation_capacity':8, 'telemetry_enabled':True,
               'node_address_range':foundation['node_address_range'], 'zone_prefixes':()}
    try:
        result = command_module._initialize(request)
        ring = next(v for v in map_calls if v[0] == 'traffic_events')
        losses = next(v for v in map_calls if v[0] == 'traffic_losses')
        assert ring[2]['map_type'] == command_module._BPF_MAP_TYPE_RINGBUF
        assert losses[2] == {'key_size':4, 'value_size':8, 'max_entries':2, 'map_type':command_module._BPF_MAP_TYPE_ARRAY}
        assert len(program_calls) == 2
        for slot, (arguments, facts) in enumerate(program_calls):
            assert arguments == (foundation['programs'][0],)
            assert facts['node_slot'] == slot
            assert facts['bindings']['traffic_events'] == ring[1]
            assert facts['bindings']['traffic_losses'] == losses[1]
        assert result['traffic_telemetry']['shared_ring_buffer'] is True
    finally:
        command_module._cleanup(request)
    kernel.assert_clean()


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
    recorder = _TrafficTelemetryRecorder(tmp_path / "visualizations/traffic")
    recorder.start(
        helper=_CollectorHelper(),
        foundation={
            "foundation_token": "foundation-test",
            "pin_root": "/sys/fs/bpf/skyseed-v2/run-test",
            "ring_capacity_bytes": 1 << 20,
            "event_schema": {"name": "skyseed-tc-ingress-v3"},
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
    recorder.enqueue_generation_identity(
        ({"generation": 4, "node_slot": 0, "node_id": "node-a",
          "logical_port_id": 1, "interface": "sv2examplea",
          "connection_id": "isl:node-a--node-b", "peer_node_id": "node-b",
          "validity": "exact-generation"},),
        ({"logical_port_id": 1, "ifindex": 12},), (),
        {"generation_publication_occurred": True, "active_generation": 4,
         "boundary_timestamp": "2026-01-01T00:00:00+00:00"},
    )
    recorder.write_simulation_clock_anchor(
        nominal_origin="2026-01-01T00:00:00+00:00",
        monotonic_origin_seconds=123.25,
    )

    snapshot = recorder.stop_and_drain()
    records = tuple(
        json.loads(line)
        for line in (tmp_path / "visualizations/traffic/traffic-telemetry.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert records[0]["record_type"] == "telemetry_header"
    assert records[0]["source_observation"] == {
        "hook": "network-and-local-ingress",
        "source_outbound_observed": True,
        "scope": "covered-nodes",
        "first_link": "source-selection-recorded-arrival-requires-peer-ingress",
    }
    assert records[1]["record_type"] == "node_identity"
    assert any(value["record_type"] == "interface_update" for value in records)
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
        "revision": 0, "action": "start", "held_since": None,
        "active_frontier": None, "effective_monotonic_ns": 123_250_000_000,
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


def test_identity_expansion_and_flush_run_only_on_background_writer(monkeypatch, tmp_path):
    from threading import Event, Thread, current_thread
    recorder = _TrafficTelemetryRecorder(tmp_path)
    entered, release = Event(), Event()
    written = []
    main_thread = current_thread()
    def write(records):
        assert current_thread() is not main_thread
        entered.set()
        assert release.wait(2)
        written.extend(records)
    monkeypatch.setattr(recorder, "_write_many", write)
    monkeypatch.setattr(recorder, "_write", lambda record: None)
    writer = Thread(target=recorder._write_queued_identities)
    writer.start()
    templates = ({"logical_port_id": 1, "generation": 0, "node_id": "a"},)
    boundary = {"generation_publication_occurred": True, "active_generation": 0, "boundary_timestamp": "2026-01-01T00:00:00+00:00"}
    recorder.enqueue_generation_identity(templates, ({"logical_port_id": 1, "ifindex": 42},), (), boundary)
    assert entered.wait(1)
    # The blocked writer does not gate this next boundary's compact enqueue.
    recorder.enqueue_generation_identity(templates, (), (), {**boundary, "active_generation": 1})
    release.set()
    recorder._identity_queue.put(None)
    writer.join(2)
    assert not writer.is_alive()
    assert [record["ifindex"] for record in written] == [42]  # retained endpoint shared across generations


def test_formal_clock_anchor_is_queued_without_synchronous_flush(monkeypatch, tmp_path):
    recorder = _TrafficTelemetryRecorder(tmp_path)
    def forbidden(*args, **kwargs): raise AssertionError("clock anchor blocked on output")
    monkeypatch.setattr(recorder, "_write", forbidden)
    recorder.write_simulation_clock_anchor(nominal_origin="2026-01-01T00:00:00+00:00", monotonic_origin_seconds=123)
    record = recorder._identity_queue.get_nowait()
    assert record["record_type"] == "simulation_clock_anchor"
    assert record["monotonic_origin_ns"] == 123_000_000_000
