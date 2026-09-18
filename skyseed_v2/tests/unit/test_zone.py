from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from math import inf, nan

import pytest

import skyseed_v2
from skyseed_v2.configuration.simulator import (
    LatitudeLongitudeRectangleDefinition,
    ZoneDefinition,
    ZoneLayoutDefinition,
    ZoneLayoutDefinitionLoadError,
    load_zone_layout_definition,
)
from skyseed_v2.simulator import (
    CircularOrbitPositionModel,
    DefaultZoneMembershipModel,
    LatitudeLongitudeRectangle,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    SphericalCap,
    Zone,
)
from skyseed_v2.simulator.input import SimulatorStaticInput


def rectangle_definition(
    south: float = -10.0,
    north: float = 10.0,
    west: float = -20.0,
    east: float = 20.0,
) -> LatitudeLongitudeRectangleDefinition:
    return LatitudeLongitudeRectangleDefinition(south, north, west, east)


def zone_definition(
    zone_id: str,
    *,
    priority: int = 0,
    geometry: LatitudeLongitudeRectangleDefinition | None = None,
) -> ZoneDefinition:
    return ZoneDefinition(
        zone_id=zone_id,
        priority=priority,
        geometry=geometry or rectangle_definition(),
    )


def layout_definition(
    *zones: ZoneDefinition, allow_overlap: bool = True
) -> ZoneLayoutDefinition:
    return ZoneLayoutDefinition(zones=zones, allow_overlap=allow_overlap)


def create_zone_physical_world(
    definition: ZoneLayoutDefinition,
    model=None,
):
    simulator = Simulator(
        SimulationDefinition(
            static_input=SimulatorStaticInput(),
            timeline=SimulationTimeline(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
                timedelta(seconds=10),
            ),
        )
    )
    physical_world = simulator.create_physical_world(
        position_model=CircularOrbitPositionModel(),
        zone_layout_definition=definition,
        zone_membership_model=model,
    )
    return simulator, physical_world


def test_programmatic_definitions_are_canonical_immutable_and_snapshotted() -> None:
    rectangle = rectangle_definition(west=180.0, east=-170.0)
    source = [zone_definition("zone-b"), zone_definition("zone-a")]
    definition = ZoneLayoutDefinition(zones=iter(source), allow_overlap=True)
    source.clear()

    assert rectangle.west_longitude_deg == -180.0
    assert tuple(zone.zone_id for zone in definition.zones) == (
        "zone-a",
        "zone-b",
    )
    assert not hasattr(rectangle, "__dict__")
    assert not hasattr(definition, "__dict__")
    with pytest.raises(FrozenInstanceError):
        definition.allow_overlap = False


def test_definition_rejects_invalid_ids_priorities_layouts_and_rectangles() -> None:
    with pytest.raises(ValueError, match="zone_id"):
        zone_definition("invalid id")
    with pytest.raises(TypeError, match="priority"):
        zone_definition("zone", priority=True)
    with pytest.raises(ValueError, match="at least one"):
        ZoneLayoutDefinition(zones=(), allow_overlap=False)
    with pytest.raises(ValueError, match="duplicate"):
        layout_definition(zone_definition("zone"), zone_definition("zone"))
    with pytest.raises(TypeError, match="allow_overlap"):
        ZoneLayoutDefinition(zones=(zone_definition("zone"),), allow_overlap=1)

    for value in (nan, inf, -inf):
        with pytest.raises(ValueError, match="finite"):
            rectangle_definition(south=value)
    for value in ("0", None, True):
        with pytest.raises(TypeError, match="finite"):
            rectangle_definition(west=value)
    for bounds in (
        (-91.0, 0.0, -10.0, 10.0),
        (0.0, 91.0, -10.0, 10.0),
        (10.0, 10.0, -10.0, 10.0),
        (20.0, 10.0, -10.0, 10.0),
        (-10.0, 10.0, -181.0, 10.0),
        (-10.0, 10.0, -10.0, 181.0),
        (-10.0, 10.0, 180.0, -180.0),
        (-10.0, 10.0, 10.0, 10.0),
    ):
        with pytest.raises(ValueError):
            rectangle_definition(*bounds)


