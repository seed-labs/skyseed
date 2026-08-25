from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone

import pytest

import skyseed_v2
import skyseed_v2.simulator.slicing.difference as difference_module
from skyseed_v2.configuration.simulator import (
    LatitudeLongitudeRectangleDefinition,
    ZoneDefinition,
    ZoneLayoutDefinition,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionKind,
    ConnectionSlice,
    DifferenceOperation,
    DirectionalConnectionParameters,
    DirectionalConnectionParametersPatch,
    EarthFixedPosition,
    GroundStationRouteTarget,
    HopCountRouteModel,
    RouteKey,
    RoutePatch,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    TopologyDifference,
    TopologySlice,
    ZoneRouteTarget,
    ZoneServiceAssignmentPatch,
)
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)


EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
SATELLITE_1 = NodeId(65001, "satellite", "sat-1")
SATELLITE_2 = NodeId(65001, "satellite", "sat-2")
SATELLITE_3 = NodeId(65001, "satellite", "sat-3")
GROUND_STATION = NodeId(65001, "ground-station", "gs-1")


def _satellite(
    node_id: NodeId,
    orbit_index: int,
    mean_anomaly_deg: float,
) -> SatelliteSimulationInput:
    return SatelliteSimulationInput(
        node_id=node_id,
        satellite_id=node_id.entity_id,
        shell_id="shell-1",
        orbit_id="orbit-1",
        orbit_index=orbit_index,
        altitude_km=550.0,
        inclination_deg=53.0,
        right_ascension_deg=0.0,
        mean_anomaly_deg=mean_anomaly_deg,
        epoch_utc=EPOCH,
    )


def _static_input() -> SimulatorStaticInput:
    return SimulatorStaticInput(
        (
            _satellite(SATELLITE_1, 0, 0.0),
            _satellite(SATELLITE_2, 1, 120.0),
            _satellite(SATELLITE_3, 2, 240.0),
        ),
        (
            GroundStationSimulationInput(
                node_id=GROUND_STATION,
                ground_station_id=GROUND_STATION.entity_id,
                latitude_deg=0.0,
                longitude_deg=0.0,
                elevation_m=0.0,
                station_type="gateway",
                operator="Skyseed",
            ),
        ),
    )


def _zone_layout() -> ZoneLayoutDefinition:
    return ZoneLayoutDefinition(
        zones=tuple(
            ZoneDefinition(
                zone_id=zone_id,
                geometry=LatitudeLongitudeRectangleDefinition(
                    south_latitude_deg=south,
                    north_latitude_deg=south + 10.0,
                    west_longitude_deg=-10.0,
                    east_longitude_deg=0.0,
                ),
            )
            for zone_id, south in (
                ("zone-add", -40.0),
                ("zone-change", -10.0),
                ("zone-remove", 20.0),
            )
        ),
        allow_overlap=False,
    )


def _parameters(
    bandwidth_bps: int = 1_000,
    delay_us: int = 10,
    packet_loss_ppm: int = 100,
) -> DirectionalConnectionParameters:
    return DirectionalConnectionParameters(
        bandwidth_bps,
        delay_us,
        packet_loss_ppm,
    )


def _connection(
    kind: ConnectionKind,
    first: NodeId,
    second: NodeId,
    *,
    endpoint_a_to_b: DirectionalConnectionParameters | None = None,
    endpoint_b_to_a: DirectionalConnectionParameters | None = None,
) -> Connection:
    return Connection(
        ConnectionId(kind, first, second),
        endpoint_a_to_b or _parameters(),
        endpoint_b_to_a or _parameters(),
    )


