from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from skyseed_v2.configuration import (
    LatitudeLongitudeRectangleDefinition,
    ZoneDefinition,
    ZoneLayoutDefinition,
)
from skyseed_v2.node_id import NodeId
from skyseed_v2.simulator import (
    LatitudeLongitudeRectangle,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    Topology,
    Zone,
)
from skyseed_v2.simulator.static_input import (
    GroundStationSimulationInput,
    SimulatorStaticInput,
)
from tools.zone import collect_zone_data, write_zone_tool
from tools.zone_probes import select_zone_probes


def zone(
    zone_id: str,
    south: float,
    north: float,
    west: float,
    east: float,
    *,
    priority: int = 0,
) -> Zone:
    return Zone(
        zone_id=zone_id,
        priority=priority,
        geometry=LatitudeLongitudeRectangle(south, north, west, east),
    )


def topology_with_zones(
    *zones: ZoneDefinition,
    model=None,
    static_input: SimulatorStaticInput | None = None,
) -> Topology:
    definition = SimulationDefinition(
        static_input=static_input or SimulatorStaticInput(),
        timeline=SimulationTimeline(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
            timedelta(seconds=10),
        ),
    )
    return Simulator(definition).create_topology(
        zone_layout_definition=ZoneLayoutDefinition(
            zones=zones,
            allow_overlap=True,
        ),
        zone_membership_model=model,
    )


def topology_without_zone_layout(
    static_input: SimulatorStaticInput | None = None,
) -> Topology:
    simulation_definition = SimulationDefinition(
        static_input=static_input or SimulatorStaticInput(),
        timeline=SimulationTimeline(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
            timedelta(seconds=10),
        ),
    )
    return Simulator(simulation_definition).create_topology()


def ground_station_input() -> GroundStationSimulationInput:
    return GroundStationSimulationInput(
        node_id=NodeId(65440, "ground-station", "reference-gs"),
        ground_station_id="reference-gs",
        latitude_deg=12.5,
        longitude_deg=145.25,
        elevation_m=42.0,
        station_type="reference",
        operator="fixture",
    )


def definition(
    zone_id: str,
    south: float,
    north: float,
    west: float,
    east: float,
    *,
    priority: int = 0,
) -> ZoneDefinition:
    return ZoneDefinition(
        zone_id=zone_id,
        priority=priority,
        geometry=LatitudeLongitudeRectangleDefinition(
            south,
            north,
            west,
            east,
        ),
    )


def categories(probes) -> set[str]:
    return {
        origin["category"]
        for probe in probes
        for origin in probe["origins"]
    }


def test_single_zone_probes_are_deterministic_and_cover_normal_rectangle() -> None:
    value = zone("normal", -10, 10, -20, 20)
    first = select_zone_probes((value,))
    second = select_zone_probes((value,))

    assert first == second
    assert len(first) == 17
    assert {
        "representative-center",
        "interior-grid",
        "north-edge-midpoint",
        "south-edge-midpoint",
        "west-edge-midpoint",
        "east-edge-midpoint",
        "north-west-corner",
        "north-east-corner",
        "south-west-corner",
        "south-east-corner",
    } <= categories(first)
    assert all(value.contains(p["latitude_deg"], p["longitude_deg"]) for p in first)


def test_zone_collector_handles_no_layout_single_zone_many_zones_and_no_relations(
    tmp_path: Path,
) -> None:
    no_layout = collect_zone_data(topology_without_zone_layout())
    assert no_layout["zone_layout_configured"] is False
    assert no_layout["zones"] == []
    assert no_layout["probes"] == []
    assert no_layout["counts"]["focus_visible"] == 0

    single = collect_zone_data(
        topology_with_zones(definition("single", -10, 10, -20, 20))
    )
    assert single["counts"]["zones"] == 1
    assert single["counts"]["relationship_candidates"] == 0
    assert 0 < single["counts"]["focus_visible"] < single["counts"]["probes"]

    many_definitions = tuple(
        definition(
            f"zone-{index:02d}",
            -10,
            10,
            -175 + index * 30,
            -165 + index * 30,
        )
        for index in range(12)
    )
    many = collect_zone_data(topology_with_zones(*many_definitions))
    assert many["counts"]["zones"] == 12
    assert many["counts"]["relationship_candidates"] == 0
    assert many["counts"]["probes"] == 12 * 17
    assert many["counts"]["focus_visible"] < many["counts"]["probes"]

    write_zone_tool(topology_without_zone_layout(), tmp_path)
    assert (tmp_path / "index.html").is_file()
    assert json.loads(
        (tmp_path / "probes.json").read_text(encoding="utf-8")
    )["zone_layout_configured"] is False