def test_zone_yaml_and_programmatic_definition_are_equivalent(tmp_path) -> None:
    path = tmp_path / "zones.yaml"
    path.write_text(
        """allow_overlap: true
zones:
  - zone_id: zone-b
    geometry:
      kind: latitude-longitude-rectangle
      south_latitude_deg: -10
      north_latitude_deg: 10
      west_longitude_deg: 180
      east_longitude_deg: -170
  - zone_id: zone-a
    priority: 7
    geometry:
      kind: latitude-longitude-rectangle
      south_latitude_deg: 20
      north_latitude_deg: 30
      west_longitude_deg: 40
      east_longitude_deg: 50
""",
        encoding="utf-8",
    )
    expected = layout_definition(
        zone_definition(
            "zone-a",
            priority=7,
            geometry=rectangle_definition(20, 30, 40, 50),
        ),
        zone_definition(
            "zone-b", geometry=rectangle_definition(-10, 10, 180, -170)
        ),
    )

    assert load_zone_layout_definition(path) == expected


def test_zone_yaml_rejects_unknown_geometry_kind_and_invalid_layout(tmp_path) -> None:
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text(
        """allow_overlap: false
zones:
  - zone_id: zone
    geometry:
      kind: polygon
      south_latitude_deg: -10
      north_latitude_deg: 10
      west_longitude_deg: -10
      east_longitude_deg: 10
""",
        encoding="utf-8",
    )
    with pytest.raises(ZoneLayoutDefinitionLoadError, match="unsupported"):
        load_zone_layout_definition(unknown)

    empty = tmp_path / "empty.yaml"
    empty.write_text("allow_overlap: false\nzones: []\n", encoding="utf-8")
    with pytest.raises(ZoneLayoutDefinitionLoadError, match="at least one"):
        load_zone_layout_definition(empty)


def test_zone_yaml_error_contract_is_owned_by_simulator_configuration() -> None:
    from skyseed_v2.configuration.emulator import (
        StaticEmulatorConfigLoadError,
    )
    from skyseed_v2.configuration.simulator import (
        ZoneLayoutDefinitionLoadError as exported_error,
    )

    assert exported_error is ZoneLayoutDefinitionLoadError
    assert exported_error is not StaticEmulatorConfigLoadError
    assert exported_error.__module__ == "skyseed_v2.configuration.simulator.zones.yaml"


def test_rectangle_contains_normal_boundaries_corners_and_dateline() -> None:
    normal = LatitudeLongitudeRectangle(-10, 10, -20, 20)
    for point in (
        (0, 0),
        (-10, 0),
        (10, 0),
        (0, -20),
        (0, 20),
        (-10, -20),
        (-10, 20),
        (10, -20),
        (10, 20),
    ):
        assert normal.contains(*point)
    assert not normal.contains(11, 0)
    assert not normal.contains(0, 21)

    wrapped = LatitudeLongitudeRectangle(-10, 10, 170, -170)
    assert wrapped.contains(0, 175)
    assert wrapped.contains(0, -175)
    assert wrapped.contains(0, 180)
    assert wrapped.contains(0, -180)
    assert not wrapped.contains(0, 0)


def test_rectangle_poles_ignore_longitude_only_when_latitude_is_included() -> None:
    north = LatitudeLongitudeRectangle(80, 90, 10, 20)
    south = LatitudeLongitudeRectangle(-90, -80, 10, 20)

    assert north.contains(90, -150)
    assert south.contains(-90, -150)
    assert not north.contains(-90, 15)
    assert not south.contains(90, 15)
    assert not north.contains(89, -150)


