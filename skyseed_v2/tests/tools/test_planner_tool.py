from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.main import (
    write_planner_inspection,
)
from examples.mini_satellite_internet.simu import (
    create_mini_satellite_internet_simulator,
    prepare_mini_satellite_internet_planning,
)


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    output = tmp_path_factory.mktemp("planner-tool")
    emulator = build_mini_satellite_internet_emulator()
    simulator = create_mini_satellite_internet_simulator(emulator)
    simulator.get_slicer().create_all_slices()
    planner, plans, configuration = prepare_mini_satellite_internet_planning(
        simulator
    )
    result = write_planner_inspection(
        simulator.get_slicer(),
        planner,
        plans,
        configuration,
        output,
    )
    data = json.loads(
        (output / "planner-inspection.json").read_text(encoding="utf-8")
    )
    return output, result, data


def test_complex_scenario_uses_real_formal_scale_and_consecutive_plans(
    generated,
) -> None:
    _, result, data = generated

    assert result["status"] == "PASS"
    assert data["authority"] == {
        "status": "non-authoritative-view",
        "slicer": "retained public Slicer results and Differences",
        "plans": "immutable public Plan publications",
        "decisions": "Planner.get_planning_decisions result",
        "models": "not invoked by this Tool",
    }
    assert data["inventory"]["satellites"] == 32
    assert data["inventory"]["ground_stations"] == 3
    assert data["inventory"]["real_slices"] == 64
    assert data["inventory"]["differences"] == 64
    assert data["configuration"]["scenario_identity"] == (
        "mini_satellite_internet"
    )
    assert data["slices"][1]["positions"]
    assert [value["coverage"] for value in data["plans"]] == [
        f"({start},{start + 4}]" for start in range(0, 64, 4)
    ]
    for previous, current in zip(data["plans"], data["plans"][1:]):
        assert previous["end_slice_id"] == current["start_slice_id"]
        assert previous["end_timestamp"] == current["start_timestamp"]


def test_projection_exposes_midpoint_merge_and_precedence(
    generated,
) -> None:
    _, _, data = generated
    behavior = data["observed_behavior"]

    assert behavior["midpoint_connection_transitions"] > 0
    assert behavior["parameter_merges"] > 0
    assert behavior["repeated_connection_ids"] == 0
    assert behavior["accepted_suppressions"] == 0
    assert behavior["unmatched_forwarding_transitions"] == 0
    assert behavior["synthetic_forwarding_claimed"] is False
    assert data["inventory"]["forwarding_precedences"] > 0
    repeated = [
        value
        for value in data["connections"]
        if len(value["formal_occurrences"]) > 1
    ]
    assert repeated == []
    assert any(value["planned"] for value in data["parameter_series"])
    assert any(
        value["planned_state_occurrences"]
        for value in data["parameter_series"]
    )
    assert any(value["precedences"] for value in data["forwarding"])
    assert "O3-S6" in data["display_aliases"]["nodes"].values()
    assert data["display_aliases"]["zones"]["north-america-east"] == (
        "Z:north-america-east"
    )


def test_every_published_forwarding_transition_matches_a_formal_route_patch(
    generated,
) -> None:
    _, _, data = generated

    for comparison in data["difference_comparisons"]:
        formal_slice = data["slices"][comparison["difference_id"]]
        patches = {
            (
                value["route_ref"],
                value["operation"],
                value["next_hop"],
            )
            for value in formal_slice["difference_from_previous"]["route_patches"]
        }
        for transition in comparison["planned"]["forwarding"]:
            assert (
                transition["route_ref"],
                transition["operation"],
                transition["next_hop"],
            ) in patches


def test_primary_page_contains_all_required_semantic_views(generated) -> None:
    output, _, data = generated
    page = (output / "index.html").read_text(encoding="utf-8")

    for identifier in (
        "global-timeline",
        "connection-select",
        "parameter-select",
        "route-select",
        "decision-list",
        "difference-select",
        "plan-table",
        "planner-globe",
        "nominal-time-select",
        "nominal-events",
        "spatial-driver-title",
        "spatial-detail",
        "parameter-overview",
    ):
        assert f'id="{identifier}"' in page
    assert "Sampled timeline ≠ publication timeline" in page
    assert "not execution order" in page
    assert "rejected_suppression_candidates" in page
    assert "No published forwarding-state change" in page
    assert "planned target derived only" in page
    assert "No midpoint interpolation" in page
    assert "Only the selected concrete event is drawn" in page
    assert "0 real transitions" in page
    assert "drawZoneFootprint" in page
    assert "No fake satellite-to-Zone network link" in page
    assert "Formal observations vs published parameter targets" in page
    assert "Not individually published" in page
    assert "Publication batching" in page
    assert "Parameter deadband/coalescing" in page
    assert "Same-time forwarding dependency" in page
    assert "data-selected-precedence" in page
    assert "BEFORE next hop" in page
    assert "Current service/path-terminal satellite" in page
    assert "no Zone footprint is expected" in page
    assert "Actual local semantic precedence" not in page
    assert data["representatives"]["connection_ref"] in page


def test_writer_emits_deterministic_audit_bundle_and_valid_checksums(
    generated,
) -> None:
    output, result, _ = generated

    assert result["files"] == (
        "index.html",
        "planner-inspection.json",
        "summary.txt",
        "inventory.json",
        "checksums.sha256",
    )
    listed = {
        line.split("  ", 1)[1]
        for line in (output / "checksums.sha256")
        .read_text(encoding="utf-8")
        .splitlines()
    }
    assert listed == {
        "index.html",
        "planner-inspection.json",
        "summary.txt",
        "inventory.json",
    }
    inventory = json.loads(
        (output / "inventory.json").read_text(encoding="utf-8")
    )
    assert {value["path"] for value in inventory["files"]} == {
        "index.html",
        "planner-inspection.json",
        "summary.txt",
    }


def test_tool_sources_do_not_drive_planner_or_models() -> None:
    root = Path(__file__).resolve().parents[2] / "tools/planner"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in root.glob("*.py")
    )

    assert ".synchronize(" not in source
    assert ".create_planner(" not in source
    assert "PositionModel" not in source
    assert "ConnectionModel" not in source
    assert "RouteModel" not in source
    assert "PlanningModel" not in source
    assert "create_difference(" not in source