def test_zone_output_is_deterministic_under_definition_input_reordering() -> None:
    values = (
        definition("charlie", -20, -5, 80, 100),
        definition("alpha", -10, 10, -20, 20),
        definition("bravo", 5, 25, 10, 30),
    )
    forward = collect_zone_data(topology_with_zones(*values))
    reverse = collect_zone_data(topology_with_zones(*reversed(values)))

    assert forward["zones"] == reverse["zones"]
    assert forward["probes"] == reverse["probes"]
    assert forward["counts"] == reverse["counts"]


def test_zone_collector_preserves_multiple_pair_relations() -> None:
    data = collect_zone_data(
        topology_with_zones(
            definition("alpha", -10, 10, -20, 20),
            definition("bravo", -5, 15, -10, 30),
            definition("charlie", 0, 20, 0, 40),
        )
    )

    relation_pairs = {
        tuple(origin["source_zone_ids"])
        for probe in data["probes"]
        for origin in probe["origins"]
        if origin["origin_kind"]
        == "tool-selected relationship candidate"
    }
    assert relation_pairs == {
        ("alpha", "bravo"),
        ("alpha", "charlie"),
        ("bravo", "charlie"),
    }
    assert data["counts"]["relationship_candidates"] >= len(relation_pairs)


def test_wrapped_and_pole_probes_preserve_all_origins_after_deduplication() -> None:
    wrapped = zone("wrapped", -10, 10, 170, -170)
    north = zone("north", 80, 90, 10, 50)
    south = zone("south", -90, -80, -50, -10)
    probes = select_zone_probes((wrapped, north, south))

    wrapped_sides = [
        probe
        for probe in probes
        if any(
            origin["category"] == "wrapped-dateline-side"
            for origin in probe["origins"]
        )
    ]
    assert {probe["longitude_deg"] for probe in wrapped_sides} == {-179.0, 179.0}
    assert all(
        wrapped.contains(probe["latitude_deg"], probe["longitude_deg"])
        for probe in wrapped_sides
    )
    north_pole = next(probe for probe in probes if probe["latitude_deg"] == 90)
    north_categories = {
        origin["category"]
        for origin in north_pole["origins"]
        if "north" in origin["source_zone_ids"]
    }
    assert {
        "north-pole",
        "north-edge-midpoint",
        "north-west-corner",
        "north-east-corner",
    } <= north_categories
    assert any(probe["latitude_deg"] == -90 for probe in probes)


def test_pair_scan_covers_overlap_edges_corners_poles_and_dateline() -> None:
    positive = select_zone_probes(
        (
            zone("left", -10, 10, -20, 20),
            zone("overlap", 0, 15, 0, 30),
        )
    )
    edge = select_zone_probes(
        (
            zone("left", -10, 10, -20, 20),
            zone("right", -10, 10, 20, 40),
        )
    )
    corner = select_zone_probes(
        (
            zone("left", -10, 10, -20, 20),
            zone("corner", 10, 20, 20, 30),
        )
    )
    pole = select_zone_probes(
        (
            zone("north-a", 80, 90, 10, 50),
            zone("north-b", 85, 90, 100, 150),
        )
    )
    dateline = select_zone_probes(
        (
            zone("wrap-a", -10, 10, 170, -170),
            zone("wrap-b", -5, 5, 175, -175),
        )
    )

    assert "positive-overlap-representative" in categories(positive)
    assert "shared-edge-midpoint" in categories(edge)
    assert "shared-corner" in categories(corner)
    assert "shared-pole" in categories(pole)
    assert "dateline-relation" in categories(dateline)
    assert "wrapped-positive-overlap-side" in categories(dateline)
    pair_origins = [
        origin
        for probe in dateline
        for origin in probe["origins"]
        if len(origin["source_zone_ids"]) == 2
    ]
    assert pair_origins
    assert all(
        origin["origin_kind"] == "tool-selected relationship candidate"
        for origin in pair_origins
    )


