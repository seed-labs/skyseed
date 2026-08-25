from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from skyseed_v2.configuration.simulator import (
    LatitudeLongitudeRectangleDefinition,
    ZoneDefinition,
    ZoneLayoutDefinition,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    Topology,
)
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SimulatorStaticInput,
)
from tools.zone import collect_zone_data, write_zone_tool


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


def simulation_definition(
    static_input: SimulatorStaticInput | None = None,
) -> SimulationDefinition:
    return SimulationDefinition(
        static_input=static_input or SimulatorStaticInput(),
        timeline=SimulationTimeline(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
            timedelta(seconds=10),
        ),
    )


def topology_with_zones(
    *zones: ZoneDefinition,
    static_input: SimulatorStaticInput | None = None,
) -> Topology:
    return Simulator(simulation_definition(static_input)).create_topology(
        zone_layout_definition=ZoneLayoutDefinition(
            zones=zones,
            allow_overlap=True,
        )
    )


def topology_without_zone_layout() -> Topology:
    return Simulator(simulation_definition()).create_topology()


def test_collector_reads_only_owned_static_zone_layout() -> None:
    topology = topology_with_zones(
        definition("outer", -20, 20, -30, 30, priority=1),
        definition("inner", -10, 10, -10, 10, priority=2),
        static_input=SimulatorStaticInput(
            ground_stations=(ground_station_input(),)
        ),
    )

    data = collect_zone_data(topology)

    assert set(data) == {
        "format_version",
        "tool",
        "status",
        "zone_layout_configured",
        "allow_overlap",
        "zones",
        "counts",
    }
    assert data["status"] == "PASS"
    assert data["counts"] == {"zones": 2}
    assert [item["zone_id"] for item in data["zones"]] == [
        "inner",
        "outer",
    ]
    assert all(
        set(item) == {
            "zone_id",
            "priority",
            "geometry",
            "display_boundary",
        }
        for item in data["zones"]
    )
    assert "ground_stations" not in data
    assert "probes" not in data
    assert "model_type" not in data


def test_collector_does_not_execute_membership_queries(monkeypatch) -> None:
    topology = topology_with_zones(
        definition("one", -10, 10, -20, 20)
    )

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("Zone Tool must not execute membership queries")

    monkeypatch.setattr(Topology, "find_zones", unexpected_call)
    monkeypatch.setattr(Topology, "resolve_zone", unexpected_call)

    assert collect_zone_data(topology)["counts"] == {"zones": 1}


def test_collector_handles_no_layout_and_is_definition_order_independent(
    tmp_path: Path,
) -> None:
    no_layout = collect_zone_data(topology_without_zone_layout())
    assert no_layout["zone_layout_configured"] is False
    assert no_layout["zones"] == []
    assert no_layout["counts"] == {"zones": 0}

    values = (
        definition("charlie", -20, -5, 80, 100),
        definition("alpha", -10, 10, -20, 20),
        definition("bravo", 5, 25, 10, 30),
    )
    forward = collect_zone_data(topology_with_zones(*values))
    reverse = collect_zone_data(topology_with_zones(*reversed(values)))
    assert forward == reverse

    write_zone_tool(topology_without_zone_layout(), tmp_path)
    assert json.loads(
        (tmp_path / "zones.json").read_text(encoding="utf-8")
    )["zone_layout_configured"] is False


def test_wrapped_display_boundary_follows_eastward_arc_from_formal_bounds(
) -> None:
    data = collect_zone_data(
        topology_with_zones(definition("wrapped", -10, 10, 170, -170))
    )
    zone_record = data["zones"][0]
    boundary = zone_record["display_boundary"]
    north_edge = boundary[:33]

    assert zone_record["geometry"] == {
        "south_latitude_deg": -10.0,
        "north_latitude_deg": 10.0,
        "west_longitude_deg": 170.0,
        "east_longitude_deg": -170.0,
        "wrapped": True,
        "representative_center": [0.0, -180.0],
    }
    assert north_edge[0] == [10.0, 170.0]
    assert north_edge[-1] == [10.0, -170.0]
    assert all(abs(longitude) >= 170 for _, longitude in boundary)
    eastward_deltas = [
        (right[1] - left[1]) % 360
        for left, right in zip(north_edge, north_edge[1:])
    ]
    assert all(0 < delta <= 1 for delta in eastward_deltas)


def test_renderer_writes_probe_free_offline_direct_selection_artifacts(
    tmp_path: Path,
) -> None:
    topology = topology_with_zones(
        definition("wrapped", -20, 20, 170, -170, priority=4),
        definition("middle", -10, 10, -30, 30, priority=2),
        static_input=SimulatorStaticInput(
            ground_stations=(ground_station_input(),)
        ),
    )
    result = write_zone_tool(topology, tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "zones.json").read_text(encoding="utf-8"))
    summary = (tmp_path / "summary.txt").read_text(encoding="utf-8")

    assert result == {
        "status": "PASS",
        "message": "2 Zones / static layout geometry",
        "counts": {"zones": 2},
    }
    assert payload["format_version"] == 1
    assert payload["counts"] == {"zones": 2}
    assert "ground_stations" not in payload
    assert "probes" not in payload
    assert 'href="../index.html"' in html
    assert 'id="selected-zone"' in html
    assert 'id="zone-globe"' in html
    assert 'id="zone-map"' in html
    assert "const COASTLINES=" in html
    assert "canvas.dataset.coastlineSegments" in html
    assert "window.devicePixelRatio" in html
    assert "backingWidth/logicalWidth" in html
    assert "px=clientX-box.left" in html
    assert 'class:`zone-area' in html
    assert '"data-zone":zone.zone_id' in html
    assert "function selectZone(zone)" in html
    assert "function zoneAtCanvas(clientX,clientY)" in html
    assert "zoneContainsForDisplay" in html
    assert 'map.addEventListener("click"' in html
    assert 'canvas.addEventListener("pointermove"' in html
    assert "if(total>5)pointer.moved=true" in html
    assert "const wasClick=pointer&&!pointer.moved" in html
    assert "geometry.representative_center" in html
    assert "zone.priority" in html
    assert "geometry.wrapped" in html
    assert 'canvas.dataset.selectedLabel=selectedZone?.zone_id||""' in html
    assert "canvas.dataset.selectedZoneX" in html
    assert "canvas.dataset.hoverLabel" in html
    assert 'class:"zone-map-label"' in html
    assert 'selected?1.45:hovered?1.15:.65' in html
    assert "colorByZone" not in html
    assert "const colors=" not in html
    assert "mapLongitude=lon=>lon===180 ? 1000" in html
    assert "display_boundary" in html
    assert "function clearSelectedZone()" in html
    assert "function toggleZone(zone)" in html
    assert "zone?.zone_id===selectedZoneId" in html
    assert 'event.key==="Escape"' in html
    assert "球体采用单位半径" in html
    assert "不声称是大圆线" in html
    assert "probe" not in html.lower()
    assert "ground_stations" not in html
    assert "physical matches" not in html
    assert "authoritative" not in html
    assert "http://" not in html
    assert "https://" not in html
    assert "fetch(" not in html
    assert "<script src=" not in html
    assert "<link " not in html
    assert "status=PASS" in summary
    assert "zones=2" in summary
    assert "resolution=" not in summary

    (tmp_path / "zones.json").unlink()
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == html
