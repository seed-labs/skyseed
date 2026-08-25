from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import skyseed_v2.simulator.models.gateway_hop_count_route as gateway_route
from skyseed_v2.configuration.simulator import (
    LatitudeLongitudeRectangleDefinition,
    ZoneDefinition,
    ZoneLayoutDefinition,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    Connection,
    ConnectionId,
    ConnectionKind,
    ConnectionSlice,
    DirectionalConnectionParameters,
    EarthFixedPosition,
    GatewayHopCountRouteModel,
    GroundStationRouteTarget,
    RouteSlice,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    ZoneRouteTarget,
)
from skyseed_v2.simulator.geometry import geodetic_to_ecef
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)
from skyseed_v2.simulator.slicing._routing_policy import (
    _prepare_routing_policy_context,
)


EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
PARAMETERS = DirectionalConnectionParameters(1_000_000, 10, 0)


def _satellite(identifier: str, index: int) -> SatelliteSimulationInput:
    return SatelliteSimulationInput(
        node_id=NodeId(65090, "satellite", identifier),
        satellite_id=identifier,
        shell_id="shell",
        orbit_id="orbit",
        orbit_index=index,
        altitude_km=550.0,
        inclination_deg=53.0,
        right_ascension_deg=0.0,
        mean_anomaly_deg=index * 180.0,
        epoch_utc=EPOCH,
    )


def _station(
    identifier: str, longitude_deg: float
) -> GroundStationSimulationInput:
    return GroundStationSimulationInput(
        node_id=NodeId(65090, "ground-station", identifier),
        ground_station_id=identifier,
        latitude_deg=0.0,
        longitude_deg=longitude_deg,
        elevation_m=0.0,
        station_type="gateway",
        operator=None,
    )


def _topology(zone_ids: tuple[str, ...] = ("zone",)):
    satellites = (_satellite("sat-a", 0), _satellite("sat-b", 1))
    stations = (
        _station("a-near", 0.0),
        _station("b-middle", 10.0),
        _station("c-far", 20.0),
    )
    simulator = Simulator(
        SimulationDefinition(
            SimulatorStaticInput(satellites, stations),
            SimulationTimeline(EPOCH, EPOCH, timedelta(seconds=10)),
        )
    )
    topology = simulator.create_topology(
        zone_layout_definition=ZoneLayoutDefinition(
            zones=tuple(
                ZoneDefinition(
                    zone_id=zone_id,
                    priority=1,
                    geometry=LatitudeLongitudeRectangleDefinition(
                        south_latitude_deg=-1.0 + index * 3.0,
                        north_latitude_deg=1.0 + index * 3.0,
                        west_longitude_deg=-1.0,
                        east_longitude_deg=1.0,
                    ),
                )
                for index, zone_id in enumerate(zone_ids)
            ),
            allow_overlap=False,
        )
    )
    return simulator, topology, satellites, stations


def _connection(
    first: NodeId, second: NodeId
) -> Connection:
    kind = (
        ConnectionKind.GROUND_STATION_ACCESS
        if "ground-station" in {first.entity_kind, second.entity_kind}
        else ConnectionKind.INTER_SATELLITE
    )
    return Connection(ConnectionId(kind, first, second), PARAMETERS, PARAMETERS)


def _connected_slice(satellites, stations) -> ConnectionSlice:
    return ConnectionSlice(
        (
            _connection(satellites[0].node_id, satellites[1].node_id),
            _connection(stations[0].node_id, satellites[0].node_id),
            _connection(stations[1].node_id, satellites[1].node_id),
            _connection(stations[2].node_id, satellites[1].node_id),
        ),
        {"zone": satellites[1].node_id},
    )