def _changing_connection_slices(
    *,
    reverse_input: bool = False,
) -> tuple[ConnectionSlice, ConnectionSlice]:
    first_connections = (
        _connection(
            ConnectionKind.INTER_SATELLITE,
            SATELLITE_1,
            SATELLITE_2,
        ),
        _connection(
            ConnectionKind.INTER_SATELLITE,
            SATELLITE_2,
            SATELLITE_3,
        ),
        _connection(
            ConnectionKind.GROUND_STATION_ACCESS,
            GROUND_STATION,
            SATELLITE_3,
        ),
    )
    second_connections = (
        _connection(
            ConnectionKind.INTER_SATELLITE,
            SATELLITE_1,
            SATELLITE_3,
        ),
        _connection(
            ConnectionKind.INTER_SATELLITE,
            SATELLITE_2,
            SATELLITE_3,
        ),
        _connection(
            ConnectionKind.GROUND_STATION_ACCESS,
            GROUND_STATION,
            SATELLITE_3,
            endpoint_a_to_b=_parameters(bandwidth_bps=2_000),
            endpoint_b_to_a=_parameters(
                delay_us=20,
                packet_loss_ppm=200,
            ),
        ),
    )
    first_assignments = (
        ("zone-change", SATELLITE_1),
        ("zone-remove", SATELLITE_3),
    )
    second_assignments = (
        ("zone-add", SATELLITE_3),
        ("zone-change", SATELLITE_2),
    )
    if reverse_input:
        first_connections = tuple(reversed(first_connections))
        second_connections = tuple(reversed(second_connections))
        first_assignments = tuple(reversed(first_assignments))
        second_assignments = tuple(reversed(second_assignments))
    return (
        ConnectionSlice(first_connections, first_assignments),
        ConnectionSlice(second_connections, second_assignments),
    )


class _TimestampPositionModel:
    def compute_positions(self, topology, timestamp):
        offset = int((timestamp - EPOCH).total_seconds())
        return {
            node_id: EarthFixedPosition(
                7_000_000.0 + index + offset,
                float(index),
                0.0,
            )
            for index, node_id in enumerate(
                (
                    GROUND_STATION,
                    SATELLITE_1,
                    SATELLITE_2,
                    SATELLITE_3,
                )
            )
        }


class _SequenceConnectionModel:
    def __init__(self, values: tuple[ConnectionSlice, ...]) -> None:
        self._values = values
        self._index = 0

    def compute_connection_slice(
        self,
        topology,
        timestamp,
        positions,
        previous_connection_slice,
    ):
        result = self._values[self._index]
        self._index += 1
        return result


def _build_slicer(
    connection_slices: tuple[ConnectionSlice, ...],
):
    simulator = Simulator(
        SimulationDefinition(
            _static_input(),
            SimulationTimeline(
                EPOCH,
                EPOCH + timedelta(seconds=20),
                timedelta(seconds=10),
            ),
        )
    )
    simulator.create_topology(zone_layout_definition=_zone_layout())
    slicer = simulator.create_slicer(
        position_model=_TimestampPositionModel(),
        connection_model=_SequenceConnectionModel(connection_slices),
        route_model=HopCountRouteModel(),
    )
    slicer.create_slices(len(connection_slices))
    return slicer


@pytest.mark.parametrize(
    "changed_values",
    (
        {"bandwidth_bps": 1},
        {"delay_us": 0, "packet_loss_ppm": 0},
        {"packet_loss_ppm": 1_000_000},
    ),
    ids=(
        "minimum-bandwidth",
        "zero-delay-and-loss",
        "maximum-packet-loss",
    ),
)
def test_directional_parameter_patch_accepts_formal_boundaries(
    changed_values: dict[str, int],
) -> None:
    identifier = ConnectionId(
        ConnectionKind.INTER_SATELLITE,
        SATELLITE_1,
        SATELLITE_2,
    )

    patch = DirectionalConnectionParametersPatch(
        identifier,
        ConnectionDirection.ENDPOINT_A_TO_B,
        **changed_values,
    )

    assert patch.connection_id == identifier
    assert patch.direction is ConnectionDirection.ENDPOINT_A_TO_B
    for field_name in (
        "bandwidth_bps",
        "delay_us",
        "packet_loss_ppm",
    ):
        assert getattr(patch, field_name) == changed_values.get(field_name)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("bandwidth_bps", 0),
        ("bandwidth_bps", -1),
        ("bandwidth_bps", True),
        ("delay_us", -1),
        ("delay_us", True),
        ("packet_loss_ppm", -1),
        ("packet_loss_ppm", 1_000_001),
        ("packet_loss_ppm", True),
    ),
    ids=(
        "zero-bandwidth",
        "negative-bandwidth",
        "boolean-bandwidth",
        "negative-delay",
        "boolean-delay",
        "negative-packet-loss",
        "packet-loss-over-maximum",
        "boolean-packet-loss",
    ),
)
def test_directional_parameter_patch_rejects_out_of_range_values(
    field_name: str,
    invalid_value: int,
) -> None:
    identifier = ConnectionId(
        ConnectionKind.INTER_SATELLITE,
        SATELLITE_1,
        SATELLITE_2,
    )

    with pytest.raises(ValueError):
        DirectionalConnectionParametersPatch(
            identifier,
            ConnectionDirection.ENDPOINT_A_TO_B,
            **{field_name: invalid_value},
        )


