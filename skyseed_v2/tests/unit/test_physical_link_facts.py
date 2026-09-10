from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from skyseed_v2.configuration.simulator import (
    LatitudeLongitudeRectangleDefinition,
    ZoneDefinition,
    ZoneLayoutDefinition,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.simulator import (
    EarthFixedPosition,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)
from skyseed_v2.simulator.geometry import elevation_deg, geodetic_to_ecef
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)
from skyseed_v2.simulator.models import GeometricPhysicalLinkModel
from skyseed_v2.simulator.physical.physical_link import (
    GroundStationAccessCandidate,
    InterSatelliteLinkCandidate,
    PhysicalLinkModel,
    PhysicalLinkObservation,
    PhysicalLinkRequest,
    PhysicalLinkWorkCounts,
    ZoneServiceCandidate,
)


EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
LIGHT_SPEED_M_PER_S = 299_792_458.0


class _FixedPositionModel:
    def __init__(self, positions):
        self.positions = positions

    def compute_positions(self, physical_world, timestamp):
        return self.positions


def _satellite(
    satellite_id: str,
    *,
    orbit_id: str,
    orbit_index: int,
    right_ascension_deg: float,
) -> SatelliteSimulationInput:
    return SatelliteSimulationInput(
        node_id=NodeId(65001, "satellite", satellite_id),
        satellite_id=satellite_id,
        shell_id="shell-a",
        orbit_id=orbit_id,
        orbit_index=orbit_index,
        altitude_km=550.0,
        inclination_deg=53.0,
        right_ascension_deg=right_ascension_deg,
        mean_anomaly_deg=orbit_index * 10.0,
        epoch_utc=EPOCH,
        maximum_simultaneous_inter_satellite_links=4,
    )


def _ground_station() -> GroundStationSimulationInput:
    return GroundStationSimulationInput(
        node_id=NodeId(65001, "ground-station", "ground-a"),
        ground_station_id="ground-a",
        latitude_deg=0.0,
        longitude_deg=0.0,
        elevation_m=0.0,
        station_type="gateway",
        operator="Skyseed",
    )


def _physical_world_fixture():
    satellites = tuple(
        _satellite(
            f"sat-{index}",
            orbit_id=f"orbit-{index}",
            orbit_index=0,
            right_ascension_deg=index * 90.0,
        )
        for index in range(4)
    )
    # Add a second Satellite to plane zero so same-plane classification is
    # independently exercised from cyclic cross-plane separation.
    satellites += (
        _satellite(
            "sat-same-plane",
            orbit_id="orbit-0",
            orbit_index=1,
            right_ascension_deg=0.0,
        ),
    )
    ground_station = _ground_station()
    positions = {
        ground_station.node_id: geodetic_to_ecef(0.0, 0.0, 0.0),
        satellites[0].node_id: geodetic_to_ecef(0.0, 0.0, 550_000.0),
        satellites[1].node_id: geodetic_to_ecef(0.0, 1.0, 550_000.0),
        satellites[2].node_id: geodetic_to_ecef(0.0, 2.0, 550_000.0),
        satellites[3].node_id: geodetic_to_ecef(0.0, 3.0, 550_000.0),
        satellites[4].node_id: geodetic_to_ecef(0.0, 0.5, 550_000.0),
    }
    definition = SimulationDefinition(
        SimulatorStaticInput(satellites, (ground_station,)),
        SimulationTimeline(EPOCH, EPOCH, timedelta(seconds=5)),
    )
    physical_world = Simulator(definition).create_physical_world(
        position_model=_FixedPositionModel(positions),
        zone_layout_definition=ZoneLayoutDefinition(
            zones=(
                ZoneDefinition(
                    zone_id="zone-a",
                    geometry=LatitudeLongitudeRectangleDefinition(
                        south_latitude_deg=-1.0,
                        north_latitude_deg=1.0,
                        west_longitude_deg=-1.0,
                        east_longitude_deg=1.0,
                    ),
                ),
            ),
            allow_overlap=False,
        ),
    )
    return physical_world, satellites, ground_station


def _model(*, maximum_isl_distance_m: float = 5_000_000.0):
    return GeometricPhysicalLinkModel(
        maximum_isl_distance_m=maximum_isl_distance_m,
        ground_station_minimum_elevation_deg=10.0,
        ground_station_maximum_distance_m=None,
        zone_coverage_minimum_elevation_deg=10.0,
        propagation_speed_m_per_s=LIGHT_SPEED_M_PER_S,
    )