def _route_slice_without_service_tree_reuse(
    model: GatewayHopCountRouteModel,
    topology,
    connection_slice: ConnectionSlice,
) -> RouteSlice:
    """Reproduce the pre-optimization per-Zone tree construction."""

    candidates_by_zone = model._prepare_gateway_candidates(topology)
    context = _prepare_routing_policy_context(topology, connection_slice)
    entries = {}
    zones_by_gateway = {}
    for zone_id, service_satellite in (
        connection_slice.zone_service_assignments.items()
    ):
        for gateway_id in candidates_by_zone.get(zone_id, ()):
            zones_by_gateway.setdefault(gateway_id, []).append(
                service_satellite
            )
    for gateway_id in sorted(zones_by_gateway, key=gateway_route._node_key):
        target = GroundStationRouteTarget(gateway_id)
        choices = gateway_route._eligible_shortest_next_hops(
            context, target, gateway_id
        )
        for service_satellite in sorted(
            set(zones_by_gateway[gateway_id]), key=gateway_route._node_key
        ):
            gateway_route._materialize_reachable_path(
                entries,
                choices,
                service_satellite,
                target,
                gateway_id,
            )
    for zone_id, service_satellite in (
        connection_slice.zone_service_assignments.items()
    ):
        target = ZoneRouteTarget(zone_id)
        choices = gateway_route._eligible_shortest_next_hops(
            context, target, service_satellite
        )
        for gateway_id in candidates_by_zone.get(zone_id, ()):
            gateway_route._materialize_reachable_path(
                entries,
                choices,
                gateway_id,
                target,
                service_satellite,
            )
    return RouteSlice(entries)


@pytest.mark.parametrize(
    ("keyword", "value"),
    (
        ("gateway_candidate_count", 0),
        ("gateway_candidate_count", True),
        ("gateway_candidate_max_distance_m", 0.0),
        ("gateway_candidate_max_distance_m", float("inf")),
    ),
)
def test_gateway_policy_configuration_fails_closed(keyword, value) -> None:
    arguments = {
        "gateway_candidate_count": 1,
        "gateway_candidate_max_distance_m": None,
    }
    arguments[keyword] = value
    with pytest.raises(ValueError):
        GatewayHopCountRouteModel(**arguments)


def test_gateway_candidates_are_static_ordered_bounded_and_two_way() -> None:
    _, topology, satellites, stations = _topology()
    connections = _connected_slice(satellites, stations)
    model = GatewayHopCountRouteModel(
        gateway_candidate_count=2,
        gateway_candidate_max_distance_m=None,
    )

    routes = model.compute_route_slice(
        topology, EPOCH, connections, RouteSlice()
    )
    zone_target = ZoneRouteTarget("zone")
    first_target = GroundStationRouteTarget(stations[0].node_id)
    second_target = GroundStationRouteTarget(stations[1].node_id)
    third_target = GroundStationRouteTarget(stations[2].node_id)

    assert routes.get_next_hop(stations[0].node_id, zone_target) == (
        satellites[0].node_id
    )
    assert routes.get_next_hop(satellites[0].node_id, zone_target) == (
        satellites[1].node_id
    )
    assert routes.get_next_hop(stations[1].node_id, zone_target) == (
        satellites[1].node_id
    )
    assert routes.get_next_hop(satellites[1].node_id, first_target) == (
        satellites[0].node_id
    )
    assert routes.get_next_hop(satellites[0].node_id, first_target) == (
        stations[0].node_id
    )
    assert routes.get_next_hop(satellites[1].node_id, second_target) == (
        stations[1].node_id
    )
    with pytest.raises(KeyError):
        routes.get_next_hop(satellites[1].node_id, third_target)
    assert sum(len(targets) for targets in routes.routes.values()) == 6

    repeated = model.compute_route_slice(
        topology,
        EPOCH + timedelta(seconds=10),
        ConnectionSlice(
            connections.connections,
            {"zone": satellites[0].node_id},
        ),
        routes,
    )
    assert model._gateway_candidates_by_zone == {
        "zone": (stations[0].node_id, stations[1].node_id)
    }
    assert repeated.routes