@pytest.mark.parametrize(
    ("connection_id", "direction", "changed_values", "error_type"),
    (
        (
            ConnectionId(
                ConnectionKind.INTER_SATELLITE,
                SATELLITE_1,
                SATELLITE_2,
            ),
            ConnectionDirection.ENDPOINT_A_TO_B,
            {},
            ValueError,
        ),
        (
            "not-a-connection-id",
            ConnectionDirection.ENDPOINT_A_TO_B,
            {"delay_us": 0},
            TypeError,
        ),
        (
            ConnectionId(
                ConnectionKind.INTER_SATELLITE,
                SATELLITE_1,
                SATELLITE_2,
            ),
            "not-a-direction",
            {"packet_loss_ppm": 0},
            TypeError,
        ),
    ),
    ids=(
        "all-values-unchanged",
        "invalid-connection-id",
        "invalid-direction",
    ),
)
def test_directional_parameter_patch_rejects_invalid_identity_or_empty_change(
    connection_id: object,
    direction: object,
    changed_values: dict[str, int],
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        DirectionalConnectionParametersPatch(
            connection_id,
            direction,
            **changed_values,
        )


@pytest.mark.parametrize(
    ("zone_id", "operation", "service_satellite_id", "error_type"),
    (
        ("zone-a", DifferenceOperation.ADDED, None, TypeError),
        ("zone-a", DifferenceOperation.CHANGED, None, TypeError),
        (
            "zone-a",
            DifferenceOperation.REMOVED,
            SATELLITE_1,
            ValueError,
        ),
        (
            "zone-a",
            DifferenceOperation.ADDED,
            GROUND_STATION,
            ValueError,
        ),
        (
            "zone-a",
            DifferenceOperation.CHANGED,
            GROUND_STATION,
            ValueError,
        ),
        ("zone-a", "not-an-operation", SATELLITE_1, TypeError),
        ("", DifferenceOperation.ADDED, SATELLITE_1, ValueError),
    ),
    ids=(
        "added-without-satellite",
        "changed-without-satellite",
        "removed-with-satellite",
        "added-with-ground-station",
        "changed-with-ground-station",
        "invalid-operation",
        "invalid-zone-id",
    ),
)
def test_zone_service_assignment_patch_rejects_invalid_operation_value_pairs(
    zone_id: str,
    operation: object,
    service_satellite_id: NodeId | None,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        ZoneServiceAssignmentPatch(
            zone_id,
            operation,
            service_satellite_id,
        )


@pytest.mark.parametrize(
    ("route_key", "operation", "next_hop", "error_type"),
    (
        (
            RouteKey(SATELLITE_1, ZoneRouteTarget("zone-a")),
            DifferenceOperation.ADDED,
            None,
            TypeError,
        ),
        (
            RouteKey(SATELLITE_1, ZoneRouteTarget("zone-a")),
            DifferenceOperation.CHANGED,
            None,
            TypeError,
        ),
        (
            RouteKey(SATELLITE_1, ZoneRouteTarget("zone-a")),
            DifferenceOperation.REMOVED,
            SATELLITE_2,
            ValueError,
        ),
        (
            RouteKey(SATELLITE_1, ZoneRouteTarget("zone-a")),
            "not-an-operation",
            SATELLITE_2,
            TypeError,
        ),
        (
            "not-a-route-key",
            DifferenceOperation.ADDED,
            SATELLITE_2,
            TypeError,
        ),
    ),
    ids=(
        "added-without-next-hop",
        "changed-without-next-hop",
        "removed-with-next-hop",
        "invalid-operation",
        "invalid-route-key",
    ),
)
def test_route_patch_rejects_invalid_operation_value_pairs(
    route_key: object,
    operation: object,
    next_hop: NodeId | None,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        RoutePatch(route_key, operation, next_hop)


def test_zero_to_one_contains_the_complete_initial_dynamic_change() -> None:
    first, second = _changing_connection_slices()
    slicer = _build_slicer((first, second))

    difference = slicer.create_difference(0, 1)
    first_slice = slicer.get_slice(1)

    assert difference.previous_slice_id == 0
    assert difference.previous_timestamp is None
    assert difference.current_slice_id == 1
    assert difference.current_timestamp == EPOCH
    assert difference.added_connections == first_slice.connection_slice.connections
    assert difference.removed_connection_ids == ()
    assert difference.connection_parameter_patches == ()
    assert {
        (patch.zone_id, patch.operation, patch.service_satellite_id)
        for patch in difference.zone_service_assignment_patches
    } == {
        ("zone-change", DifferenceOperation.ADDED, SATELLITE_1),
        ("zone-remove", DifferenceOperation.ADDED, SATELLITE_3),
    }
    assert difference.route_patches
    assert all(
        patch.operation is DifferenceOperation.ADDED
        and patch.next_hop is not None
        for patch in difference.route_patches
    )
    assert len(difference.route_patches) == sum(
        len(routes) for routes in first_slice.route_slice.routes.values()
    )
    assert not difference.is_empty


def test_one_to_two_captures_all_sparse_patch_categories() -> None:
    first, second = _changing_connection_slices()
    slicer = _build_slicer((first, second))

    difference = slicer.create_difference(1, 2)

    assert tuple(
        connection.connection_id for connection in difference.added_connections
    ) == (
        ConnectionId(
            ConnectionKind.INTER_SATELLITE,
            SATELLITE_1,
            SATELLITE_3,
        ),
    )
    assert difference.removed_connection_ids == (
        ConnectionId(
            ConnectionKind.INTER_SATELLITE,
            SATELLITE_1,
            SATELLITE_2,
        ),
    )
    access_id = ConnectionId(
        ConnectionKind.GROUND_STATION_ACCESS,
        GROUND_STATION,
        SATELLITE_3,
    )
    assert difference.connection_parameter_patches == (
        DirectionalConnectionParametersPatch(
            access_id,
            ConnectionDirection.ENDPOINT_A_TO_B,
            bandwidth_bps=2_000,
        ),
        DirectionalConnectionParametersPatch(
            access_id,
            ConnectionDirection.ENDPOINT_B_TO_A,
            delay_us=20,
            packet_loss_ppm=200,
        ),
    )
    assert {
        (patch.zone_id, patch.operation, patch.service_satellite_id)
        for patch in difference.zone_service_assignment_patches
    } == {
        ("zone-add", DifferenceOperation.ADDED, SATELLITE_3),
        ("zone-change", DifferenceOperation.CHANGED, SATELLITE_2),
        ("zone-remove", DifferenceOperation.REMOVED, None),
    }
    route_operations = {
        patch.operation for patch in difference.route_patches
    }
    assert route_operations == {
        DifferenceOperation.ADDED,
        DifferenceOperation.CHANGED,
        DifferenceOperation.REMOVED,
    }
    assert all(
        (patch.next_hop is None)
        == (patch.operation is DifferenceOperation.REMOVED)
        for patch in difference.route_patches
    )
    assert {
        type(patch.route_key.target)
        for patch in difference.route_patches
    } == {GroundStationRouteTarget, ZoneRouteTarget}


def test_empty_difference_ignores_position_changes() -> None:
    first, _ = _changing_connection_slices()
    slicer = _build_slicer((first, first))
    previous_positions = slicer.get_slice(1).positions
    current_positions = slicer.get_slice(2).positions

    assert previous_positions != current_positions
    difference = slicer.create_difference(1, 2)
    assert difference.previous_slice_id == 1
    assert difference.previous_timestamp == EPOCH
    assert difference.current_slice_id == 2
    assert difference.current_timestamp == EPOCH + timedelta(seconds=10)
    assert difference.is_empty
    assert difference.added_connections == ()
    assert difference.removed_connection_ids == ()
    assert difference.connection_parameter_patches == ()
    assert difference.zone_service_assignment_patches == ()
    assert difference.route_patches == ()
    assert not hasattr(difference, "positions")


def test_difference_is_deterministic_deeply_immutable_and_independent() -> None:
    first, second = _changing_connection_slices()
    reversed_first, reversed_second = _changing_connection_slices(
        reverse_input=True
    )
    slicer = _build_slicer((first, second))
    reversed_slicer = _build_slicer((reversed_first, reversed_second))
    retained_before = tuple(
        slicer.get_slice(slice_id)
        for slice_id in slicer.get_retained_slice_ids()
    )
    latest_before = slicer.get_latest_slice()

    difference = slicer.create_difference(1, 2)
    repeated = slicer.create_difference(1, 2)
    reversed_result = reversed_slicer.create_difference(1, 2)

    assert difference == repeated == reversed_result
    assert difference is not repeated
    assert tuple(
        slicer.get_slice(slice_id)
        for slice_id in slicer.get_retained_slice_ids()
    ) == retained_before
    assert all(
        slicer.get_slice(index) is retained
        for index, retained in enumerate(retained_before)
    )
    assert slicer.get_latest_slice() is latest_before
    assert not hasattr(slicer, "_differences")
    assert not hasattr(difference, "__dict__")
    with pytest.raises(FrozenInstanceError):
        difference.current_slice_id = 99
    with pytest.raises(FrozenInstanceError):
        difference.route_patches[0].next_hop = SATELLITE_1

    reversed_route_input = list(reversed(difference.route_patches))
    copied = TopologyDifference(
        previous_slice_id=difference.previous_slice_id,
        previous_timestamp=difference.previous_timestamp,
        current_slice_id=difference.current_slice_id,
        current_timestamp=difference.current_timestamp,
        added_connections=reversed(difference.added_connections),
        removed_connection_ids=reversed(
            difference.removed_connection_ids
        ),
        connection_parameter_patches=reversed(
            difference.connection_parameter_patches
        ),
        zone_service_assignment_patches=reversed(
            difference.zone_service_assignment_patches
        ),
        route_patches=reversed_route_input,
    )
    reversed_route_input.clear()
    assert copied == difference
    assert copied.route_patches


def test_difference_rejects_missing_nonadjacent_reverse_and_foreign_inputs() -> None:
    first, second = _changing_connection_slices()
    slicer = _build_slicer((first, second))
    foreign_slicer = _build_slicer((first, second))

    with pytest.raises(ValueError, match="adjacent"):
        slicer.create_difference(0, 2)
    with pytest.raises(ValueError, match="adjacent"):
        slicer.create_difference(2, 1)
    with pytest.raises(ValueError, match="adjacent"):
        slicer.create_difference(1, 1)
    with pytest.raises(KeyError, match="not retained"):
        slicer.create_difference(2, 3)
    with pytest.raises(ValueError, match="slice_id"):
        slicer.create_difference(
            slicer.get_slice(1),
            foreign_slicer.get_slice(2),
        )

    assert not hasattr(TopologySlice, "diff")
    assert not hasattr(slicer, "compare_slices")
    assert "_compare_topology_slices" not in difference_module.__all__


def test_difference_checks_retained_metadata_but_trusts_published_contents() -> None:
    first, second = _changing_connection_slices()
    slicer = _build_slicer((first, second))
    published = slicer.get_slice(1)
    corrupt_timestamp = TopologySlice(
        slice_id=1,
        timestamp=EPOCH + timedelta(seconds=1),
        positions=published.positions,
        connection_slice=published.connection_slice,
        route_slice=published.route_slice,
    )
    slicer._slices[1] = corrupt_timestamp

    with pytest.raises(ValueError, match="timestamp metadata"):
        slicer.create_difference(0, 1)
    assert slicer.get_slice(1) is corrupt_timestamp
    assert slicer.get_latest_slice_id() == 2

    slicer = _build_slicer((first, second))
    published = slicer.get_slice(1)
    corrupt_id = TopologySlice(
        slice_id=7,
        timestamp=published.timestamp,
        positions=published.positions,
        connection_slice=published.connection_slice,
        route_slice=published.route_slice,
    )
    slicer._slices[1] = corrupt_id

    with pytest.raises(ValueError, match="ID metadata"):
        slicer.create_difference(0, 1)
    assert slicer.get_slice(1) is corrupt_id

    slicer = _build_slicer((first, second))
    del slicer._retained_publication_timestamps[1]
    with pytest.raises(RuntimeError, match="publication metadata is missing"):
        slicer.create_difference(0, 1)

    slicer = _build_slicer((first, second))
    published = slicer.get_slice(2)
    foreign_node = NodeId(65002, "satellite", "foreign")
    foreign_connection = _connection(
        ConnectionKind.INTER_SATELLITE,
        SATELLITE_1,
        foreign_node,
    )
    corrupt_data = TopologySlice(
        slice_id=2,
        timestamp=published.timestamp,
        positions=published.positions,
        connection_slice=ConnectionSlice((foreign_connection,)),
        route_slice=published.route_slice,
    )
    slicer._slices[2] = corrupt_data

    difference = slicer.create_difference(1, 2)
    assert foreign_connection in difference.added_connections
    assert slicer.get_slice(2) is corrupt_data


@pytest.mark.parametrize(
    ("include_added", "include_removed", "include_parameter_patch"),
    (
        (True, True, False),
        (True, False, True),
        (False, True, True),
    ),
    ids=(
        "added-and-removed",
        "added-and-updated",
        "removed-and-updated",
    ),
)
def test_topology_difference_rejects_connection_classification_overlap(
    include_added: bool,
    include_removed: bool,
    include_parameter_patch: bool,
) -> None:
    connection = _connection(
        ConnectionKind.INTER_SATELLITE,
        SATELLITE_1,
        SATELLITE_2,
    )
    identifier = connection.connection_id
    parameter_patch = DirectionalConnectionParametersPatch(
        identifier,
        ConnectionDirection.ENDPOINT_A_TO_B,
        delay_us=11,
    )

    with pytest.raises(ValueError, match="cannot be added"):
        TopologyDifference(
            previous_slice_id=1,
            previous_timestamp=EPOCH,
            current_slice_id=2,
            current_timestamp=EPOCH + timedelta(seconds=10),
            added_connections=(connection,) if include_added else (),
            removed_connection_ids=(identifier,) if include_removed else (),
            connection_parameter_patches=(
                (parameter_patch,) if include_parameter_patch else ()
            ),
        )


def test_public_patch_values_reject_duplicates_contradictions_and_old_state() -> None:
    connection = _connection(
        ConnectionKind.INTER_SATELLITE,
        SATELLITE_1,
        SATELLITE_2,
    )
    identifier = connection.connection_id
    parameter_patch = DirectionalConnectionParametersPatch(
        identifier,
        ConnectionDirection.ENDPOINT_A_TO_B,
        delay_us=11,
    )
    route_key = RouteKey(
        SATELLITE_1,
        ZoneRouteTarget("zone-change"),
    )
    route_patch = RoutePatch(
        route_key,
        DifferenceOperation.ADDED,
        SATELLITE_2,
    )
    zone_patch = ZoneServiceAssignmentPatch(
        "zone-change",
        DifferenceOperation.ADDED,
        SATELLITE_1,
    )

    with pytest.raises(ValueError, match="duplicate Connection parameter"):
        TopologyDifference(
            previous_slice_id=1,
            previous_timestamp=EPOCH,
            current_slice_id=2,
            current_timestamp=EPOCH + timedelta(seconds=10),
            connection_parameter_patches=(
                parameter_patch,
                parameter_patch,
            ),
        )
    with pytest.raises(ValueError, match="duplicate Zone"):
        TopologyDifference(
            previous_slice_id=1,
            previous_timestamp=EPOCH,
            current_slice_id=2,
            current_timestamp=EPOCH + timedelta(seconds=10),
            zone_service_assignment_patches=(zone_patch, zone_patch),
        )
    with pytest.raises(ValueError, match="duplicate Route"):
        TopologyDifference(
            previous_slice_id=1,
            previous_timestamp=EPOCH,
            current_slice_id=2,
            current_timestamp=EPOCH + timedelta(seconds=10),
            route_patches=(route_patch, route_patch),
        )
    with pytest.raises(ValueError, match="adjacent"):
        TopologyDifference(
            previous_slice_id=0,
            previous_timestamp=None,
            current_slice_id=2,
            current_timestamp=EPOCH,
        )
    with pytest.raises(ValueError, match="later"):
        TopologyDifference(
            previous_slice_id=1,
            previous_timestamp=EPOCH,
            current_slice_id=2,
            current_timestamp=EPOCH,
        )

    forbidden_fields = {
        "previous_slice",
        "current_slice",
        "positions",
        "old_value",
        "visibility",
        "coverage",
        "distance",
        "model_configuration",
        "label",
        "interface",
        "resource",
        "planner_threshold",
        "rollback",
        "backend",
        "full_path",
    }
    assert forbidden_fields.isdisjoint(
        {field.name for field in fields(TopologyDifference)}
    )
    for patch_type in (
        DirectionalConnectionParametersPatch,
        ZoneServiceAssignmentPatch,
        RoutePatch,
    ):
        assert not any(
            field.name.startswith("old_") for field in fields(patch_type)
        )
    assert hasattr(__import__("skyseed_v2.simulator", fromlist=[""]), "TopologyDifference")
    assert not hasattr(skyseed_v2, "TopologyDifference")
