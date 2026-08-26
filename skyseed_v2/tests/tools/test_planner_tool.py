from __future__ import annotations

from copy import deepcopy
from inspect import getsource
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
from tools.planner.renderer import render_planner_page
from tools._chunk_encoding import CHUNK_ENCODING, decode_json_chunk
from tools.planner.projection import (
    _parameter_series,
    _planned_parameter_occurrences,
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
        (output / "planner-manifest.json").read_text(encoding="utf-8")
    )
    return output, result, data


def _load_chunk(output: Path, relative_path: str):
    source = (output / relative_path).read_text(encoding="utf-8")
    encoded = json.loads(source.rsplit("]=", 1)[1].removesuffix(";\n"))
    return decode_json_chunk(encoded)


def _catalog_values(
    output: Path,
    descriptor: dict[str, object],
):
    values = []
    for page in descriptor["index_chunks"]:
        for group in _load_chunk(output, page["chunk"]):
            values.extend(_load_chunk(output, group["chunk"]))
    return values


def test_complex_scenario_uses_real_formal_scale_and_consecutive_plans(
    generated,
) -> None:
    output, result, data = generated

    assert result["status"] == "PASS"
    assert data["authority"] == {
        "status": "non-authoritative-view",
        "slicer": "retained public Slicer results",
        "differences": "exact caller-provided Planner-consumed Differences",
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
    assert data["configuration"]["scenario_display_name"] == (
        "Mini Satellite Internet"
    )
    assert _load_chunk(output, data["slice_manifest"][1]["chunk"])[
        "positions"
    ]
    assert [value["coverage"] for value in data["plans"]] == [
        f"({start},{start + 4}]" for start in range(0, 64, 4)
    ]
    for previous, current in zip(data["plans"], data["plans"][1:]):
        assert previous["end_slice_id"] == current["start_slice_id"]
        assert previous["end_timestamp"] == current["start_timestamp"]


def test_projection_exposes_midpoint_merge_and_precedence(
    generated,
) -> None:
    output, _, data = generated
    behavior = data["observed_behavior"]

    assert behavior["midpoint_connection_transitions"] > 0
    assert behavior["parameter_merges"] > 0
    assert behavior["repeated_connection_ids"] == 0
    assert behavior["accepted_suppressions"] == 0
    assert behavior["unmatched_forwarding_transitions"] == 0
    assert behavior["synthetic_forwarding_claimed"] is False
    assert data["inventory"]["forwarding_precedences"] > 0
    connections = _catalog_values(output, data["catalogs"]["connection"])
    repeated = [
        value
        for value in connections
        if len(value["formal_occurrences"]) > 1
    ]
    assert repeated == []
    parameter_series = _catalog_values(
        output, data["catalogs"]["parameter"]
    )
    forwarding = _catalog_values(
        output, data["catalogs"]["forwarding"]
    )
    assert any(value["planned"] for value in parameter_series)
    assert any(
        value["planned_state_occurrences"]
        for value in parameter_series
    )
    assert any(value["precedences"] for value in forwarding)
    assert "O3-S6" in data["display_aliases"]["nodes"].values()
    assert data["display_aliases"]["zones"]["north-america-east"] == (
        "Z:north-america-east"
    )


def test_parameter_projection_indexes_relevant_transitions_once() -> None:
    projection_source = getsource(_parameter_series)
    occurrence_source = getsource(_planned_parameter_occurrences)

    assert "connection_transitions_by_ref" in projection_source
    assert "parameter_transitions_by_key" in projection_source
    assert "for value in transitions" not in occurrence_source


def test_projection_uses_current_artifact_end_and_exact_compact_summaries(
    generated,
) -> None:
    output, _, data = generated

    assert data["timeline"]["artifact_end_time"] == data["slice_manifest"][-1][
        "timestamp"
    ]
    connections = _catalog_values(output, data["catalogs"]["connection"])
    assert any(
        occurrence["right_censored"]
        and occurrence["observed_through_timestamp"]
        == data["timeline"]["artifact_end_time"]
        for value in connections
        for occurrence in value["formal_occurrences"]
    )
    assert any(
        occurrence["right_censored"]
        and occurrence["withdraw_time"] is None
        for value in connections
        for occurrence in value["planned_occurrences"]
    )

    summary = data["planner_summary"]
    assert summary["whole_run"]["connection"][
        "materialized_transitions"
    ] == data["inventory"]["connection_transitions"]
    assert summary["whole_run"]["parameter"][
        "emitted_transitions"
    ] == data["inventory"]["parameter_transitions"]
    assert summary["whole_run"]["forwarding"][
        "emitted_transitions"
    ] == data["inventory"]["forwarding_transitions"]
    assert len(summary["per_plan"]) == data["inventory"]["plans"]
    for plan, plan_summary in zip(
        data["plans"], summary["per_plan"], strict=True
    ):
        assert plan_summary["plan_index"] == plan["plan_index"]
        assert plan_summary["published"] == {
            "connection": plan["connection_transition_count"],
            "parameter": plan["parameter_transition_count"],
            "forwarding": plan["forwarding_transition_count"],
            "precedence": plan["forwarding_precedence_count"],
        }


def test_every_published_forwarding_transition_matches_a_formal_route_patch(
    generated,
) -> None:
    output, _, data = generated

    for entry in data["difference_catalog"]:
        comparison = _load_chunk(output, entry["chunk"])
        patches = {
            (
                value["route_ref"],
                value["operation"],
                value["next_hop"],
            )
            for value in comparison["difference"]["route_patches"]
        }
        plan_chunks = [
            _load_chunk(output, data["plans"][index]["chunk"])
            for index in comparison["plan_indices"]
        ]
        forwarding_ids = set(comparison["planned"]["forwarding"])
        for transition in (
            value
            for plan in plan_chunks
            for value in plan["forwarding_transitions"]
            if value["transition_id"] in forwarding_ids
        ):
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
        "planner-overview-summary",
        "planner-distributions",
        "selected-plan-summary",
        "playback-previous",
        "playback-toggle",
        "playback-next",
        "playback-scrubber",
        "playback-speed",
        "playback-mode",
        "playback-status",
    ):
        assert f'id="{identifier}"' in page
    assert "Sampled timeline ≠ publication timeline" in page
    assert 'aria-label="Planner visual encoding legend"' in page
    for legend_label in (
        "Formal sampled state",
        "Planned published state",
        "Shared path edge",
        "Formal-only path edge",
        "Planned-only path edge",
        "Planner nominal transition",
        "Formal sample boundary",
        "Parameter formal sample",
        "Parameter planned step",
        "Published ParameterTransition",
    ):
        assert legend_label in page
    assert "NOT tuple order" in page
    assert "Rejected candidates" in page
    assert "No published forwarding-state change" in page
    assert "planned target is derived only" in page
    assert "No midpoint interpolation" in page
    assert "complete event group as bounded change context" in page
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
    assert data["representatives"]["connection_ref"] not in page
    assert "restoreSelectedRoutePath" in page
    assert "loadChunk" in page
    assert "releaseChunk" in page
    assert "JSON.stringify(DATA,null,2)" not in page
    assert "planner-inspection.json" not in page
    assert "planner-manifest.js" in page
    assert "window.SKYSEED_PLANNER_MANIFEST={" not in page
    assert '"difference_from_previous"' not in page
    assert page.count('"positions"') == 0
    assert len(page.encode("utf-8")) < 1_000_000
    assert "Mini Satellite Internet · Planner inspection" in page
    assert "artifact_end_time" in page
    assert "planning_windows" in page
    assert "OPEN/right-censored" in page
    assert "observed through current artifact end" in page
    assert "loadPlannedNextHops" in page
    assert "formal-only" in page
    assert "planned-only" in page
    assert "No third Route is constructed" in page
    assert "Candidate/divergence start timestamps are Planner-private" in page
    assert "openConnectionRef" in page
    assert "Previous formal Slice sample" in page
    assert "Planner nominal transition" in page
    assert "Next formal Slice sample" in page
    assert "Full-run playback" in page
    assert 'timeline.dataset.ready="true"' in page
    assert 'timeline.querySelectorAll(".plan-bar[data-plan-index]")' in page
    assert 'eventRegion.setAttribute("aria-busy","true")' in page
    assert 'document.body.dataset.plannerPlayback="playing"' in page
    assert "scrollIntoView" not in page
    assert "location.reload" not in page
    assert "Formal + planned change overlay" in page
    assert "select a Route for its complete exact P/Q paths" in page
    assert "Showing ${previewLimit} of ${values.length}" in page
    assert "simulation time (UTC)" in page
    assert "parameterUnits" in page
    assert "Show planning details" in page
    assert 'viewBox="0 0 100 100"' not in page
    assert "#spatial-detail { max-height:520px; overflow:auto" in page


