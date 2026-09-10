from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path

import pytest

from tools.runner import collect_runner_data, write_runner_tool
from tools.runner.__main__ import main as runner_tool_main


def _event(name: str, when: float, **facts) -> dict[str, object]:
    return {
        "event": name,
        "monotonic_seconds": when,
        "wall_time_seconds": 1_700_000_000 + when,
        **facts,
    }


def _snapshot() -> dict[str, object]:
    boundary = "2026-01-01T00:00:00+00:00"
    events = (
        _event(
            "preparation_start", 10.0,
            execution_contract="sealed-precomputed-only",
        ),
        _event(
            "runtime_foundation_ready",
            11.0,
            shared_active_generation_map=True,
            capacity_high_water=7,
        ),
        _event(
            "ready_published",
            13.5,
            boundary_timestamp=boundary,
            generation=0,
            predecessor_time=None,
            preparation_seconds=1.0,
            target_active_interface_entries=7,
            target_route_entries=3,
            generation_map_update_operations=10,
            boundary_decode_seconds=0.02,
            draft_timings={
                "draft_state_materialization_seconds": 0.03,
                "derived_view_build_seconds": 0.04,
                "symbolic_request_grouping_seconds": 0.01,
                "draft_total_seconds": 0.08,
            },
            draft_completed_monotonic_seconds=12.0,
            post_main_finalize_seconds=0.4,
            privileged_preparation_seconds=0.3,
            preparation_result_validation_seconds=0.04,
            public_ready_publication_seconds=0.01,
            parent_helper_envelope={"request_json_bytes": 1234},
            execution_metrics={
                "external_processes": 2,
                "namespace_processes": 2,
                "bpf_syscalls": 10,
                "bpf_map_update_syscalls": 10,
                "worker_limit": 2,
                "batch_sizes": [3, 7],
            },
            expected_active_generation=0xFFFFFFFF,
            prepared_resources=4,
        ),
        _event(
            "capacity_audit_end",
            13.0,
            scope="sealed-artifact-exact-demand",
            capacity={"generation": 0, "headroom": 2},
        ),
        _event(
            "preparation_end",
            14.0,
            duration_seconds=4.0,
            ready_frontier=boundary,
            active_frontier=None,
        ),
        _event("automatic_run_start", 15.0, nominal_origin=boundary),
        _event(
            "boundary_scheduled",
            16.0,
            boundary_timestamp=boundary,
            actual_readiness_wait_seconds=0.0,
            ready_publication_lead_seconds=2.5,
            ready_publication_lag_seconds=0.0,
        ),
        _event(
            "activation_end",
            16.2,
            boundary_timestamp=boundary,
            main_seconds=0.2,
            ideal_start_lag_seconds=0.001,
            ideal_completion_lag_seconds=0.201,
            active_frontier=boundary,
            ready_frontier=None,
            active_generation=0,
            foundation_validation_succeeded=True,
            foundation_validation_seconds=0.03,
            foundation_validation_timing_complete=True,
            foundation_validation_timing_status="complete",
            foundation_validation_scope="privileged-child-preflight",
            foundation_validation_included_in_runtime_apply=True,
            runtime_apply_seconds=0.1,
            activation_switch_seconds=0.001,
            retirement_seconds=0.01,
            bookkeeping_seconds=0.02,
            grouping={
                "grouped_operation_count": 4,
                "affected_node_count": 1,
            },
            activation_phases={
                "pre_publication": {
                    "duration_seconds": 0.03,
                    "submitted_task_count": 1,
                    "nonempty_task_count": 1,
                }
            },
            activation_phase_reconciliation={"counters_complete": True},
            parent_main_tail={"outer_ledger_update_seconds": 0.001},
            parent_helper_envelope={
                "request_json_bytes": 4321,
                "response_json_bytes": 2345,
            },
        ),
        _event(
            "backend_execution_ended",
            16.99,
            execution_class="generation-reclaim",
            admitted_service_seconds=0.05,
            failed=False,
        ),
        _event(
            "generation_reclaimed",
            17.0,
            generation=0,
            deleted_entries=7,
        ),
        _event("automatic_run_end", 18.0, active_frontier=boundary),
        _event("private_detail_only", 18.5, retained=True),
        _event(
            "runtime_cleanup_end",
            19.5,
            pin_root_removed=True,
            errors=[],
        ),
        _event("cleanup_end", 20.0, duration_seconds=1.0),
    )
    return {
        "format_version": 2,
        "runner": {
            "lifecycle_state": "cleaned",
            "execution_mode": "automatic",
            "execution_contract": "sealed-precomputed-only",
            "frontiers": {
                "last_active": boundary,
                "artifact_end": boundary,
            },
            "artifact": {
            "artifact_type": "skyseed-v2-runner-runtime-artifact",
                "format_version": 2,
                "encoding": "typed-packed-runtime-tables-v3",
                "boundary_count": 1,
                "rich_compiled_realizations_reconstructed": False,
            },
            "activation_count": 1,
            "runtime_foundation": {
                "compose_project_name": "runner-tool-test",
                "foundation_token": "foundation-test-token",
                "expected_node_count": 1,
                "bound_node_count": 1,
                "bindings": (
                    {
                        "node_id": "65001/satellite/sat-1",
                        "service": "sat-1",
                        "container_id": "container-id-1",
                        "container_name": "as65001ext-sat-1",
                        "image_id": "sha256:image-1",
                        "pid": 1234,
                        "netns_inode": 5678,
                        "started_at": "2026-01-01T00:00:00Z",
                    },
                ),
            },
        },
        "events": events,
    }


