from __future__ import annotations

from collections import Counter
from datetime import timedelta
from types import SimpleNamespace

import pytest

from examples.mini_satellite_internet import main as reference_main
from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.simu import (
    create_mini_satellite_internet_simulator,
    prepare_mini_satellite_internet_planning,
)
from skyseed_v2.backend.bpf import BpfCompiledRealization
from skyseed_v2 import _generated_bpf_execution as bpf_bootstrap
from skyseed_v2.simulator import Plan
from skyseed_v2.backend.bpf.runner import _resolve_nominal_boundaries
from skyseed_v2.backend.bpf._runner.runtime_realization import (
    _TcBpfRuntimeRealization,
)


def _canonical_compiled_chain() -> tuple[
    tuple[Plan, ...], tuple[BpfCompiledRealization, ...]
]:
    emulator = build_mini_satellite_internet_emulator()
    simulator = create_mini_satellite_internet_simulator(emulator)
    slicer = simulator.get_slicer()
    slicer.create_all_slices()
    _, plans, _ = prepare_mini_satellite_internet_planning(simulator)
    return plans, reference_main._compile_bpf_realizations(slicer, plans)


def test_canonical_compiler_output_is_sufficient_for_runner_preparation() -> None:
    plans, compiled = _canonical_compiled_chain()
    first = compiled[0]
    bindings = {
        node_id: SimpleNamespace(pid=index + 100, netns_inode=index + 200)
        for index, node_id in enumerate(first._topology_signature.node_ids)
    }
    class _Executor:
        def execute(self, action, request):
            if action == "audit_node_addresses":
                return {
                    "result": "passed",
                    "selected_node_address_range": request["node_address_range"],
                    "allocated_node_addresses": len(request["nodes"]),
                    "compiled_zone_prefixes": len(request["zone_prefixes"]),
                    "audited_node_namespaces": len(request["nodes"]),
                    "observed_ipv4_address_prefixes": len(request["nodes"]),
                    "observed_non_default_ipv4_route_prefixes": 0,
                    "audited_sources": [],
                }
            if action == "initialize":
                return {
                    "shared_active_generation_map": True,
                    "collision_audit": {
                        "result": "passed",
                        "selected_node_address_range": request[
                            "node_address_range"
                        ],
                        "allocated_node_addresses": len(request["nodes"]),
                        "compiled_zone_prefixes": len(request["zone_prefixes"]),
                        "audited_node_namespaces": len(request["nodes"]),
                        "observed_ipv4_address_prefixes": len(request["nodes"]),
                        "observed_non_default_ipv4_route_prefixes": 0,
                        "audited_sources": [],
                    },
                    "installed_node_addresses": sorted(
                        node["key"] for node in request["nodes"]
                    ),
                }
            if action == "prepare_boundary":
                return {
                    "generation_keys": [
                        {
                            "key": node["key"],
                            "active_interface_indexes": list(
                                range(len(node["active_interfaces"]))
                            ),
                            "route_keys": list(range(len(node["routes"]))),
                        }
                        for node in request["generation_nodes"]
                    ]
                }
            return {"shared_active_generation_map": True}

    dataplane = _TcBpfRuntimeRealization(
        executor=_Executor(),
        pin_root="/sys/fs/bpf/skyseed-v2/run-canonical",
        bindings=bindings,
    )
    boundaries = _resolve_nominal_boundaries(compiled)
    first_boundaries = _resolve_nominal_boundaries(compiled[:1])
    first_two_boundaries = _resolve_nominal_boundaries(compiled[:2])
    assert first_two_boundaries[: len(first_boundaries)] == first_boundaries
    assert first_boundaries[0].inspected_constraint_count == 790
    assert first_two_boundaries[0].inspected_constraint_count == 802
    assert first_boundaries[0].later_constraint_count == 63
    assert first_two_boundaries[0].later_constraint_count == 75
    capacity = dataplane.initialize(
        first._topology_signature,
        zone_prefixes=tuple(prefix for _, prefix in first.zone_prefixes),
        capacity_boundaries=boundaries,
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )["capacity_audit"]
    assert capacity["node_address_requirement"] == len(bindings)
    assert capacity["node_address_capacity"] >= len(bindings)
    assert capacity["node_address_headroom"] >= 0
    assert capacity["selected_node_address_range"] is not None
    assert capacity["collision_audit"]["result"] == "passed"
    prepared = []
    predecessor = None
    for boundary in boundaries:
        pending, _ = dataplane.start_preparation(boundary)
        ready, _ = dataplane.finish_preparation(
            pending,
            foundation_token="canonical-foundation",
            predecessor_time=predecessor,
        )
        prepared.append(ready)
        predecessor = boundary.nominal_time
    boundary = boundaries[0]
    request = prepared[0].preparation_request

    assert [(value.start_slice_id, value.end_slice_id) for value in compiled] == [
        (0, 4),
        (4, 8),
        (8, 12),
        (12, 16),
        (16, 20),
        (20, 24),
        (24, 28),
        (28, 32),
        (32, 36),
        (36, 40),
        (40, 44),
        (44, 48),
        (48, 52),
        (52, 56),
        (56, 60),
        (60, 64),
    ]
    assert len(boundaries) == 75
    assert Counter(
        int((current.nominal_time - previous.nominal_time).total_seconds())
        for previous, current in zip(boundaries, boundaries[1:])
    ) == Counter({10: 52, 5: 22})
    assert boundaries[-1].nominal_time.isoformat() == (
        "2026-01-01T00:11:00+00:00"
    )
    assert Counter(
        intent.operation.value
        for value in compiled
        for intent in value.resource_realizations
    ) == Counter({"establish": 62, "withdraw": 14})
    assert sum(len(value.parameter_realizations) for value in compiled) == 1714
    assert sum(len(value.forwarding_realizations) for value in compiled) == 708
    assert sum(len(value.zone_edge_realizations) for value in compiled) == 12
    assert capacity["mode"] == "bounded-full-range"
    assert capacity["known_generation_count"] == len(boundaries)
    assert capacity["resident_generation_capacity"] == len(boundaries)
    assert all(
        value["configured_active_link_entries"]
        == value["required_active_link_entries"]
        and value["configured_route_entries"] == value["required_route_entries"]
        for value in capacity["per_node"]
    )
    assert len(request["nodes"]) == 35
    assert boundary.nominal_time.isoformat() == "2026-01-01T00:00:30+00:00"
    assert dict(boundary.operation_counts) == {
        "resource": 51,
        "parameter": 102,
        "forwarding": 163,
        "zone-edge": 3,
    }
    assert boundary.inspected_constraint_count == 1631
    assert boundary.source_constraint_count == 790
    assert boundary.applied_constraint_count == 677
    assert boundary.outgoing_constraint_count == 50
    assert dict(boundary.constraint_reasons) == {
        "forwarding-ready-before-zone-activation": 99,
        "plan-forwarding-precedence": 150,
        "qdisc-ready-before-forwarding": 163,
        "resource-ready-before-forwarding": 163,
        "resource-ready-before-qdisc": 102,
    }
    assert len(first.intended_resources) == 53
    assert len(first.intended_forwarding) == 198
    assert [len(value.prepared_resources) for value in prepared[:4]] == [
        51,
        1,
        0,
        1,
    ]
    assert [len(value.state.resources) for value in prepared[:4]] == [
        51,
        52,
        52,
        53,
    ]
    assert [len(value.state.forwarding) for value in prepared[:4]] == [
        163,
        163,
        163,
        163,
    ]
    assert [value.generation for value in prepared[:4]] == [0, 1, 2, 3]
    assert [value.expected_active_generation for value in prepared[:4]] == [
        0xFFFFFFFF,
        0,
        1,
        2,
    ]
    assert [
        sum(len(node["routes"]) for node in value.preparation_request["generation_nodes"])
        for value in prepared[:4]
    ] == [201, 201, 201, 201]
    assert [
        sum(len(node["qdisc"]) for node in value.activation_request["nodes"])
        for value in prepared[:4]
    ] == [0, 0, 16, 0]
    assert [
        sum(len(node["link_down"]) for node in value.activation_request["nodes"])
        for value in prepared[:4]
    ] == [0, 0, 0, 0]
    assert [
        value.activation_request["folded_forwarding_constraints"]
        for value in prepared[:4]
    ] == [150, 0, 0, 0]
    assert [
        sum(
            len(node["access_peer_route_replace"])
            for node in value.activation_request["nodes"]
        )
        for value in prepared[:4]
    ] == [3, 1, 0, 1]
    route_deletes = [
        route
        for value in prepared
        for node in value.activation_request["nodes"]
        for route in node["route_delete"]
    ]
    assert route_deletes
    assert all(route["destination"] and route["interface"] for route in route_deletes)