def test_forwarding_event_boundary_projection_handles_add_change_remove(
    generated,
) -> None:
    output, _, _ = generated
    page = (output / "index.html").read_text(encoding="utf-8")

    # The selected boundary is group-semantic: BEFORE excludes every event at
    # t; AFTER includes them. Applying a non-remove sets/replaces the route,
    # while REMOVE deletes its prior planned state.
    assert 'includeBoundary=boundary==="after"' in page
    assert "!includeBoundary&&transitionTime===limit" in page
    assert 'if(transition.operation==="removed")nextHops.delete(key)' in page
    assert "else nextHops.set(key,transition.next_hop)" in page
    assert "loadPlannedNextHops(spatial.time,spatialSide)" in page
    assert "the selected transition is not applied" in page
    assert "after applying all transitions at" in page
    assert "planned state ${spatialSide} event boundary" in page


def test_stable_specific_surfaces_are_selected_from_actual_models(
    generated,
) -> None:
    output, _, _ = generated
    page = (output / "index.html").read_text(encoding="utf-8")

    assert 'startsWith("MidpointConnection")' in page
    assert 'startsWith("StableForwarding")' in page
    assert "stableConnection?" in page
    assert "stableForwarding?" in page
    assert "faithful sample-boundary translation" in page
    assert "faithful formal-follow / always FOLLOW" in page
    assert "HOLD thresholds are not active" in page