def _outcome(
    status: str = "completed",
    *,
    primary_error: dict[str, str] | None = None,
    cleanup_status: str = "completed",
) -> dict[str, object]:
    return {
        "status": status,
        "exit_code": 0 if status == "completed" else 130 if status == "interrupted" else 1,
        "primary_error": primary_error,
        "cleanup": {
            "status": cleanup_status,
            "error": (
                None
                if cleanup_status == "completed"
                else {"type": "RuntimeError", "message": "cleanup residue"}
            ),
        },
    }


def test_reclamation_summary_counts_compacted_ids_and_preserves_history() -> None:
    snapshot = _snapshot()
    snapshot["events"] += (
        _event("generation_reclaim", 16.3, reclaimed_generations=(1, 2), work_units=4, deleted_entries=2),
        _event("generation_reclaim", 16.4,
               reclaimed_generations={"item_count": 80, "detail": "omitted-from-inline-evidence"},
               work_units=240, deleted_entries=160),
        _event("batch_plan_reclaimed", 16.5, released_boundaries=60),
    )
    data = collect_runner_data(snapshot, _outcome())
    assert data["delivery"]["reclamation"] == {
        "completed_generations": 82, "work_units": 244,
        "deleted_map_entries": 162, "released_plan_boundaries": 60,
    }
    assert "80" in json.dumps(data["timeline"])
    assert snapshot["events"][-2]["reclaimed_generations"]["item_count"] == 80