def test_standard_generated_execution_owns_lifecycle_and_detached_tool_handoff(
    monkeypatch,
    tmp_path,
) -> None:
    calls = []
    output = tmp_path / "output"

    class _Slicer:
        def _enable_durable_history(self, path):
            calls.append(("slice-history", path))

    class _Planner:
        def _enable_durable_history(self, path):
            calls.append(("planning-history", path))

    class _Runner:
        def __init__(self, **facts):
            calls.append(("runner", facts))

        def _enable_event_history(self, path):
            calls.append(("event-history", path))

        def _enable_traffic_telemetry(self, directory):
            calls.append(("traffic-telemetry", directory))

        def prepare(self):
            calls.append("prepare")

        def run(self):
            calls.append("run")

        def cleanup(self):
            calls.append("cleanup")

        def inspection_snapshot(self):
            calls.append("inspection_snapshot")
            return {"format_version": 1, "runner": {}, "events": ()}

    monkeypatch.setattr(
        bpf_bootstrap,
        "_read_and_verify_execution_definition",
        lambda _output: {
            "simulation": {"generation_lookahead_seconds": 1},
            "runner": {
                "preparation_lookahead_seconds": 1,
                "preparation_boundary_depth": 1,
            },
            "generated_output_identity": "standard-execution",
        },
    )
    monkeypatch.setattr(
        bpf_bootstrap,
        "_construct_progression",
        lambda _output, _definition: (_Slicer(), _Planner()),
    )
    monkeypatch.setattr(
        bpf_bootstrap,
        "_RollingRealizationSupplier",
        lambda *_args, **kwargs: calls.append(("supplier", kwargs))
        or (lambda: None),
    )
    monkeypatch.setattr(bpf_bootstrap, "BpfRunner", _Runner)
    monkeypatch.setattr(
        bpf_bootstrap,
        "_write_runner_inspection_handoff",
        lambda path, snapshot, outcome: calls.append(
            ("inspection-handoff", path, snapshot, outcome)
        ),
    )

    bpf_bootstrap._run_generated_bpf_experiment(output)

    assert "prepare" in calls
    assert calls.index("prepare") < calls.index("run") < calls.index("cleanup")
    supplier = next(value for value in calls if value[0] == "supplier")
    assert set(supplier[1]) == {"generation_lookahead", "compiled_history_path"}
    handoff = next(value for value in calls if value[0] == "inspection-handoff")
    assert handoff[1] == output / "inspection/runner/runner-invocation.json"
    assert handoff[3]["status"] == "completed"
    assert not any("traffic_workload" in repr(value) for value in calls)