def test_rectangle_query_validation_and_representative_centers() -> None:
    normal = LatitudeLongitudeRectangle(-20, 10, 20, 60)
    wrapped = LatitudeLongitudeRectangle(-20, 20, 170, -150)

    assert normal.representative_center == (-5.0, 40.0)
    assert wrapped.representative_center == (0.0, -170.0)
    for latitude, longitude in (
        ("0", 0),
        (0, "0"),
        (True, 0),
        (0, None),
    ):
        with pytest.raises(TypeError):
            normal.contains(latitude, longitude)
    for latitude, longitude in (
        (nan, 0),
        (0, inf),
        (-91, 0),
        (91, 0),
        (0, -181),
        (0, 181),
    ):
        with pytest.raises(ValueError):
            normal.contains(latitude, longitude)


def test_overlap_policy_handles_area_edges_corners_poles_and_wrapping() -> None:
    left = zone_definition("left", geometry=rectangle_definition(0, 20, 0, 20))
    overlapping = zone_definition(
        "overlap", geometry=rectangle_definition(10, 30, 10, 30)
    )
    for ordered_zones in ((left, overlapping), (overlapping, left)):
        with pytest.raises(ValueError, match="positive-area"):
            layout_definition(*ordered_zones, allow_overlap=False)
    assert layout_definition(left, overlapping, allow_overlap=True).allow_overlap

    shared_edge = zone_definition(
        "edge", geometry=rectangle_definition(0, 20, 20, 40)
    )
    shared_corner = zone_definition(
        "corner", geometry=rectangle_definition(20, 30, 20, 30)
    )
    polar_peer = zone_definition(
        "polar-peer", geometry=rectangle_definition(80, 90, 30, 40)
    )
    polar_left = zone_definition(
        "polar-left", geometry=rectangle_definition(80, 90, 10, 20)
    )
    layout_definition(left, shared_edge, allow_overlap=False)
    layout_definition(left, shared_corner, allow_overlap=False)
    layout_definition(polar_left, polar_peer, allow_overlap=False)

    wrapped = zone_definition(
        "wrapped", geometry=rectangle_definition(0, 20, 170, -170)
    )
    wrapped_overlap = zone_definition(
        "wrapped-overlap", geometry=rectangle_definition(10, 30, 175, -175)
    )
    with pytest.raises(ValueError, match="positive-area"):
        layout_definition(wrapped_overlap, wrapped, allow_overlap=False)


def test_default_membership_is_sorted_and_resolution_is_deterministic() -> None:
    low = zone_definition("z-low", priority=1)
    high = zone_definition("a-high", priority=2)
    _, physical_world = create_zone_physical_world(layout_definition(low, high))

    assert physical_world.find_zones(0, 0) == (
        physical_world.get_zone("a-high"),
        physical_world.get_zone("z-low"),
    )
    assert physical_world.resolve_zone(0, 0) is physical_world.get_zone("a-high")
    assert physical_world.find_zones(50, 50) == ()
    with pytest.raises(LookupError, match="no Zone"):
        physical_world.resolve_zone(50, 50)


def test_physical_world_intersection_facade_delegates_with_identical_overlap_results() -> None:
    broad = zone_definition(
        "broad",
        geometry=rectangle_definition(-20, 20, -30, 30),
    )
    focused = zone_definition(
        "focused",
        priority=10,
        geometry=rectangle_definition(-5, 5, -5, 5),
    )
    _, physical_world = create_zone_physical_world(
        layout_definition(broad, focused, allow_overlap=True)
    )
    layout = physical_world.get_zone_layout()
    coverage = SphericalCap((0.0, 0.0), 1.0)

    direct = layout.find_intersecting_zones(coverage)
    through_facade = physical_world.find_intersecting_zones(coverage)

    assert through_facade == direct
    assert all(
        facade_zone is layout_zone
        for facade_zone, layout_zone in zip(through_facade, direct, strict=True)
    )
    assert tuple(zone.zone_id for zone in through_facade) == (
        "broad",
        "focused",
    )


