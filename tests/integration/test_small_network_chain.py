from __future__ import annotations

from collections import Counter, defaultdict
from inspect import Parameter, signature
import json
import os
from pathlib import Path
from subprocess import run
import sys
from types import SimpleNamespace

import pytest
from seedemu.core import Emulator
from seedemu.layers import Base
from yaml import safe_load


from tests.fixtures import small_network
from tests.fixtures.small_network import build_emulator
from tests._support import drain_simulator
FORMAL_BOUNDARY_COUNT = 64
from skyseed.emulator.seedemu import EmulatorAutonomousSystem
from skyseed.simulator import (
    OperationalConnectionKind,
    Simulator,
    apply_plan_transition,
)
from skyseed.simulator.geometry import (
    elevation_deg,
    geodetic_to_ecef,
    has_earth_line_of_sight,
)
from tools.observation.bpf_compiler import read_bpf_compiler_history
from tools.observation.planner import read_planner_history


DATA_ROOT = small_network.DATA_ROOT


@pytest.fixture(scope="module")
def reference_emulator() -> Emulator:
    return build_emulator()


@pytest.fixture(scope="module")
def reference_chain(
    tmp_path_factory: pytest.TempPathFactory,
    reference_emulator: Emulator,
) -> SimpleNamespace:
    history = tmp_path_factory.mktemp("small-domain-history")
    simulator = small_network.create_simulator(reference_emulator)
    # Retain immutable probes before Producer closes all computation owners.
    world = simulator.get_physical_world()
    physical_frames = {timestamp: world.frame_at(timestamp)
                       for timestamp in simulator.get_timeline().iter_timestamps()}
    physical_request = simulator.get_planner().create_physical_link_request()
    overlap_matches = world.find_zones(42.0, -74.0)
    overlap_winner = world.resolve_zone(42.0, -74.0)
    plans, realizations, snapshot = drain_simulator(simulator, evidence_path=history)
    evidence = read_planner_history(history / "planner/planning.jsonl.gz")
    transitions = tuple(
        transition
        for plan in plans
        for transition in plan.transitions
    )
    frames = tuple(frame for value in evidence for frame in value.frames)
    states = []
    state = None
    for transition in transitions:
        state = apply_plan_transition(state, transition)
        states.append(state)
    return SimpleNamespace(
        simulator=simulator,
        physical_world=simulator.get_physical_world(),
        plans=plans,
        realizations=realizations,
        snapshot=snapshot,
        history=history,
        evidence=evidence,
        transitions=transitions,
        frames=frames,
        states=tuple(states),
        physical_frames=physical_frames,
        physical_request=physical_request,
        overlap_matches=overlap_matches,
        overlap_winner=overlap_winner,
    )


def _satellite_parents(physical_world) -> dict[object, tuple[int, str, str]]:
    return {
        satellite.node_id: (
            autonomous_system.asn,
            shell.shell_id,
            orbit.orbit_id,
        )
        for autonomous_system in physical_world.autonomous_systems
        for shell in autonomous_system.shells
        for orbit in shell.orbits
        for satellite in orbit.satellites
    }


def _is_dynamic_transition(transition) -> bool:
    return bool(
        transition.established_connections
        or transition.withdrawn_connection_ids
        or transition.parameter_updated_connections
        or transition.zone_service_transitions
        or transition.route_outcome_transitions
    )