@pytest.mark.parametrize(
    (
        "primary_kind",
        "cleanup_fails",
        "handoff_fails",
        "expected_type",
        "expected_message",
        "expected_status",
    ),
    (
        ("interrupt", False, False, KeyboardInterrupt, "^$", "interrupted"),
        ("runtime", True, True, RuntimeError, "primary runtime", "failed"),
        (None, True, False, RuntimeError, "cleanup failed", "cleanup-failed"),
        (
            None,
            False,
            True,
            RuntimeError,
            "inspection handoff failed",
            "completed",
        ),
    ),
)
def test_generated_execution_handoff_preserves_error_precedence(
    monkeypatch,
    tmp_path,
    capsys,
    primary_kind,
    cleanup_fails,
    handoff_fails,
    expected_type,
    expected_message,
    expected_status,
) -> None:
    output = tmp_path / "output"
    captured = []

    class _Slicer:
        def _enable_durable_history(self, _path):
            pass

    class _Planner:
        def _enable_durable_history(self, _path):
            pass

    class _Runner:
        def __init__(self, **_facts):
            pass

        def _enable_event_history(self, _path):
            pass

        def _enable_traffic_telemetry(self, _directory):
            pass

        def prepare(self):
            pass

        def run(self):
            if primary_kind == "interrupt":
                raise KeyboardInterrupt
            if primary_kind == "runtime":
                raise RuntimeError("primary runtime")

        def cleanup(self):
            if cleanup_fails:
                raise RuntimeError("cleanup failed")

        def inspection_snapshot(self):
            return {"format_version": 1, "runner": {}, "events": ()}

    def write_handoff(path, snapshot, outcome):
        captured.append((path, snapshot, outcome))
        if handoff_fails:
            raise RuntimeError("inspection handoff failed")

    monkeypatch.setattr(
        bpf_bootstrap,
        "_read_and_verify_execution_definition",
        lambda _output: {
            "simulation": {"generation_lookahead_seconds": 1},
            "runner": {
                "preparation_lookahead_seconds": 1,
                "preparation_boundary_depth": 1,
            },
            "generated_output_identity": "mini-finalization",
        },
    )
    monkeypatch.setattr(
        bpf_bootstrap,
        "_construct_progression",
        lambda _output, _definition: (_Slicer(), _Planner()),
    )
    monkeypatch.setattr(
        bpf_bootstrap,
        "_RollingRealizationSupplier",
        lambda *_args, **_kwargs: (lambda: None),
    )
    monkeypatch.setattr(bpf_bootstrap, "BpfRunner", _Runner)
    monkeypatch.setattr(
        bpf_bootstrap,
        "_write_runner_inspection_handoff",
        write_handoff,
    )

    with pytest.raises(expected_type, match=expected_message):
        bpf_bootstrap._run_generated_bpf_experiment(output)

    assert captured[0][2]["status"] == expected_status
    if primary_kind is not None and handoff_fails:
        assert "without replacing the runtime error" in capsys.readouterr().err