def test_physical_request_and_candidate_values_are_canonical_and_immutable() -> None:
    first = NodeId(65001, "satellite", "a")
    second = NodeId(65001, "satellite", "b")
    request = PhysicalLinkRequest(
        inter_satellite_pairs=((second, first), (first, second))
    )

    assert request.inter_satellite_pairs == ((first, second),)
    with pytest.raises(FrozenInstanceError):
        request.include_zone_service = False
    with pytest.raises(ValueError, match="distinct"):
        PhysicalLinkRequest(inter_satellite_pairs=((first, first),))

    candidate = InterSatelliteLinkCandidate(
        endpoint_a=second,
        endpoint_b=first,
        same_plane=True,
        cyclic_plane_separation=0,
        distance_m=0.0,
        propagation_delay_us=0,
    )
    # Distinct physical identities may share a test/projection coordinate; the
    # retained legacy geometric path treats that zero-distance ISL as feasible.
    assert (candidate.endpoint_a, candidate.endpoint_b) == (first, second)
    with pytest.raises(FrozenInstanceError):
        candidate.distance_m = 1.0


def test_geometric_physical_observation_is_sparse_bounded_and_deterministic() -> None:
    physical_world, satellites, _ = _physical_world_fixture()
    frame = physical_world.frame_at(EPOCH)
    request = PhysicalLinkRequest(
        inter_satellite_pairs=(
            (satellites[4].node_id, satellites[0].node_id),
            (satellites[0].node_id, satellites[2].node_id),
        ),
        include_ground_station_access=False,
        include_zone_service=False,
    )
    model = _model()

    first = model.compute_observation(physical_world, frame, request)
    second = model.compute_observation(physical_world, frame, request)

    assert isinstance(model, PhysicalLinkModel)
    assert first == second
    assert first.timestamp == frame.timestamp
    assert tuple(
        (
            value.endpoint_a,
            value.endpoint_b,
            value.same_plane,
            value.cyclic_plane_separation,
        )
        for value in first.inter_satellite_candidates
    ) == (
        (
            satellites[0].node_id,
            satellites[2].node_id,
            False,
            2,
        ),
        (
            satellites[0].node_id,
            satellites[4].node_id,
            True,
            0,
        ),
    )
    assert first.ground_station_access_candidates == ()
    assert first.zone_service_candidates == ()
    assert first.work_counts == PhysicalLinkWorkCounts(
        inter_satellite_pairs_requested=2,
        inter_satellite_pairs_evaluated=2,
        inter_satellite_pairs_feasible=2,
    )
    # Other geometrically nearby Satellites were not requested and therefore
    # were never materialized as failed or feasible pair records.
    assert all(
        satellites[1].node_id
        not in (candidate.endpoint_a, candidate.endpoint_b)
        for candidate in first.inter_satellite_candidates
    )


def test_geometric_physical_observation_records_actual_model_compute() -> None:
    physical_world, satellites, _ = _physical_world_fixture()
    observer = _ExecutionObserver()
    physical_world._set_execution_observer(observer)
    frame = physical_world.frame_at(EPOCH)
    request = PhysicalLinkRequest(
        inter_satellite_pairs=(
            (satellites[0].node_id, satellites[2].node_id),
        ),
        include_ground_station_access=False,
        include_zone_service=False,
    )

    observation = _model().compute_observation(
        physical_world, frame, request
    )

    records = tuple(
        value
        for value in observer.snapshot()["records"]
        if value["component"] == "physical-link"
    )
    assert len(records) == 1
    record = records[0]
    assert record["stage"] == "physical-link-observation"
    assert record["scope"] == {"timestamp": EPOCH.isoformat()}
    assert record["duration_seconds"] > 0
    assert record["work"]["inter_satellite_pairs_requested"] == 1
    assert record["work"]["inter_satellite_pairs_evaluated"] == 1
    assert record["work"]["inter_satellite_pairs_feasible"] == len(
        observation.inter_satellite_candidates
    )