def test_small_chain_uses_one_producer_and_preserves_all_boundaries(
    reference_chain: SimpleNamespace,
) -> None:
    simulator = reference_chain.simulator
    plans = reference_chain.plans
    realizations = reference_chain.realizations
    snapshot = reference_chain.snapshot
    history = reference_chain.history

    transition_timestamps = tuple(
        transition.timestamp
        for plan in plans
        for transition in plan.transitions
    )
    activation_timestamps = tuple(
        timestamp
        for realization in realizations
        for timestamp in realization.activation_timestamps
    )
    assert transition_timestamps == tuple(
        simulator.get_timeline().iter_timestamps()
    )
    assert activation_timestamps == transition_timestamps
    assert len(transition_timestamps) == FORMAL_BOUNDARY_COUNT == 64
    assert len(plans) == len(realizations) == 2
    assert snapshot["formal_frame_count"] == FORMAL_BOUNDARY_COUNT
    assert snapshot["physical_link_observation_count"] == FORMAL_BOUNDARY_COUNT
    assert snapshot["transition_count"] == FORMAL_BOUNDARY_COUNT
    assert snapshot["plan_count"] == 2
    assert snapshot["realization_count"] == 2
    assert snapshot["domain_history_sealed"] is True

    manifest = json.loads(
        (history / "experiment/domain-history-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["through"] == "compiler"
    assert manifest["counts"]["formal_frames"] == FORMAL_BOUNDARY_COUNT
    assert manifest["counts"]["transitions"] == FORMAL_BOUNDARY_COUNT
    assert manifest["counts"]["plans"] == 2
    assert manifest["counts"]["realizations"] == 2
    assert len(reference_chain.evidence) == 2
    assert len(
        read_bpf_compiler_history(history / "compiler/compiler.jsonl")[
            "realizations"
        ]
    ) == 2


def test_small_generated_anomalies_preserve_the_alternating_policy(
    reference_chain: SimpleNamespace,
) -> None:
    physical_world = reference_chain.physical_world
    orbits = sorted(
        physical_world.autonomous_systems[0].shells[0].orbits,
        key=lambda orbit: orbit.orbit_id,
    )

    assert [
        [
            satellite.mean_anomaly_deg
            for satellite in sorted(
                orbit.satellites,
                key=lambda satellite: satellite.orbit_index,
            )
        ]
        for orbit in orbits
    ] == [
        [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0],
        [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5],
        [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0],
        [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5],
    ]
    assert all(
        station.max_simultaneous_satellite_accesses is None
        for autonomous_system in physical_world.autonomous_systems
        for station in autonomous_system.ground_stations
    )


def test_small_reference_uses_the_bounded_physical_planning_compiler_chain(
    reference_chain: SimpleNamespace,
) -> None:
    timeline = reference_chain.simulator.get_timeline()
    physical_world = reference_chain.physical_world

    assert small_network.DEFAULT_START_TIME.isoformat() == (
        "2026-01-01T00:00:30+00:00"
    )
    assert (small_network.DEFAULT_START_TIME + small_network.DEFAULT_DURATION).isoformat() == (
        "2026-01-01T00:11:00+00:00"
    )
    assert small_network.DEFAULT_OBSERVATION_INTERVAL.total_seconds() == 10
    assert reference_chain.simulator.get_planner().configuration.frames_per_batch == 32
    assert reference_chain.simulator.get_planner().configuration.ground_station_acquisition_duration.total_seconds() == 5
    assert tuple(
        transition.timestamp for transition in reference_chain.transitions
    ) == tuple(timeline.iter_timestamps())
    assert Counter(
        len(plan.transitions) for plan in reference_chain.plans
    ) == Counter({32: 2})
    assert len(reference_chain.plans) == 2
    assert len(reference_chain.transitions) == FORMAL_BOUNDARY_COUNT == 64
    assert any(
        _is_dynamic_transition(transition)
        for transition in reference_chain.transitions[1:]
    )
    assert sum(
        len(orbit.satellites)
        for autonomous_system in physical_world.autonomous_systems
        for shell in autonomous_system.shells
        for orbit in shell.orbits
    ) == 32
    assert sum(
        len(autonomous_system.ground_stations)
        for autonomous_system in physical_world.autonomous_systems
    ) == 3
    assert len(physical_world.get_zone_layout().zones) == 5


def test_cross_plane_scope_and_selected_degree_preserve_the_k3_policy(
    reference_chain: SimpleNamespace,
) -> None:
    physical_world = reference_chain.physical_world
    parent_by_node = _satellite_parents(physical_world)
    request = reference_chain.physical_request
    pairs = request.inter_satellite_pairs

    assert len(pairs) == len(set(pairs)) == 496
    assert all(
        (first.asn, first.entity_kind, first.entity_id)
        < (second.asn, second.entity_kind, second.entity_id)
        for first, second in pairs
    )
    assert sum(
        parent_by_node[first] == parent_by_node[second]
        for first, second in pairs
    ) == 112
    assert sum(
        parent_by_node[first] != parent_by_node[second]
        for first, second in pairs
    ) == 384

    selected_cross_degree_distributions = Counter()
    for frame in reference_chain.frames:
        same_degree = Counter()
        cross_degree = Counter()
        total_degree = Counter()
        for lifecycle in frame.selected_inter_satellite_links:
            candidate = lifecycle.candidate
            first = candidate.endpoint_a
            second = candidate.endpoint_b
            total_degree[first] += 1
            total_degree[second] += 1
            target = (
                same_degree
                if parent_by_node[first] == parent_by_node[second]
                else cross_degree
            )
            target[first] += 1
            target[second] += 1
        assert max(total_degree.values(), default=0) <= 4
        assert max(same_degree.values(), default=0) <= 2
        assert max(cross_degree.values(), default=0) <= 2
        selected_cross_degree_distributions[
            tuple(
                sorted(
                    Counter(
                        cross_degree.get(node_id, 0)
                        for node_id in parent_by_node
                    ).items()
                )
            )
        ] += 1

    assert sum(selected_cross_degree_distributions.values()) == 64
    assert all(all(degree <= 2 for degree, count in distribution) for distribution in selected_cross_degree_distributions)


def test_small_emulator_contains_static_network_and_routing_layers() -> None:
    emulator = build_emulator()
    base = emulator.getLayer("Base")

    assert isinstance(base, Base)
    assert str(base.getInternetExchange(110).getPeeringLan().getPrefix()) == (
        "10.110.0.0/24"
    )
    assert base.getAutonomousSystem(65110).getAsn() == 65110
    assert {layer.getName() for layer in emulator.getLayers()} == {
        "Base",
        "Routing",
        "Ospf",
        "Ibgp",
        "Ebgp",
    }


def test_small_reference_naturally_exercises_representative_behavior(
    reference_chain: SimpleNamespace,
) -> None:
    transitions = reference_chain.transitions
    frames = reference_chain.frames
    totals = {
        field_name: sum(
            len(getattr(transition, field_name))
            for transition in transitions
        )
        for field_name in (
            "established_connections",
            "withdrawn_connection_ids",
            "parameter_updated_connections",
            "zone_service_transitions",
            "route_outcome_transitions",
        )
    }
    assert all(count > 0 for count in totals.values())
    assert totals["zone_service_transitions"] == 14
    assert 0 < sum(_is_dynamic_transition(value) for value in transitions[1:]) < 64
    assert len(frames) == 64
    assert all(
        frame.inspection.stages
        == (
            "physical-input-validation",
            "isl-candidate-selection",
            "acquisition/lifecycle-advancement",
            "ground-station-planning",
            "zone-planning",
            "parameter-planning",
            "route-planning",
            "coordination",
            "state-advancement",
        )
        for frame in frames
    )
    assert sum(frame.inspection.acquisition_started for frame in frames) > 0
    assert sum(frame.inspection.acquisition_completed for frame in frames) > 0
    assert sum(frame.inspection.acquisition_reset for frame in frames) > 0
    assert {frame.inspection.route_recomputed for frame in frames} == {False, True}

    parent_by_node = _satellite_parents(reference_chain.physical_world)
    first_connections = Counter()
    for connection in reference_chain.states[0].operational_connections:
        identity = connection.connection_id
        if identity.kind is OperationalConnectionKind.GROUND_STATION_ACCESS:
            first_connections["ground-station-access"] += 1
        elif parent_by_node[identity.endpoint_a] == parent_by_node[
            identity.endpoint_b
        ]:
            first_connections["same-plane"] += 1
        else:
            first_connections["cross-plane"] += 1
    assert first_connections["same-plane"] == 32
    assert 0 < first_connections["cross-plane"] <= 32
    assert 0 < first_connections["ground-station-access"] <= 3
    assert all(
        len(state.zone_service_assignments) == 5
        for state in reference_chain.states
    )

    zone_operations = Counter()
    route_operations = Counter()
    for transition in transitions:
        for value in transition.zone_service_transitions:
            if (
                value.previous_satellite_id is None
                and value.current_satellite_id is None
            ):
                operation = "explicit-unserved"
            elif value.previous_satellite_id is None:
                operation = "added"
            elif value.current_satellite_id is None:
                operation = "removed"
            else:
                operation = "changed"
            zone_operations[operation] += 1
        for value in transition.route_outcome_transitions:
            if value.previous is None:
                operation = "added"
            elif value.current is None:
                operation = "removed"
            else:
                operation = "changed"
            route_operations[operation] += 1
    assert zone_operations == Counter(
        {"added": 7, "removed": 4, "explicit-unserved": 2, "changed": 1}
    )
    assert set(route_operations) == {"changed", "added", "removed"}


def test_zone_service_uses_representative_point_not_subpoint_containment(
    reference_chain: SimpleNamespace,
) -> None:
    physical_world = reference_chain.physical_world
    outside = None
    for evidence in reference_chain.frames:
        physical_frame = reference_chain.physical_frames[evidence.timestamp]
        for candidate in evidence.selected_zone_service_candidates:
            position = physical_frame.positions[candidate.satellite_id]
            zone = physical_world.get_zone(candidate.zone_id)
            if not zone.contains(
                position.latitude_deg,
                position.longitude_deg,
            ):
                outside = (evidence, candidate, physical_frame, zone)
                break
        if outside is not None:
            break

    assert outside is not None
    evidence, candidate, physical_frame, zone = outside
    assert candidate.elevation_deg >= 10.0
    representative_position = geodetic_to_ecef(
        zone.representative_point[0],
        zone.representative_point[1],
        0.0,
    )
    eligible = []
    for node_id, position in physical_frame.positions.items():
        if node_id.entity_kind != "satellite":
            continue
        elevation = elevation_deg(representative_position, position)
        if (
            elevation >= 10.0
            and has_earth_line_of_sight(representative_position, position)
        ):
            eligible.append(
                (
                    -elevation,
                    (node_id.asn, node_id.entity_kind, node_id.entity_id),
                    node_id,
                )
            )
    assert eligible
    assert candidate.satellite_id == min(eligible)[2]
    assert candidate.elevation_deg == pytest.approx(-min(eligible)[0])
    assert evidence.timestamp == physical_frame.timestamp


def test_reference_zone_overlap_preserves_all_memberships_and_priority_winner(
    reference_chain: SimpleNamespace,
) -> None:
    physical_world = reference_chain.physical_world
    layout = physical_world.get_zone_layout()
    probe = (42.0, -74.0)

    assert layout.allow_overlap
    assert {
        zone.zone_id for zone in reference_chain.overlap_matches
    } == {"north-america-east", "northeast-priority"}
    assert reference_chain.overlap_winner.zone_id == "northeast-priority"
    for zone_id in ("north-america-east", "northeast-priority"):
        zone = layout.get_zone(zone_id)
        assert zone.contains(*zone.representative_point)


def test_reference_ground_stations_keep_reference_values_and_dynamic_access(
    reference_chain: SimpleNamespace,
) -> None:
    expected = {
        "gs-porto-alegre-reference": (-30.0346, -51.2177, 0.0),
        "gs-marsala-reference": (37.7986, 12.4357, 0.0),
        "gs-yamaguchi-reference": (34.1861, 131.4706, 0.0),
    }
    physical_world = reference_chain.physical_world
    ground_stations = {
        station.ground_station_id: (
            station.latitude_deg,
            station.longitude_deg,
            station.elevation_m,
        )
        for autonomous_system in physical_world.autonomous_systems
        for station in autonomous_system.ground_stations
    }
    assert ground_stations == expected
    representative_points = {
        zone.representative_point
        for zone in physical_world.get_zone_layout().zones
    }
    assert not representative_points.intersection(
        (latitude, longitude)
        for latitude, longitude, _ in ground_stations.values()
    )

    station_ids = {
        station.node_id
        for autonomous_system in physical_world.autonomous_systems
        for station in autonomous_system.ground_stations
    }
    access_sets = {node_id: [] for node_id in station_ids}
    for state in reference_chain.states:
        per_station = defaultdict(set)
        for connection in state.operational_connections:
            identity = connection.connection_id
            if identity.kind is not OperationalConnectionKind.GROUND_STATION_ACCESS:
                continue
            ground_station = next(
                endpoint
                for endpoint in (identity.endpoint_a, identity.endpoint_b)
                if endpoint in station_ids
            )
            satellite = (
                identity.endpoint_b
                if ground_station == identity.endpoint_a
                else identity.endpoint_a
            )
            per_station[ground_station].add(satellite)
        for station_id in station_ids:
            access_sets[station_id].append(
                frozenset(per_station[station_id])
            )
    assert all(any(values) for values in access_sets.values())
    assert all(len(set(values)) > 1 for values in access_sets.values())

    later = reference_chain.transitions[1:]
    assert any(
        connection.connection_id.kind
        is OperationalConnectionKind.GROUND_STATION_ACCESS
        for transition in later
        for connection in transition.established_connections
    )
    assert any(
        identity.kind is OperationalConnectionKind.GROUND_STATION_ACCESS
        for transition in later
        for identity in transition.withdrawn_connection_ids
    )
    assert any(
        connection.connection_id.kind
        is OperationalConnectionKind.GROUND_STATION_ACCESS
        for transition in later
        for connection in transition.parameter_updated_connections
    )

    scenario_source = (DATA_ROOT / "scenario.yaml").read_text(
        encoding="utf-8"
    )
    assert "starlink_gateways.json" in scenario_source
    assert "s4_visualization_gateway_binding_12.json" in scenario_source
    assert "Reference Ground Station - Porto Alegre, Brazil" in scenario_source
    assert "Reference Ground Station - Marsala, Italy" in scenario_source
    assert "Reference Ground Station - Yamaguchi, Japan" in scenario_source
    assert "Starlink Gateway -" not in scenario_source
    assert "commercial operator's current" in scenario_source


def test_small_core_identities_and_results_repeat_deterministically(
    reference_chain: SimpleNamespace,
) -> None:
    repeated_emulator = build_emulator()
    repeated_simulator = small_network.create_simulator(repeated_emulator)
    repeated_plans, repeated_realizations, repeated_snapshot = drain_simulator(repeated_simulator)

    assert repeated_simulator.get_physical_world().semantic_fingerprint() == (
        reference_chain.physical_world.semantic_fingerprint()
    )
    assert repeated_plans == reference_chain.plans
    assert repeated_realizations == reference_chain.realizations
    assert repeated_snapshot["formal_frame_count"] == 64
    assert repeated_snapshot["plan_count"] == 2
    assert repeated_snapshot["realization_count"] == 2