def test_projection_curates_complete_trace_without_runner_side_indexes() -> None:
    data = collect_runner_data(_snapshot(), _outcome())

    assert data["overview"]["lifecycle_state"] == "cleaned"
    assert data["overview"]["frontiers"]["last_active"] == (
        "2026-01-01T00:00:00+00:00"
    )
    assert data["overview"]["execution_contract"] == "sealed-precomputed-only"
    assert data["overview"]["artifact"]["boundary_count"] == 1
    assert data["scale_safety"]["batching"] == {
        "observed_batch_count": 2,
        "batch_sizes": (3, 7),
        "batch_size_distribution": {},
        "minimum_batch_size": 3,
        "maximum_batch_size": 7,
        "external_processes": 2,
        "namespace_processes": 2,
        "bpf_syscalls": 10,
    }
    assert data["scale_safety"]["main_subphases"][0]["main_seconds"] == 0.2
    assert data["scale_safety"]["main_subphases"][0][
        "activation_phase_reconciliation"
    ]["counters_complete"] is True
    assert data["scale_safety"]["main_subphases"][0]["activation_phases"][
        "pre_publication"
    ]["submitted_task_count"] == 1
    assert data["scale_safety"]["main_subphases"][0][
        "parent_helper_envelope"
    ]["request_json_bytes"] == 4321
    assert data["timing"]["activations"] == (
        {
            "boundary_timestamp": "2026-01-01T00:00:00+00:00",
            "start_lag_seconds": 0.001,
            "duration_seconds": 0.2,
            "completion_lag_seconds": 0.201,
            "completed": True,
            "active_frontier": "2026-01-01T00:00:00+00:00",
        },
    )
    assert [
        value["event"]
        for value in data["timeline"]
        if value["monotonic_seconds"] == 16.0
    ] == ["boundary_scheduled"]
    assert "private_detail_only" not in {
        value["event"] for value in data["timeline"]
    }
    assert data["raw"]["events"]["embedded"] is False
    assert data["raw"]["events"]["count"] == len(_snapshot()["events"])
    assert data["default_dataplane"]["kind"] == "default-tc-bpf"
    assert data["default_dataplane"]["fresh_kernel_inspection"] is False


def test_projection_does_not_fabricate_missing_current_main_timing() -> None:
    snapshot = _snapshot()
    activation = next(
        value
        for value in snapshot["events"]
        if value["event"] == "activation_end"
    )
    activation["main_seconds"] = None

    data = collect_runner_data(snapshot, _outcome())

    assert data["timing"]["activations"][0]["duration_seconds"] is None
    assert data["timing"]["recent_activation_duration_seconds"] is None
    assert data["timing"]["max_activation_duration_seconds"] is None
    assert data["scale_safety"]["main_subphases"][0]["main_seconds"] is None




def test_projection_preserves_incomplete_post_publication_failure_truth() -> None:
    snapshot = _snapshot()
    snapshot["events"] = (*snapshot["events"], {
        "event": "failure",
        "phase": "activation",
        "target_boundary": "2026-01-01T00:00:10+00:00",
        "last_completed_active_frontier": "2026-01-01T00:00:00+00:00",
        "active_frontier": "2026-01-01T00:00:00+00:00",
        "lifecycle_state": "failed",
        "semantic_outcome": "target-transition-incomplete",
        "live_mutation_started": True,
        "generation_publication_occurred": True,
        "target_transition_complete": False,
        "live_state_may_be_partial": True,
        "failure_phase": "withdrawn-veth-deletion",
        "failure_type": "RuntimeError",
        "failure_message": "delete failed",
        "candidate_rollback_attempted": False,
        "candidate_rollback_complete": None,
        "deleted_connections_complete": False,
        "cleanup_ownership_retained": True,
        "cleanup_required": True,
        "cleanup_connections": [{"connection": "owned-residue"}],
    })
    projected = collect_runner_data(
        snapshot, _outcome()
    )["scale_safety"]["main_subphases"][-1]
    assert projected["generation_publication_occurred"] is True
    assert projected["target_transition_complete"] is False
    assert projected["active_frontier"] == "2026-01-01T00:00:00+00:00"
    assert projected["semantic_outcome"] == "target-transition-incomplete"
    assert projected["live_state_may_be_partial"] is True
    assert projected["deleted_connections_complete"] is False
    assert projected["cleanup_connections"] == [
        {"connection": "owned-residue"}
    ]


def test_projection_detaches_runtime_foundation_scalar_bindings() -> None:
    snapshot = _snapshot()
    data = collect_runner_data(snapshot, _outcome())

    assert data["runtime_foundation"] == {
        "compose_project_name": "runner-tool-test",
        "foundation_token": "foundation-test-token",
        "expected_node_count": 1,
        "bound_node_count": 1,
        "bindings": (
            {
                "node_id": "65001/satellite/sat-1",
                "service": "sat-1",
                "container_id": "container-id-1",
                "container_name": "as65001ext-sat-1",
                "image_id": "sha256:image-1",
                "pid": 1234,
                "netns_inode": 5678,
                "started_at": "2026-01-01T00:00:00Z",
            },
        ),
    }
    snapshot["runner"]["runtime_foundation"]["bindings"][0]["pid"] = 0
    assert data["runtime_foundation"]["bindings"][0]["pid"] == 1234


