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
    input_frontier = "2026-01-01T00:04:00+00:00"
    events = (
        _event("preparation_start", 10.0, preparation_lookahead="0:03:00"),
        _event(
            "input_advanced",
            10.5,
            realization_count=1,
            input_frontier=input_frontier,
            boundary_count=1,
        ),
        _event(
            "runtime_foundation_ready",
            11.0,
            shared_active_generation_map=True,
            capacity_high_water=7,
        ),
        _event(
            "maintenance_prepare_start",
            12.0,
            boundary_timestamp=boundary,
            generation=0,
        ),
        _event(
            "maintenance_prepare_end",
            13.0,
            boundary_timestamp=boundary,
            generation=0,
            duration_seconds=1.0,
            state_materialization_seconds=0.25,
            target_active_interface_entries=7,
            target_route_entries=3,
            generation_map_update_operations=10,
            execution_metrics={
                "external_processes": 2,
                "namespace_processes": 2,
                "bpf_syscalls": 10,
                "bpf_map_update_syscalls": 10,
                "worker_limit": 2,
                "batch_sizes": [3, 7],
            },
        ),
        _event(
            "capacity_audit_end",
            13.0,
            scope="boundary",
            boundary_timestamp=boundary,
            capacity={"generation": 0, "headroom": 2},
        ),
        _event(
            "ready_published",
            13.5,
            boundary_timestamp=boundary,
            predecessor=None,
            ready_frontier=boundary,
            input_frontier=input_frontier,
            preparation_headroom_seconds=0.0,
            generation=0,
            expected_active_generation=0xFFFFFFFF,
            prepared_resources=4,
        ),
        _event(
            "preparation_end",
            14.0,
            duration_seconds=4.0,
            input_frontier=input_frontier,
            ready_frontier=boundary,
            active_frontier=None,
        ),
        _event("automatic_run_start", 15.0, nominal_origin=boundary),
        _event(
            "boundary_scheduled",
            16.0,
            boundary_timestamp=boundary,
            scheduled_wall=16.0,
        ),
        _event("activation_start", 16.0, start_lag_seconds=0.001),
        _event(
            "activation_end",
            16.2,
            duration_seconds=0.2,
            completion_lag_seconds=0.201,
            active_frontier=boundary,
            ready_frontier=None,
            active_generation=0,
            readiness_dependency_wait_seconds=0.0,
            foundation_validation_seconds=0.03,
            runtime_apply_seconds=0.1,
            activation_switch_seconds=0.001,
            retirement_seconds=0.01,
            bookkeeping_seconds=0.02,
            grouping={
                "grouped_operation_count": 4,
                "affected_node_count": 1,
            },
        ),
        _event(
            "generation_reclaimed",
            17.0,
            duration_seconds=0.05,
            generation=0,
            deleted_entries=7,
        ),
        _event("automatic_run_end", 18.0, active_frontier=boundary),
        _event("private_detail_only", 18.5, retained=True),
        _event("cleanup_start", 19.0),
        _event(
            "runtime_cleanup_end",
            19.5,
            pin_root_removed=True,
            errors=[],
        ),
        _event("cleanup_end", 20.0, duration_seconds=1.0),
    )
    return {
        "format_version": 1,
        "runner": {
            "lifecycle_state": "cleaned",
            "execution_mode": "automatic",
            "frontiers": {
                "last_active": boundary,
                "ready_at_capture": None,
                "input": input_frontier,
            },
            "input_exhausted": True,
            "preparation_lookahead": {"mode": "rolling", "seconds": 180.0},
            "preparation_boundary_depth": 8,
            "activation_count": 1,
            "ready_count_at_capture": 0,
            "ready_depth_at_capture": 0,
            "ready_limiting_reason": "input",
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
            "maintenance": {
                "in_flight": False,
                "failed": False,
                "pending_forwarding_reclaim": 0,
                "pending_physical_reclaim": 0,
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


def test_projection_curates_complete_trace_without_runner_side_indexes() -> None:
    data = collect_runner_data(_snapshot(), _outcome())

    assert data["overview"]["lifecycle_state"] == "cleaned"
    assert data["overview"]["frontiers"]["last_active"] == (
        "2026-01-01T00:00:00+00:00"
    )
    assert data["overview"]["last_ready_published"] == (
        "2026-01-01T00:00:00+00:00"
    )
    assert data["overview"]["preparation_lookahead"] == {
        "mode": "rolling",
        "seconds": 180.0,
    }
    assert data["overview"]["preparation_boundary_depth"] == 8
    assert data["overview"]["ready_depth_at_capture"] == 0
    assert data["overview"]["ready_limiting_reason"] == "input"
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
    assert data["scale_safety"]["main_subphases"][0][
        "foundation_validation_seconds"
    ] == 0.03
    assert data["timing"]["activations"] == (
        {
            "boundary_timestamp": "2026-01-01T00:00:00+00:00",
            "scheduled_wall": 16.0,
            "start_lag_seconds": 0.001,
            "duration_seconds": 0.2,
            "completion_lag_seconds": 0.201,
            "completed": True,
            "active_frontier": "2026-01-01T00:00:00+00:00",
        },
    )
    assert data["timing"]["maintenance_preparations"] == (
        {
            "boundary_timestamp": "2026-01-01T00:00:00+00:00",
            "runtime_preparation_seconds": 1.0,
            "state_materialization_seconds": 0.25,
            "total_ready_preparation_seconds": 1.25,
            "generation": 0,
        },
    )
    assert data["timing"]["maintenance_preparation_total_seconds"] == 1.25
    assert [
        value["event"]
        for value in data["timeline"]
        if value["monotonic_seconds"] == 16.0
    ] == ["boundary_scheduled", "activation_start"]
    assert "private_detail_only" not in {
        value["event"] for value in data["timeline"]
    }
    assert data["raw"]["events"]["embedded"] is False
    assert data["raw"]["events"]["count"] == len(_snapshot()["events"])
    assert data["default_dataplane"]["kind"] == "default-tc-bpf"
    assert data["default_dataplane"]["fresh_kernel_inspection"] is False


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
        "target_timeline_index": 2,
        "generation": None,
    }
    automatic = groups["lifecycle"]["automatic_run_phase"][0]
    assert automatic["elapsed_start_seconds"] == 5.0
    assert automatic["elapsed_end_seconds"] == 8.0
    assert automatic["completed"] is True
    assert automatic["target_timeline_index"] == 7
    assert groups["work"]["future_preparation"][0] == {
        "kind": "future_preparation",
        "visual": "work",
        "elapsed_start_seconds": 2.0,
        "elapsed_end_seconds": 3.0,
        "boundary_timestamp": "2026-01-01T00:00:00+00:00",
        "target_timeline_index": 4,
        "generation": 0,
        "completed": True,
        "ready_marker": {
            "kind": "ready_published",
            "elapsed_seconds": 3.5,
            "boundary_timestamp": "2026-01-01T00:00:00+00:00",
            "target_timeline_index": 5,
            "generation": 0,
        },
    }
    assert set(groups) == {"lifecycle", "work"}
    assert not any(
        value["kind"] == "automatic_run_start"
        for group in groups.values()
        for values in group.values()
        for value in values
    )
    activation = groups["work"]["main_activation"][0]
    assert activation["elapsed_start_seconds"] == 6.0
    assert activation["elapsed_end_seconds"] == pytest.approx(6.2)
    assert activation["boundary_timestamp"] == "2026-01-01T00:00:00+00:00"
    reclaim = groups["work"]["reclaim_work"][0]
    assert reclaim["elapsed_start_seconds"] == pytest.approx(6.95)
    assert reclaim["elapsed_end_seconds"] == 7.0
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
        "Preparation boundary depth",
        "READY limiting reason",
        'data-language="zh"',
        'data-language="en"',
        'id="graphical-timeline"',
        'id="timeline"',
    ):
        assert required in page
    for forbidden in ("fetch(", "WebSocket", "EventSource", "http://", "https://"):
        assert forbidden not in page
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
        'id="maintenance-title"',
        'id="dataplane-title"',
        'id="failures-title"',
        'id="detailed-timeline-title"',
        'id="boundaries-title"',
        'id="raw-title"',
    )
    assert [page.index(marker) for marker in ordered_sections] == sorted(
        page.index(marker) for marker in ordered_sections
    )
    assert "end/duration*100" in page
    assert "Automatic scheduling/waiting lifecycle phase" in page
    assert "Lifecycle 横条表示 Runner 当时处于什么阶段" in page
    assert "work 在全局比例下可能只是一条细线" in page
    assert "准备未来状态" in page
    assert "切换当前网络状态" in page
    assert "回收旧状态" in page
    assert "Cyan end-cap" not in page
    assert "青色 end-cap" not in page
    assert "amber notch" not in page
    assert "黄色 notch" not in page
    assert 'id="graph-local-detail"' in page
    assert "pinnedGraphDetail" in page
    assert 'addEventListener("mouseenter"' in page
    assert 'data-local-event' in page
    assert "graph-end-cap ready" in page
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