def test_renderer_uses_supplied_scenario_and_current_policy_configuration(
    generated,
) -> None:
    _, _, mini_data = generated
    s4_data = deepcopy(mini_data)
    s4_data["configuration"] = {
        "scenario_identity": "s4_satellite_internet",
        "scenario_display_name": "S4 Satellite Internet",
        "slice_interval_seconds": 10.0,
        "qualification_target_slice_id": 20,
        "planning_window": "bounded exact time prefix",
        "planning_lookahead_seconds": 50.0,
        "publication_horizon_seconds": 20.0,
        "connection_model": "MidpointConnectionPlanningModel",
        "connection_timing": "interval midpoint",
        "connection_episode_candidate_max_seconds_by_kind": {"ISL": 10.0},
        "parameter_model": "AccumulatedDeadbandParameterPlanningModel",
        "parameter_delay_us": {
            "absolute_deadband": 1,
            "relative_deadband": 0.1,
            "persistence_seconds": 10.0,
            "max_staleness_seconds": 20.0,
        },
        "forwarding_model": "StableForwardingPlanningModel",
        "forwarding_stability": {"minimum_hop_gain": 1},
        "coordination_policy": "DefaultCoordinationPolicy",
    }

    page = render_planner_page(s4_data)
    renderer_source = (
        Path(__file__).resolve().parents[2] / "tools/planner/renderer.py"
    ).read_text(encoding="utf-8")

    assert "S4 Satellite Internet · Planner inspection" in page
    assert "Mini Satellite Internet" not in page
    assert "minimum dwell 240" not in page.lower()
    assert "config.parameter_delay_us" in page
    assert 'configDetail(["forwarding_stability"])' in page
    assert "Mini Satellite Internet" not in renderer_source
    assert "S4 Satellite Internet" not in renderer_source


def test_writer_emits_deterministic_audit_bundle_and_valid_checksums(
    generated,
) -> None:
    output, result, data = generated

    assert result["files"][0:4] == (
        "index.html",
        "planner-manifest.json",
        "planner-manifest.js",
        "summary.txt",
    )
    assert result["files"][-2:] == ("inventory.json", "checksums.sha256")
    listed = {
        line.split("  ", 1)[1]
        for line in (output / "checksums.sha256")
        .read_text(encoding="utf-8")
        .splitlines()
    }
    assert {
        "index.html",
        "planner-manifest.json",
        "planner-manifest.js",
        "summary.txt",
        "inventory.json",
    } <= listed
    assert any(path.startswith("chunks/slice-") for path in listed)
    assert any(path.startswith("chunks/plan-") for path in listed)
    assert any(path.startswith("chunks/parameter-") for path in listed)
    assert any(path.startswith("chunks/forwarding-") for path in listed)
    assert any(path.startswith("chunks/difference-") for path in listed)
    inventory = json.loads(
        (output / "inventory.json").read_text(encoding="utf-8")
    )
    inventory_paths = {value["path"] for value in inventory["files"]}
    assert {
        "index.html",
        "planner-manifest.json",
        "planner-manifest.js",
        "summary.txt",
    } <= (
        inventory_paths
    )
    assert "planner-inspection.json" not in inventory_paths
    assert inventory["chunk_encoding"] == CHUNK_ENCODING
    assert data["chunk_encoding"] == CHUNK_ENCODING


def test_lazy_chunks_omit_eager_route_paths_and_use_exact_differences(
    generated,
) -> None:
    output, _, data = generated

    for entry in data["slice_manifest"]:
        source = (output / entry["chunk"]).read_text(encoding="utf-8")
        assert '"paths"' not in source
        assert '"difference_from_previous"' not in source
    planner_reader = (
        Path(__file__).resolve().parents[2] / "tools/planner/reader.py"
    ).read_text(encoding="utf-8")
    mini_wrapper = (
        Path(__file__).resolve().parents[2]
        / "examples/mini_satellite_internet/main.py"
    ).read_text(encoding="utf-8")
    assert "_restore_paths_for_planner" not in planner_reader
    assert "planner.get_consumed_differences()" in mini_wrapper


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