def test_graphical_timeline_uses_elapsed_time_and_nominal_boundaries() -> None:
    data = collect_runner_data(_snapshot(), _outcome())
    graph = data["graphical_timeline"]
    groups = {
        group["group"]: {
            row["row"]: row["items"] for row in group["rows"]
        }
        for group in graph["groups"]
    }

    assert graph["time_basis"] == (
        "elapsed monotonic seconds from first recorded event"
    )
    assert graph["duration_seconds"] == 10.0
    preparation = groups["lifecycle"]["preparation_phase"][0]
    assert preparation["visual"] == "phase"
    assert preparation["elapsed_start_seconds"] == 0.0
    assert preparation["elapsed_end_seconds"] == 4.0
    assert preparation["foundation_marker"] == {
        "kind": "runtime_foundation_ready",
        "elapsed_seconds": 1.0,
        "boundary_timestamp": None,
        "target_timeline_index": 1,
        "generation": None,
    }
    automatic = groups["lifecycle"]["automatic_run_phase"][0]
    assert automatic["elapsed_start_seconds"] == 5.0
    assert automatic["elapsed_end_seconds"] == 8.0
    assert automatic["completed"] is True
    assert automatic["target_timeline_index"] == 3
    assert set(groups) == {"lifecycle", "work"}
    assert not any(
        value["kind"] == "automatic_run_start"
        for group in groups.values()
        for values in group.values()
        for value in values
    )
    activation = groups["work"]["main_activation"][0]
    assert activation["elapsed_start_seconds"] == pytest.approx(6.0)
    assert activation["elapsed_end_seconds"] == pytest.approx(6.2)
    assert activation["boundary_timestamp"] == "2026-01-01T00:00:00+00:00"
    cleanup = groups["lifecycle"]["cleanup_phase"][0]
    assert cleanup["elapsed_start_seconds"] == 9.0
    assert cleanup["elapsed_end_seconds"] == 10.0


def test_interrupted_automatic_phase_extends_to_cleanup_without_fake_work() -> None:
    snapshot = _snapshot()
    snapshot["events"] = tuple(
        event
        for event in snapshot["events"]
        if event["event"] != "automatic_run_end"
    )

    graph = collect_runner_data(
        snapshot,
        _outcome(
            "interrupted",
            primary_error={"type": "KeyboardInterrupt", "message": ""},
        ),
    )["graphical_timeline"]
    lifecycle = next(
        group for group in graph["groups"] if group["group"] == "lifecycle"
    )
    automatic = next(
        row
        for row in lifecycle["rows"]
        if row["row"] == "automatic_run_phase"
    )["items"][0]

    assert automatic["elapsed_start_seconds"] == 5.0
    assert automatic["elapsed_end_seconds"] == 9.0
    assert automatic["completed"] is False
    assert automatic["visual"] == "phase"


@pytest.mark.parametrize(
    ("status", "primary_error", "cleanup_status"),
    (
        ("interrupted", {"type": "KeyboardInterrupt", "message": ""}, "completed"),
        ("failed", {"type": "RuntimeError", "message": "runtime failed"}, "completed"),
        ("cleanup-failed", None, "failed"),
    ),
)
def test_projection_keeps_invocation_outcome_separate_from_runner_lifecycle(
    status,
    primary_error,
    cleanup_status,
) -> None:
    data = collect_runner_data(
        _snapshot(),
        _outcome(
            status,
            primary_error=primary_error,
            cleanup_status=cleanup_status,
        ),
    )

    assert data["overview"]["outcome"] == status
    assert data["overview"]["lifecycle_state"] == "cleaned"
    assert data["failures_cleanup"]["primary_error"] == primary_error
    assert data["failures_cleanup"]["cleanup"]["status"] == cleanup_status