def test_every_probe_calls_formal_find_and_resolve_and_records_observation(
    monkeypatch,
) -> None:
    topology = topology_with_zones(
        definition("outer", -20, 20, -30, 30, priority=1),
        definition("inner", -10, 10, -10, 10, priority=2),
    )
    calls = {"find": 0, "resolve": 0}
    original_find = Topology.find_zones
    original_resolve = Topology.resolve_zone

    def find_spy(self, latitude_deg, longitude_deg):
        calls["find"] += 1
        return original_find(self, latitude_deg, longitude_deg)

    def resolve_spy(self, latitude_deg, longitude_deg):
        calls["resolve"] += 1
        return original_resolve(self, latitude_deg, longitude_deg)

    monkeypatch.setattr(Topology, "find_zones", find_spy)
    monkeypatch.setattr(Topology, "resolve_zone", resolve_spy)
    data = collect_zone_data(topology)

    assert calls == {
        "find": len(data["probes"]),
        "resolve": len(data["probes"]),
    }
    assert data["status"] == "PASS"
    assert all(
        probe["invariant_status"] == "PASS"
        and probe["resolution_status"] == "OBSERVED"
        for probe in data["probes"]
    )
    assert any(len(probe["physical_matches"]) > 1 for probe in data["probes"])


def test_focus_view_keeps_every_important_probe_and_hides_some_ordinary_ones() -> None:
    topology = topology_with_zones(
        definition("wrap-a", -15, 15, 170, -170, priority=1),
        definition("wrap-b", -10, 10, 175, -175, priority=2),
        definition("north", 75, 90, 20, 80, priority=3),
    )
    data = collect_zone_data(topology)

    assert any(not probe["focus_visible"] for probe in data["probes"])
    for probe in data["probes"]:
        origins = probe["origins"]
        categories = probe["categories"]
        mandatory = (
            probe["invariant_status"] == "FAIL"
            or len(probe["physical_matches"]) > 1
            or any(
                origin["origin_kind"]
                == "tool-selected relationship candidate"
                for origin in origins
            )
            or any("pole" in category for category in categories)
            or any("dateline" in category for category in categories)
            or "representative-center" in categories
        )
        if mandatory:
            assert probe["focus_visible"], probe
            assert probe["focus_reasons"]
    assert data["counts"]["focus_visible"] == sum(
        probe["focus_visible"] for probe in data["probes"]
    )


def test_wrapped_display_boundary_follows_eastward_arc_from_formal_bounds() -> None:
    data = collect_zone_data(
        topology_with_zones(definition("wrapped", -10, 10, 170, -170))
    )
    zone_record = data["zones"][0]
    boundary = zone_record["display_boundary"]
    north_edge = boundary[:33]

    assert zone_record["geometry"]["wrapped"]
    assert all(latitude == 10 for latitude, _ in north_edge)
    assert north_edge[0] == [10.0, 170.0]
    assert north_edge[-1] == [10.0, -170.0]
    assert all(abs(longitude) >= 170 for _, longitude in boundary)
    eastward_deltas = [
        (right[1] - left[1]) % 360
        for left, right in zip(north_edge, north_edge[1:])
    ]
    assert all(0 < delta <= 1 for delta in eastward_deltas)