def test_resolution_uses_haversine_center_distance_then_zone_id() -> None:
    dateline = zone_definition(
        "dateline",
        geometry=rectangle_definition(-20, 20, 170, -170),
    )
    distant = zone_definition(
        "distant",
        geometry=rectangle_definition(-20, 20, -180, -140),
    )
    _, physical_world = create_zone_physical_world(layout_definition(distant, dateline))
    assert physical_world.resolve_zone(0, -179).zone_id == "dateline"

    tied_a = zone_definition("a-tie")
    tied_b = zone_definition("b-tie")
    _, physical_world = create_zone_physical_world(layout_definition(tied_b, tied_a))
    assert physical_world.resolve_zone(0, 0).zone_id == "a-tie"


class ReverseModel:
    def find_zones(self, zones, latitude_deg, longitude_deg):
        return reversed(zones)

    def resolve_zone(self, matches, latitude_deg, longitude_deg):
        return matches[-1]


class ExternalZoneModel:
    def find_zones(self, zones, latitude_deg, longitude_deg):
        owned = zones[0]
        return (Zone(owned.zone_id, owned.priority, owned.geometry),)

    def resolve_zone(self, matches, latitude_deg, longitude_deg):
        return matches[0]


class DuplicateZoneModel:
    def find_zones(self, zones, latitude_deg, longitude_deg):
        return (zones[0], zones[0])

    def resolve_zone(self, matches, latitude_deg, longitude_deg):
        return matches[0]


class NonMatchWinnerModel:
    def find_zones(self, zones, latitude_deg, longitude_deg):
        self.winner = zones[1]
        return (zones[0],)

    def resolve_zone(self, matches, latitude_deg, longitude_deg):
        return self.winner


def test_custom_model_is_injectable_but_layout_normalizes_its_results() -> None:
    model = ReverseModel()
    _, physical_world = create_zone_physical_world(
        layout_definition(zone_definition("b"), zone_definition("a")),
        model,
    )
    layout = physical_world.get_zone_layout()

    assert layout.get_membership_model() is model
    assert layout.get_membership_model() is layout.get_membership_model()
    assert tuple(zone.zone_id for zone in layout.find_zones(0, 0)) == (
        "a",
        "b",
    )
    assert layout.resolve_zone(0, 0) is layout.get_zone("b")
    assert not hasattr(layout, "set_zone_model")
    assert not hasattr(layout, "service_satellites")


def test_default_membership_model_has_one_readonly_owner_query() -> None:
    _, physical_world = create_zone_physical_world(
        layout_definition(zone_definition("zone"))
    )
    layout = physical_world.get_zone_layout()
    model = layout.get_membership_model()

    assert isinstance(model, DefaultZoneMembershipModel)
    assert model is layout.get_membership_model()
    assert not hasattr(layout, "set_membership_model")
    with pytest.raises((AttributeError, FrozenInstanceError)):
        layout._membership_model = ReverseModel()


@pytest.mark.parametrize(
    ("model", "message"),
    (
        (ExternalZoneModel(), "not owned"),
        (DuplicateZoneModel(), "duplicate"),
        (NonMatchWinnerModel(), "physical match"),
    ),
)
def test_custom_model_cannot_inject_or_duplicate_zones_or_select_nonmatch(
    model, message
) -> None:
    _, physical_world = create_zone_physical_world(
        layout_definition(zone_definition("a"), zone_definition("b")),
        model,
    )
    with pytest.raises(ValueError, match=message):
        physical_world.resolve_zone(0, 0)