def test_artifact_bundle_is_standalone_complete_and_checksummed(
    tmp_path: Path,
) -> None:
    result = write_runner_tool(_snapshot(), _outcome(), tmp_path)

    assert result["status"] == "COMPLETED"
    assert set(result["files"]) == {
        "index.html",
        "runner-inspection.json",
        "summary.txt",
        "inventory.json",
        "checksums.sha256",
    }
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    for required in (
        "BPF Runner Inspection Tool",
        "Last active frontier",
        "Current default tc-BPF dataplane",
        "Traffic telemetry",
        "Raw detached Runner snapshot and event history",
        "BPF Runner 检查工具",
        "图形执行时间轴",
        "如何阅读 / How to read",
        "Runtime foundation / 运行期绑定",
        "Frozen node bindings",
        "Runner artifact",
        "Preparation completes with boundary 0 active",
        'data-language="zh"',
        'data-language="en"',
        'id="graphical-timeline"',
        'id="timeline"',
    ):
        assert required in page
    for forbidden in ("fetch(", "WebSocket", "EventSource", "http://", "https://"):
        assert forbidden not in page
    for retired_runner_event in (
        "maintenance_prepare_start",
        "maintenance_prepare_end",
        "activation_start",
        "cleanup_start",
    ):
        assert retired_runner_event not in page
    data = json.loads(
        (tmp_path / "runner-inspection.json").read_text(encoding="utf-8")
    )
    assert data["inventory"]["events"] == len(_snapshot()["events"])
    for line in (tmp_path / "checksums.sha256").read_text(
        encoding="utf-8"
    ).splitlines():
        expected, relative = line.split("  ", maxsplit=1)
        assert sha256((tmp_path / relative).read_bytes()).hexdigest() == expected


def test_tool_reads_complete_durable_history_without_runner_materialization(
    tmp_path: Path,
) -> None:
    events = tuple(
        _event("synthetic_progress", float(index), generated_index=index)
        for index in range(700)
    )
    history_path = tmp_path / "runner-events.jsonl"
    records = (
        {
            "record_type": "history_header",
            "format_version": 1,
            "owner": "BpfRunner",
        },
        *(
            {"record_type": "runner_event", **event}
            for event in events
        ),
    )
    history_path.write_text(
        "".join(json.dumps(value, sort_keys=True) + "\n" for value in records),
        encoding="utf-8",
    )
    snapshot = _snapshot()
    snapshot["events"] = events[-512:]
    snapshot["event_history"] = {
        "scope": "recent-only",
        "returned_count": 512,
        "total_count": 700,
        "durable_history_enabled": True,
    }

    data = collect_runner_data(
        snapshot,
        _outcome(),
        event_history_path=history_path,
    )

    assert data["inventory"]["events"] == 700
    assert data["raw"]["event_history"]["scope"] == "complete"
    assert data["raw"]["events"] == {
        "embedded": False,
        "count": 700,
        "exact_source_path": str(history_path.resolve()),
        "note": (
            "Exact events remain in the durable JSONL source; this page "
            "does not duplicate the complete raw history."
        ),
    }


def test_tool_rejects_incomplete_bounded_event_history(tmp_path: Path) -> None:
    history_path = tmp_path / "runner-events.jsonl"
    history_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "record_type": "history_header",
                        "format_version": 2,
                        "owner": "BpfRunner",
                        "capture": "bounded-nonblocking",
                    }
                ),
                json.dumps(
                    {
                        "record_type": "runner_event",
                        **_event("only-event", 1.0),
                    }
                ),
                "",
            )
        ),
        encoding="utf-8",
    )
    snapshot = _snapshot()
    snapshot["event_history"] = {
        "scope": "recent-only",
        "returned_count": 1,
        "total_count": 2,
        "durable_history_enabled": True,
        "durable": {
            "complete": False,
            "overflowed": True,
            "dropped_count": 1,
        },
    }

    with pytest.raises(ValueError, match="incomplete or overflowed"):
        collect_runner_data(
            snapshot,
            _outcome(),
            event_history_path=history_path,
        )