def test_custom_model_winner_is_observed_without_default_explanation(
    tmp_path: Path,
) -> None:
    class LastMatchModel:
        def find_zones(self, zones, latitude_deg, longitude_deg):
            return tuple(
                item
                for item in zones
                if item.contains(latitude_deg, longitude_deg)
            )

        def resolve_zone(self, matches, latitude_deg, longitude_deg):
            return matches[-1]

    topology = topology_with_zones(
        definition("alpha", -20, 20, -30, 30, priority=100),
        definition("zulu", -10, 10, -10, 10, priority=0),
        model=LastMatchModel(),
    )
    data = collect_zone_data(topology)
    write_zone_tool(topology, tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")

    overlap = next(
        probe
        for probe in data["probes"]
        if len(probe["physical_matches"]) == 2
    )
    assert overlap["authoritative_result"]["zone_id"] == "zulu"
    assert data["model_type"] == "LastMatchModel"
    assert not data["model_is_default"]
    assert "maximum priority" not in html
    assert "LastMatchModel" in html


def test_zone_tool_uses_public_membership_model_facade() -> None:
    source = (Path(__file__).resolve().parents[2] / "tools/zone.py").read_text(
        encoding="utf-8"
    )

    assert "layout.get_membership_model()" in source
    assert "._membership_model" not in source
    assert 'getattr(layout, "_membership_model")' not in source


@pytest.mark.parametrize("failure_kind", ("foreign-match", "foreign-winner"))
def test_illegal_model_output_is_fail_without_fallback(failure_kind: str) -> None:
    external = zone("external", -90, 90, -180, 0)

    class IllegalModel:
        def find_zones(self, zones, latitude_deg, longitude_deg):
            if failure_kind == "foreign-match":
                return (external,)
            return tuple(
                item
                for item in zones
                if item.contains(latitude_deg, longitude_deg)
            )

        def resolve_zone(self, matches, latitude_deg, longitude_deg):
            return external

    topology = topology_with_zones(
        definition("owned", -20, 20, -30, 30),
        model=IllegalModel(),
    )
    data = collect_zone_data(topology)

    assert data["status"] == "FAIL"
    assert data["counts"]["failed"] == data["counts"]["probes"]
    assert all(probe["failure_reasons"] for probe in data["probes"])
    assert all(
        probe["authoritative_result"] is None for probe in data["probes"]
    )


def test_zone_renderer_writes_embedded_offline_audit_artifacts(
    tmp_path: Path,
) -> None:
    topology = topology_with_zones(
        definition("outer", -20, 20, -30, 30, priority=1),
        definition("inner", -10, 10, -10, 10, priority=2),
        static_input=SimulatorStaticInput(
            ground_stations=(ground_station_input(),)
        ),
    )
    result = write_zone_tool(topology, tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "probes.json").read_text(encoding="utf-8"))
    summary = (tmp_path / "summary.txt").read_text(encoding="utf-8")

    assert result["status"] == "PASS"
    assert payload["format_version"] == 1
    assert len(payload["probes"]) == payload["counts"]["probes"]
    assert payload["ground_stations"] == [
        {
            "asn": 65440,
            "ground_station_id": "reference-gs",
            "node_id": "65440/ground-station/reference-gs",
            "latitude_deg": 12.5,
            "longitude_deg": 145.25,
            "elevation_m": 42.0,
            "station_type": "reference",
            "operator": "fixture",
        }
    ]
    assert "const DATA=" in html
    assert "physical matches" in html
    assert "authoritative" in html
    assert "DefaultZoneMembershipModel" in html
    assert "maximum priority" in html
    assert "zone-filter" in html
    assert "category-filter" in html
    assert "status-filter" in html
    assert 'option value="focus"' in html
    assert 'option value="all"' in html
    assert 'option value="anomalies"' in html
    assert 'option value="multi"' in html
    assert 'id="probe-total"' in html
    assert 'id="probe-visible"' in html
    assert 'id="mode-label"' in html
    assert 'id="probe-table"' in html
    assert 'id="zone-globe"' in html
    assert 'id="zone-map"' in html
    assert "display_boundary" in html
    assert "longitudeAt" in html
    assert "spherePoint(station.latitude_deg,station.longitude_deg" in html
    assert "球体采用单位半径" in html
    assert "不声称是大圆线" in html
    assert "不重新调用正式 API" in html
    assert "http://" not in html
    assert "https://" not in html
    assert "fetch(" not in html
    assert "tiles" not in html.lower()
    assert "cdn" not in html.lower()
    assert "<script src=" not in html
    assert "<link " not in html
    assert "mean_anomaly_deg" not in html
    assert "timestamp position" not in html
    assert "status=PASS" in summary
    assert "resolution=OBSERVED" in summary
    (tmp_path / "probes.json").unlink()
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == html