def test_gateway_count_distance_tie_and_unreachable_candidates() -> None:
    _, topology, satellites, stations = _topology()
    count_one = GatewayHopCountRouteModel(
        gateway_candidate_count=1,
        gateway_candidate_max_distance_m=1.0,
    ).compute_route_slice(
        topology,
        EPOCH,
        _connected_slice(satellites, stations),
        RouteSlice(),
    )
    assert sum(len(targets) for targets in count_one.routes.values()) == 4

    unreachable = GatewayHopCountRouteModel(
        gateway_candidate_count=1,
        gateway_candidate_max_distance_m=None,
    ).compute_route_slice(
        topology,
        EPOCH,
        ConnectionSlice(
            (
                _connection(satellites[0].node_id, satellites[1].node_id),
                _connection(stations[1].node_id, satellites[1].node_id),
            ),
            {"zone": satellites[1].node_id},
        ),
        RouteSlice(),
    )
    assert not unreachable.routes


def test_gateway_routes_reuse_one_zone_tree_per_service_satellite(
    monkeypatch,
) -> None:
    zone_ids = ("zone-a", "zone-b", "zone-c")
    _, topology, satellites, stations = _topology(zone_ids)
    base = _connected_slice(satellites, stations)
    connections = ConnectionSlice(
        base.connections,
        {zone_id: satellites[1].node_id for zone_id in zone_ids},
    )
    model = GatewayHopCountRouteModel(
        gateway_candidate_count=2,
        gateway_candidate_max_distance_m=None,
    )
    expected = _route_slice_without_service_tree_reuse(
        model, topology, connections
    )
    original = gateway_route._eligible_shortest_next_hops
    calls = []

    def counted(context, target, terminal, **kwargs):
        calls.append((target, terminal))
        assert kwargs["required_sources"]
        return original(context, target, terminal, **kwargs)

    monkeypatch.setattr(
        gateway_route, "_eligible_shortest_next_hops", counted
    )
    actual = model.compute_route_slice(
        topology, EPOCH, connections, RouteSlice()
    )

    assert actual == expected
    assert sum(
        isinstance(target, ZoneRouteTarget) for target, _terminal in calls
    ) == 1
    assert sum(
        isinstance(target, GroundStationRouteTarget)
        for target, _terminal in calls
    ) == 2
    assert len(calls) == 3
    assert 2 + len(zone_ids) == 5  # Pre-optimization tree-build count.

    context = _prepare_routing_policy_context(topology, connections)
    assert all(
        isinstance(neighbors, tuple)
        for neighbors in context.graph.values()
    )
    assert all(
        neighbors == tuple(sorted(neighbors, key=gateway_route._node_key))
        for neighbors in context.graph.values()
    )


class _FixedPositionModel:
    def __init__(self, positions):
        self.positions = positions

    def compute_positions(self, topology, timestamp):
        return self.positions


class _FixedConnectionModel:
    def __init__(self, connections):
        self.connections = connections

    def compute_connection_slice(
        self, topology, timestamp, positions, previous_connection_slice
    ):
        return self.connections


def test_slicer_validates_sparse_gateway_routes_without_owning_demand() -> None:
    simulator, _, satellites, stations = _topology()
    connections = _connected_slice(satellites, stations)
    positions = {
        satellites[0].node_id: EarthFixedPosition(7_000_000.0, 0.0, 0.0),
        satellites[1].node_id: EarthFixedPosition(7_000_000.0, 1_000.0, 0.0),
        **{
            station.node_id: geodetic_to_ecef(
                station.latitude_deg, station.longitude_deg, station.elevation_m
            )
            for station in stations
        },
    }
    slicer = simulator.create_slicer(
        position_model=_FixedPositionModel(positions),
        connection_model=_FixedConnectionModel(connections),
        route_model=GatewayHopCountRouteModel(
            gateway_candidate_count=1,
            gateway_candidate_max_distance_m=None,
        ),
    )

    published = slicer.create_next_slice()
    assert sum(
        len(targets) for targets in published.route_slice.routes.values()
    ) == 4
    assert slicer.resolve_path(
        1,
        stations[0].node_id,
        ZoneRouteTarget("zone"),
    ) == (
        stations[0].node_id,
        satellites[0].node_id,
        satellites[1].node_id,
    )