def test_tool_keeps_compose_startup_separate_from_runner_preparation(
    tmp_path: Path,
) -> None:
    startup = tmp_path / "deployment-startup.json"
    startup.write_text(
        json.dumps(
            {
                "artifact_type": "compose-startup-evidence",
                "format_version": 1,
                "duration_seconds": 12.5,
                "exit_code": 0,
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )

    data = collect_runner_data(
        _snapshot(),
        _outcome(),
        deployment_startup_path=startup,
    )

    assert data["scale_safety"]["deployment_startup"] == {
        "artifact_type": "compose-startup-evidence",
        "format_version": 1,
        "duration_seconds": 12.5,
        "exit_code": 0,
        "status": "completed",
    }


def test_tool_owned_cli_consumes_detached_generated_execution_handoff(
    monkeypatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    handoff_path = tmp_path / "runner-invocation.json"
    history_path = tmp_path / "runner-events.jsonl"
    output = tmp_path / "output"
    handoff_path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "artifact_type": "runner-inspection-handoff",
                "runner_snapshot": snapshot,
                "runtime_outcome": _outcome(),
            }
        ),
        encoding="utf-8",
    )
    history_path.write_text(
        json.dumps(
            {
                "record_type": "history_header",
                "format_version": 1,
                "owner": "BpfRunner",
            }
        )
        + "\n"
        + "".join(
            json.dumps({"record_type": "runner_event", **event}) + "\n"
            for event in snapshot["events"]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "tools.runner",
            "--handoff",
            str(handoff_path),
            "--events",
            str(history_path),
            "--output",
            str(output),
        ],
    )

    assert runner_tool_main() == 0
    assert (output / "runner-inspection.json").is_file()
    assert (output / "index.html").is_file()


def test_bilingual_renderer_reuses_one_evidence_dataset_without_raw_translation(
    tmp_path: Path,
) -> None:
    projected = collect_runner_data(_snapshot(), _outcome())
    write_runner_tool(_snapshot(), _outcome(), tmp_path)
    page = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert page.count("const DATA=") == 1
    embedded = page.split("const DATA=", maxsplit=1)[1].split(
        ";\nconst STRINGS=", maxsplit=1
    )[0]
    assert json.loads(embedded) == json.loads(json.dumps(projected))
    assert "document.documentElement.lang=s().lang" in page
    assert "elapsed_start_seconds" in page
    assert "target_timeline_index" in page
    assert "focusTimelineItem" in page
    assert "graph.groups.map" in page
    assert "graph-bar" in page
    assert "graph-point" in page
    assert "graph-milestone" not in page
    assert "milestone-row" not in page
    assert "ready-link" not in page
    assert "writing-mode" not in page
    assert "itemIndex%3" not in page
    assert "Detailed Timeline events" in page
    assert "详细 Timeline 事件" in page
    assert "Start lag / Completion lag" in page
    assert "current default tc-BPF dataplane" in page
    ordered_sections = (
        'id="overview-title"',
        'id="timeline-title"',
        'id="foundation-title"',
        'id="timing-title"',
        'id="dataplane-title"',
        'id="failures-title"',
        'id="detailed-timeline-title"',
        'id="boundaries-title"',
        'id="raw-title"',
    )
    assert [page.index(marker) for marker in ordered_sections] == sorted(
        page.index(marker) for marker in ordered_sections
    )
    assert "Automatic scheduling/waiting lifecycle phase" in page
    assert "Lifecycle 横条表示 Runner 当时处于什么阶段" in page
    assert "work 在全局比例下可能只是一条细线" in page
    assert "切换当前网络状态" in page
    assert "Cyan end-cap" not in page
    assert "青色 end-cap" not in page
    assert "amber notch" not in page
    assert "黄色 notch" not in page
    assert 'id="graph-local-detail"' in page
    assert "pinnedGraphDetail" in page
    assert 'addEventListener("mouseenter"' in page
    assert 'data-local-event' in page
    assert "transform:translateX(-14px)" in page
    assert "graph-foundation-notch" in page
    assert "classifyBinding" in page
    assert "dynamic-shell-orbit-" in page
    assert 'return {group:"other"' in page
    assert "Ground Stations" in page
    assert "Dynamic Shell" in page
    assert "Other nodes" in page
    assert "foundation-test-token" in page
    assert "container-id-1" in page
    assert 'document.getElementById("raw-json").textContent=pretty(DATA.raw)' in page
    assert "private_detail_only" not in page
    assert "does not duplicate the complete raw history" in page