def test_physical_world_zone_lifecycle_facade_identity_and_atomic_failure() -> None:
    simulation_definition = SimulationDefinition(
        SimulatorStaticInput(),
            SimulationTimeline(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                timedelta(seconds=10),
            ),
    )
    unconfigured = Simulator(simulation_definition).create_physical_world(
        position_model=CircularOrbitPositionModel()
    )
    for operation in (
        unconfigured.get_zone_layout,
        lambda: unconfigured.get_zone("zone"),
        lambda: unconfigured.find_zones(0, 0),
        lambda: unconfigured.find_intersecting_zones(
            SphericalCap((0.0, 0.0), 1.0)
        ),
        lambda: unconfigured.resolve_zone(0, 0),
    ):
        with pytest.raises(RuntimeError, match="ZoneLayout"):
            operation()

    _, physical_world = create_zone_physical_world(
        layout_definition(zone_definition("zone"))
    )
    layout = physical_world.get_zone_layout()
    assert physical_world.get_zone("zone") is layout.get_zone("zone")
    assert physical_world.find_zones(0, 0)[0] is layout.zones[0]
    assert physical_world.resolve_zone(0, 0) is layout.zones[0]
    with pytest.raises(KeyError, match="Zone not found"):
        physical_world.get_zone("missing")
    assert not hasattr(physical_world, "set_zone_layout")
    assert not hasattr(physical_world, "set_zone_model")

    simulator = Simulator(simulation_definition)
    with pytest.raises(ValueError, match="requires"):
        simulator.create_physical_world(
            position_model=CircularOrbitPositionModel(),
            zone_membership_model=DefaultZoneMembershipModel()
        )
    with pytest.raises(RuntimeError, match="not been created"):
        simulator.get_physical_world()

    class InvalidModel:
        def find_zones(self, zones, latitude_deg, longitude_deg):
            return zones

    with pytest.raises(TypeError, match="ZoneMembershipModel"):
        simulator.create_physical_world(
            position_model=CircularOrbitPositionModel(),
            zone_layout_definition=layout_definition(zone_definition("zone")),
            zone_membership_model=InvalidModel(),
        )
    with pytest.raises(RuntimeError, match="not been created"):
        simulator.get_physical_world()


def test_zone_public_types_remain_with_owner_modules_not_root() -> None:
    for name in (
        "LatitudeLongitudeRectangleDefinition",
        "ZoneDefinition",
        "ZoneLayoutDefinition",
        "LatitudeLongitudeRectangle",
        "Zone",
        "ZoneMembershipModel",
        "DefaultZoneMembershipModel",
    ):
        assert not hasattr(skyseed_v2, name)


@pytest.mark.parametrize("alignment", ("antimeridian-centered", "prime-meridian-centered"))
def test_coarse_global_grid_has_two_cells_in_every_band(alignment):
    from skyseed_v2.configuration.simulator.zones import build_global_zone_layout

    definition = build_global_zone_layout(equatorial_cells=2, alignment=alignment)
    assert len(definition.zones) == 48
    assert not definition.allow_overlap
    _, world = create_zone_physical_world(definition)
    layout = world.get_zone_layout()
    for band in range(24):
        cells = layout.zones[band * 2:band * 2 + 2]
        assert [zone.zone_id for zone in cells] == [f"zone-b{band:02d}-c000", f"zone-b{band:02d}-c001"]
        for zone in cells:
            assert zone.geometry.west_longitude_deg != zone.geometry.east_longitude_deg
            assert zone.contains(*zone.representative_point)
        latitude = -90 + (band + .5) * 7.5
        for longitude in (-180, -135, -90, -45, 0, 45, 90, 135, 180):
            assert layout.find_zones(latitude, longitude)
    assert layout.find_zones(-90, 180)
    assert layout.find_zones(90, -180)


@pytest.mark.parametrize("value,error", ((True, TypeError), (2.0, TypeError), (1, ValueError), (0, ValueError), (-1, ValueError)))
def test_global_grid_rejects_fewer_than_two_equatorial_cells(value, error):
    from skyseed_v2.configuration.simulator.zones import build_global_zone_layout

    with pytest.raises(error, match="equatorial_cells"):
        build_global_zone_layout(equatorial_cells=value)