def test_geometric_physical_facts_include_elevation_range_los_and_delay() -> None:
    physical_world, satellites, ground_station = _physical_world_fixture()
    frame = physical_world.frame_at(EPOCH)
    request = PhysicalLinkRequest(
        inter_satellite_pairs=(
            (satellites[0].node_id, satellites[1].node_id),
        )
    )

    observation = _model().compute_observation(physical_world, frame, request)

    isl = observation.inter_satellite_candidates[0]
    assert isl.distance_m > 0.0
    assert isl.propagation_delay_us == pytest.approx(
        isl.distance_m / LIGHT_SPEED_M_PER_S * 1_000_000.0,
        abs=1.0,
    )
    ground = next(
        value
        for value in observation.ground_station_access_candidates
        if value.ground_station_id == ground_station.node_id
        and value.satellite_id == satellites[0].node_id
    )
    assert ground.elevation_deg == pytest.approx(90.0)
    assert ground.distance_m == pytest.approx(550_000.0, abs=1e-5)
    assert ground.propagation_delay_us > 0
    assert ground.elevation_deg == elevation_deg(
        frame.get_position(ground.ground_station_id),
        frame.get_position(ground.satellite_id),
    )
    zone = next(
        value
        for value in observation.zone_service_candidates
        if value.zone_id == "zone-a"
        and value.satellite_id == satellites[0].node_id
    )
    assert zone.elevation_deg == pytest.approx(90.0)
    assert zone.distance_m == pytest.approx(550_000.0, abs=1e-5)
    representative_latitude, representative_longitude = (
        physical_world.get_zone("zone-a").representative_point
    )
    representative_position = geodetic_to_ecef(
        representative_latitude,
        representative_longitude,
        0.0,
    )
    assert zone.elevation_deg == elevation_deg(
        representative_position,
        frame.get_position(zone.satellite_id),
    )
    assert observation.work_counts.ground_station_pairs_feasible == len(
        observation.ground_station_access_candidates
    )
    assert observation.work_counts.zone_service_pairs_feasible == len(
        observation.zone_service_candidates
    )

    out_of_range = _model(maximum_isl_distance_m=1.0).compute_observation(
        physical_world,
        frame,
        PhysicalLinkRequest(
            inter_satellite_pairs=(
                (satellites[0].node_id, satellites[1].node_id),
            ),
            include_ground_station_access=False,
            include_zone_service=False,
        ),
    )
    assert out_of_range.inter_satellite_candidates == ()
    assert out_of_range.work_counts.inter_satellite_pairs_requested == 1
    assert out_of_range.work_counts.inter_satellite_pairs_feasible == 0


def test_geometric_physical_isl_requires_earth_clear_line_of_sight() -> None:
    satellites = (
        _satellite(
            "near-side",
            orbit_id="orbit-0",
            orbit_index=0,
            right_ascension_deg=0.0,
        ),
        _satellite(
            "far-side",
            orbit_id="orbit-0",
            orbit_index=1,
            right_ascension_deg=0.0,
        ),
    )
    positions = {
        satellites[0].node_id: geodetic_to_ecef(0.0, 0.0, 550_000.0),
        satellites[1].node_id: geodetic_to_ecef(0.0, 180.0, 550_000.0),
    }
    definition = SimulationDefinition(
        SimulatorStaticInput(satellites),
        SimulationTimeline(EPOCH, EPOCH, timedelta(seconds=5)),
    )
    physical_world = Simulator(definition).create_physical_world(
        position_model=_FixedPositionModel(positions)
    )
    frame = physical_world.frame_at(EPOCH)

    observation = _model(
        maximum_isl_distance_m=20_000_000.0
    ).compute_observation(
        physical_world,
        frame,
        PhysicalLinkRequest(
            inter_satellite_pairs=(
                (satellites[0].node_id, satellites[1].node_id),
            ),
            include_ground_station_access=False,
            include_zone_service=False,
        ),
    )

    assert observation.inter_satellite_candidates == ()
    assert observation.work_counts.inter_satellite_pairs_evaluated == 1
    assert observation.work_counts.inter_satellite_pairs_feasible == 0