def test_tool_has_no_live_runner_kernel_or_controller_dependency() -> None:
    tool_root = Path(__file__).resolve().parents[2] / "tools/runner"
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in tool_root.glob("*.py")
    }
    combined = "\n".join(sources.values())
    assert "_TcBpfRuntimeRealization" not in combined
    assert "skyseed_v2.backend" not in combined
    for path, source in sources.items():
        tree = ast.parse(source)
        imported = {
            node.names[0].name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
        }
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        assert "subprocess" not in imported, path
        assert not (
            called_attributes
            & {"prepare", "run", "activate_next", "cleanup", "execute"}
        ), path


def test_detailed_timing_keeps_inclusive_envelopes_out_of_exclusive_main_accounting():
    from tools.runner.projection import _detailed_timing
    event = _event("activation_end", 110, boundary_timestamp="2026-01-01T00:00:05+00:00",
        preparation=False, detailed_timing_version=1, main_seconds=10.0,
        main_start_monotonic_seconds=100.0, main_end_monotonic_seconds=110.0,
        helper_pacing_anchor_monotonic_ns=102_000_000_000,
        helper_activation_start_monotonic_ns=102_000_000_000,
        helper_activation_evidence_monotonic_ns=107_800_000_000,
        generation_publication_occurred=True,
        command_timing={"command_seconds":8.0,"json_encode_seconds":.5,"channel_write_seconds":.5,
            "channel_wait_seconds":6.5,"response_decode_seconds":.5,
            "write_start_monotonic_ns":101_000_000_000,"read_end_monotonic_ns":108_000_000_000,
            "helper":{"request_decode_seconds":.2,"dispatch_seconds":6.0,
                "request_decoded_monotonic_ns":101_900_000_000,"dispatch_start_monotonic_ns":102_000_000_000}},
        parent_runtime_timing={"request_preparation_seconds":1.0,"result_ledger_seconds":.5},
        activation_phases={"pre_publication":{"duration_seconds":2.0},
            "active_generation_publication":{"duration_seconds":.5},"post_publication":{"duration_seconds":3.0}},
        operation_timings={"pre_veth_creation":{"duration_seconds":1.0,"operation_count":4},
            "post_veth_deletion":{"duration_seconds":2.0,"operation_count":3},
            "active_generation_update":{"duration_seconds":.5,"operation_count":1}})
    schedule = _event("boundary_scheduled", 100, boundary_timestamp=event["boundary_timestamp"],
        ideal_activation_monotonic_seconds=99.0,paced_not_before_monotonic_seconds=99.75)
    detail = _detailed_timing((schedule,event))
    residual = detail["boundaries"][0]["residuals"]
    assert residual["main_unattributed_seconds"] == pytest.approx(.5)
    assert residual["exchange_unattributed_seconds"] == pytest.approx(.7)
    assert residual["helper_dispatch_unattributed_seconds"] == pytest.approx(.2)
    assert residual["pre_barrier_unattributed_seconds"] == pytest.approx(1.0)
    assert residual["post_barrier_unattributed_seconds"] == pytest.approx(1.0)
    assert residual["helper_activation_outside_barriers_seconds"] == pytest.approx(.3)
    assert detail["boundaries"][0]["pacing"]["pacing_carry_seconds"] == pytest.approx(.75)
    assert detail["boundaries"][0]["pacing"]["start_after_deadline_seconds"] == pytest.approx(.25)
    assert detail["operations"]["pre_veth_creation"]["operations_per_boundary"]["total"] == 4
    assert detail["main_exceeding_five_seconds"] == 1
    assert detail["kernel_calls"] == {}  # no substitute zeros for absent counters