def test_physical_observation_has_no_operational_or_slicer_contract() -> None:
    prohibited = {
        "selected",
        "acquiring",
        "operational",
        "connection",
        "bandwidth_bps",
        "packet_loss_ppm",
        "serving_winner",
        "route",
        "forwarding",
        "planner_reason",
        "terminal_slot",
    }
    for value_type in (
        InterSatelliteLinkCandidate,
        GroundStationAccessCandidate,
        ZoneServiceCandidate,
        PhysicalLinkObservation,
    ):
        assert prohibited.isdisjoint(field.name for field in fields(value_type))

    repository = Path(__file__).parents[2]
    contract_source = (
        repository
        / "src/skyseed_v2/simulator/physical/physical_link.py"
    ).read_text(encoding="utf-8")
    core_source = (
        repository
        / "src/skyseed_v2/simulator/models/_geometric_physical.py"
    ).read_text(encoding="utf-8")
    model_source = (
        repository
        / "src/skyseed_v2/simulator/models/geometric_physical_link.py"
    ).read_text(encoding="utf-8")
    assert "simulator.slicing" not in contract_source + core_source + model_source
    assert "..slicing" not in contract_source + core_source + model_source


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    (
        ("maximum_isl_distance_m", 0.0),
        ("ground_station_minimum_elevation_deg", 91.0),
        ("ground_station_maximum_distance_m", 0.0),
        ("zone_coverage_minimum_elevation_deg", -91.0),
        ("propagation_speed_m_per_s", 0.0),
    ),
)
def test_geometric_physical_configuration_fails_at_construction(
    field_name: str, invalid: float
) -> None:
    values = {
        "maximum_isl_distance_m": 1.0,
        "ground_station_minimum_elevation_deg": 0.0,
        "ground_station_maximum_distance_m": None,
        "zone_coverage_minimum_elevation_deg": 0.0,
        "propagation_speed_m_per_s": 1.0,
    }
    values[field_name] = invalid
    with pytest.raises(ValueError, match=field_name):
        GeometricPhysicalLinkModel(**values)


def test_models_package_exports_geometric_physical_link_model() -> None:
    from skyseed_v2.simulator import models

    assert models.GeometricPhysicalLinkModel is GeometricPhysicalLinkModel
    assert "GeometricPhysicalLinkModel" in models.__all__


def test_physical_link_contracts_are_exported_by_their_owner_facades() -> None:
    from skyseed_v2 import simulator
    from skyseed_v2.simulator import physical

    contracts = {
        "GroundStationAccessCandidate": GroundStationAccessCandidate,
        "InterSatelliteLinkCandidate": InterSatelliteLinkCandidate,
        "PhysicalLinkModel": PhysicalLinkModel,
        "PhysicalLinkObservation": PhysicalLinkObservation,
        "PhysicalLinkRequest": PhysicalLinkRequest,
        "PhysicalLinkWorkCounts": PhysicalLinkWorkCounts,
        "ZoneServiceCandidate": ZoneServiceCandidate,
    }
    for name, contract in contracts.items():
        assert getattr(physical, name) is contract
        assert getattr(simulator, name) is contract
        assert name in physical.__all__
        assert name in simulator.__all__
    assert simulator.GeometricPhysicalLinkModel is GeometricPhysicalLinkModel
    assert "GeometricPhysicalLinkModel" in simulator.__all__


def test_query_rank_changes_keep_retained_pairs_and_distinguish_not_returned():
    from dataclasses import replace
    world, satellites, station = _physical_world_fixture()
    model = _model()
    frame = world.frame_at(EPOCH)
    wide = model.compute_observation(world, frame, PhysicalLinkRequest())
    retained = (station.node_id, satellites[3].node_id)
    narrow = model.compute_observation(world, frame, PhysicalLinkRequest(
        ground_station_candidate_limit=1, retained_ground_station_pairs=(retained,)))
    assert len(narrow.ground_station_access_candidates) == 2
    assert retained in {(c.ground_station_id,c.satellite_id) for c in narrow.ground_station_access_candidates}
    assert narrow.work_counts.ground_station_pairs_not_returned == len(wide.ground_station_access_candidates)-2
    assert model.compute_observation(world,frame,PhysicalLinkRequest()) == wide


def test_contact_proof_keys_survive_changed_pair_query_order():
    from skyseed_v2.simulator.models._contact_prediction import _ContactPrediction
    from skyseed_v2.simulator.physical import PhysicalContactWindow
    world, satellites, _ = _physical_world_fixture()
    first=(satellites[0].node_id,satellites[1].node_id)
    second=(satellites[0].node_id,satellites[2].node_id)
    proof=_ContactPrediction.__new__(_ContactPrediction)
    proof.pair_indexes={first:0};proof.start=EPOCH;proof.stable=frozenset({0})
    proof._stable_window=PhysicalContactWindow(EPOCH,None);proof.windows={'isl':{}}
    assert proof.window('isl',first,EPOCH).end_timestamp is None
    # An unpredicted pair at numeric index zero in another query must not
    # inherit the original pair's all-future analytical proof.
    assert proof.window('isl',second,EPOCH) is None
