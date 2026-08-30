from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import hypot
from pathlib import Path
import shutil
import tempfile

import pytest

import skyseed_v2
from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.simu import (
    create_mini_satellite_internet_slicer,
)
from skyseed_v2.simulator import (
    CircularOrbitPositionModel,
    Connection,
    ConnectionId,
    ConnectionKind,
    GeometricConnectionModel,
    GroundStationRouteTarget,
    HopCountRouteModel,
    RouteKey,
    RouteTarget,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    Slicer,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator.geometry import (
    ecef_distance_m,
    elevation_deg,
    geodetic_to_ecef,
)
from skyseed_v2.simulator.input import (
    SatelliteSimulationInput,
    SimulatorStaticInput,
)
from tools.slice import (
    collect_slice_difference_data,
    write_slice_difference_tool,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_SOURCE = PROJECT_ROOT / "tools/slice/inspection.py"
TOOL_UI_SOURCE = PROJECT_ROOT / "tools/slice/renderer.py"
TOOL_PIPELINE_SOURCES = tuple(
    sorted((PROJECT_ROOT / "tools/slice").glob("*.py"))
)
EXPECTED_FILES = {
    "checksums.sha256",
    "slice-manifest.json",
    "chunks",
    "index.html",
    "inventory.json",
    "summary.txt",
}
PORTABILITY_EPOCH = datetime(2026, 2, 1, tzinfo=timezone.utc)


def _connection_ref(connection_id: ConnectionId) -> str:
    return "|".join(
        (
            connection_id.kind.value,
            connection_id.endpoint_a.value,
            connection_id.endpoint_b.value,
        )
    )


def _connection_parameters(parameters: object) -> dict[str, int]:
    return {
        "bandwidth_bps": getattr(parameters, "bandwidth_bps"),
        "delay_us": getattr(parameters, "delay_us"),
        "packet_loss_ppm": getattr(parameters, "packet_loss_ppm"),
    }


def _assert_projected_connection(
    projected: dict[str, object],
    formal: Connection,
) -> None:
    connection_id = formal.connection_id
    assert projected["connection_ref"] == _connection_ref(connection_id)
    assert projected["connection_id"] == {
        "kind": connection_id.kind.value,
        "endpoint_a": connection_id.endpoint_a.value,
        "endpoint_b": connection_id.endpoint_b.value,
    }
    assert projected["endpoint_a_to_b"] == _connection_parameters(
        formal.endpoint_a_to_b
    )
    assert projected["endpoint_b_to_a"] == _connection_parameters(
        formal.endpoint_b_to_a
    )
    if projected["display_kind"] == "cross-orbit-isl":
        assert set(projected["cross_plane_sides"].values()) == {
            "predecessor-orbit",
            "successor-orbit",
        }
    else:
        assert projected["cross_plane_sides"] is None


def _target_data(target: RouteTarget) -> dict[str, str]:
    if isinstance(target, GroundStationRouteTarget):
        return {
            "kind": "G",
            "ground_station_id": target.ground_station_id.value,
        }
    return {"kind": "Z", "zone_id": target.zone_id}


def _route_ref(route_key: RouteKey) -> str:
    target = route_key.target
    target_ref = (
        f"G|{target.ground_station_id.value}"
        if isinstance(target, GroundStationRouteTarget)
        else f"Z|{target.zone_id}"
    )
    return f"{route_key.source.value}|{target_ref}"


def _target_ref(target: RouteTarget) -> str:
    return (
        f"G|{target.ground_station_id.value}"
        if isinstance(target, GroundStationRouteTarget)
        else f"Z|{target.zone_id}"
    )


def _portable_satellite(
    *,
    asn: int,
    shell_id: str,
    satellite_id: str,
    right_ascension_deg: float,
) -> SatelliteSimulationInput:
    return SatelliteSimulationInput(
        node_id=NodeId(asn, "satellite", satellite_id),
        satellite_id=satellite_id,
        shell_id=shell_id,
        orbit_id="shared-orbit",
        orbit_index=0,
        altitude_km=700.0,
        inclination_deg=70.0,
        right_ascension_deg=right_ascension_deg,
        mean_anomaly_deg=0.0,
        epoch_utc=PORTABILITY_EPOCH,
    )


def _portable_slicer() -> Slicer:
    static_input = SimulatorStaticInput(
        (
            _portable_satellite(
                asn=65201,
                shell_id="alpha-shell",
                satellite_id="alpha-probe",
                right_ascension_deg=10.0,
            ),
            _portable_satellite(
                asn=65202,
                shell_id="beta-shell",
                satellite_id="beta-probe",
                right_ascension_deg=190.0,
            ),
        )
    )
    timeline = SimulationTimeline(
        PORTABILITY_EPOCH,
        PORTABILITY_EPOCH + timedelta(seconds=60),
        timedelta(seconds=30),
    )
    simulator = Simulator(SimulationDefinition(static_input, timeline))
    position_model = CircularOrbitPositionModel()
    simulator.create_topology(position_model=position_model)
    slicer = simulator.create_slicer(
        connection_model=GeometricConnectionModel(
            maximum_isl_distance_m=1_000_000.0,
            ground_station_minimum_elevation_deg=0.0,
            maximum_ground_station_access_count=0,
            ground_station_maximum_distance_m=None,
            zone_coverage_minimum_elevation_deg=0.0,
            bandwidth_bps_by_kind={
                ConnectionKind.INTER_SATELLITE: 1_000_000,
                ConnectionKind.GROUND_STATION_ACCESS: 1_000_000,
            },
            packet_loss_ppm_by_kind={
                ConnectionKind.INTER_SATELLITE: 0,
                ConnectionKind.GROUND_STATION_ACCESS: 0,
            },
            propagation_speed_m_per_s=299_792_458.0,
        ),
        route_model=HopCountRouteModel(),
    )
    assert len(slicer.create_all_slices()) == 3
    return slicer


@pytest.fixture(scope="module")
def slicer() -> Slicer:
    emulator = build_mini_satellite_internet_emulator()
    value = create_mini_satellite_internet_slicer(emulator)
    value.create_all_slices()
    return value


@pytest.fixture(scope="module")
def collected(slicer: Slicer) -> dict[str, object]:
    return collect_slice_difference_data(slicer)


@pytest.fixture(scope="module")
def portable_slicer() -> Slicer:
    return _portable_slicer()


def test_projector_matches_formal_slice_position_connection_route_and_difference(
    slicer: Slicer,
    collected: dict[str, object],
) -> None:
    assert collected["authority"] == {
        "status": "non-authoritative-view",
        "source": "retained public results from one exact Slicer",
        "position": "EarthFixedPosition with formal geographic views",
        "paths": (
            "selected browser display restoration from projected formal "
            "next-hop facts"
        ),
        "differences": "Slicer.create_difference",
    }
    records = {
        record["slice_id"]: record
        for record in collected["slices"]
    }
    assert tuple(records) == slicer.get_retained_slice_ids()

    for slice_id, record in records.items():
        formal = slicer.get_slice(slice_id)
        assert record["timestamp"] == (
            None if formal.timestamp is None else formal.timestamp.isoformat()
        )
        positions = {
            value["node_id"]: value for value in record["positions"]
        }
        assert set(positions) == {
            node_id.value for node_id in formal.positions
        }
        for node_id, position in formal.positions.items():
            projected = positions[node_id.value]
            assert projected["ecef_m"] == {
                "x": position.x_m,
                "y": position.y_m,
                "z": position.z_m,
            }
            assert projected["geographic"] == {
                "latitude_deg": position.latitude_deg,
                "longitude_deg": position.longitude_deg,
                "ellipsoidal_height_m": position.ellipsoidal_height_m,
            }

        projected_connections = {
            value["connection_ref"]: value for value in record["connections"]
        }
        formal_connections = {
            _connection_ref(connection.connection_id): connection
            for connection in formal.connection_slice.connections
        }
        assert projected_connections.keys() == formal_connections.keys()
        for connection_ref, connection in formal_connections.items():
            _assert_projected_connection(
                projected_connections[connection_ref],
                connection,
            )
        assert {
            value["zone_id"]: value["service_satellite_id"]
            for value in record["zone_service_assignments"]
        } == {
            zone_id: node_id.value
            for zone_id, node_id in (
                formal.connection_slice.zone_service_assignments.items()
            )
        }
        for assignment in record["zone_service_assignments"]:
            zone = slicer.get_topology().get_zone_layout().get_zone(
                assignment["zone_id"]
            )
            satellite_id = next(
                node_id
                for node_id in formal.positions
                if node_id.value == assignment["service_satellite_id"]
            )
            satellite_position = formal.positions[satellite_id]
            representative = geodetic_to_ecef(
                zone.representative_point[0],
                zone.representative_point[1],
                0.0,
            )
            explanation = assignment["display_explanation"]
            assert explanation["representative_point_elevation_deg"] == (
                pytest.approx(
                    elevation_deg(representative, satellite_position),
                    abs=1e-12,
                )
            )
            assert explanation["slant_distance_km"] == pytest.approx(
                ecef_distance_m(representative, satellite_position) / 1_000.0,
                abs=1e-9,
            )
            assert explanation["surface_separation_km"] >= 0.0
            assert explanation["surface_separation_authority"] == (
                "Tool-derived IUGG mean-Earth spherical approximation"
            )
            assert explanation["slant_distance_authority"] == (
                "derived from formal representative-point and Satellite ECEF"
            )
            assert explanation["subpoint_inside_zone"] is zone.contains(
                satellite_position.latitude_deg,
                satellite_position.longitude_deg,
            )
            assert (
                explanation["eligibility_basis"]
                == "formal assignment uses representative-point visibility "
                "and minimum elevation; subpoint containment is not eligibility"
            )
        assert set(record["routes"]) == {
            (source.value, _target_ref(target), next_hop.value)
            for source, targets in formal.route_slice.routes.items()
            for target, next_hop in targets.items()
        }

        if slice_id == 0:
            assert record["difference_from_previous"] is None
        else:
            difference = slicer.create_difference(slice_id - 1, slice_id)
            projected = record["difference_from_previous"]
            assert projected["previous_slice_id"] == (
                difference.previous_slice_id
            )
            assert projected["previous_timestamp"] == (
                None
                if difference.previous_timestamp is None
                else difference.previous_timestamp.isoformat()
            )
            assert projected["current_slice_id"] == difference.current_slice_id
            assert projected["current_timestamp"] == (
                difference.current_timestamp.isoformat()
            )
            assert projected["is_empty"] is difference.is_empty
            for projected_connection, formal_connection in zip(
                projected["added_connections"],
                difference.added_connections,
                strict=True,
            ):
                _assert_projected_connection(
                    projected_connection,
                    formal_connection,
                )
            assert len(projected["added_connections"]) == len(
                difference.added_connections
            )
            assert projected["removed_connection_ids"] == [
                {
                    "connection_id": {
                        "kind": connection_id.kind.value,
                        "endpoint_a": connection_id.endpoint_a.value,
                        "endpoint_b": connection_id.endpoint_b.value,
                    },
                    "connection_ref": _connection_ref(connection_id),
                }
                for connection_id in difference.removed_connection_ids
            ]
            assert projected["connection_parameter_patches"] == [
                {
                    "connection_ref": _connection_ref(patch.connection_id),
                    "connection_id": {
                        "kind": patch.connection_id.kind.value,
                        "endpoint_a": patch.connection_id.endpoint_a.value,
                        "endpoint_b": patch.connection_id.endpoint_b.value,
                    },
                    "direction": patch.direction.value,
                    "changed": {
                        key: value
                        for key, value in (
                            ("bandwidth_bps", patch.bandwidth_bps),
                            ("delay_us", patch.delay_us),
                            ("packet_loss_ppm", patch.packet_loss_ppm),
                        )
                        if value is not None
                    },
                }
                for patch in difference.connection_parameter_patches
            ]
            assert projected["zone_service_assignment_patches"] == [
                {
                    "zone_id": patch.zone_id,
                    "operation": patch.operation.value,
                    "service_satellite_id": (
                        None
                        if patch.service_satellite_id is None
                        else patch.service_satellite_id.value
                    ),
                }
                for patch in difference.zone_service_assignment_patches
            ]
            assert projected["route_patches"] == [
                (
                    patch.operation.value,
                    patch.route_key.source.value,
                    _target_ref(patch.route_key.target),
                    (
                        None
                        if patch.next_hop is None
                        else patch.next_hop.value
                    ),
                )
                for patch in difference.route_patches
            ]


def test_slice_zero_and_all_timeline_entries_are_explicit(
    slicer: Slicer,
    collected: dict[str, object],
) -> None:
    timeline = collected["timeline"]
    slices = collected["slices"]
    assert timeline["retained_slice_ids"] == list(
        slicer.get_retained_slice_ids()
    )
    assert timeline["slice_count_including_zero"] == len(slices) == 65
    zero = slices[0]
    total_zones = len(collected["topology"]["zones"])
    total_ground_stations = collected["inventory"]["ground_stations"]
    assert zero == {
        "slice_id": 0,
        "timestamp": None,
        "positions": (),
        "orbit_guides": (),
        "connections": (),
        "zone_service_assignments": (),
        "routes": (),
        "difference_from_previous": None,
        "diagnostics": (),
        "summary": {
            "state_available": False,
            "positions": 0,
            "connections": 0,
            "served_zones": 0,
            "unserved_zones": total_zones,
            "total_zones": total_zones,
            "distinct_service_satellites": 0,
            "covered_ground_stations": 0,
            "total_ground_stations": total_ground_stations,
            "connection_counts": {
                "same_orbit_isl": 0,
                "cross_plane_isl": 0,
                "ground_station_access": 0,
            },
            "route_entries": 0,
            "zone_service_geometry": {
                "elevation_deg": {"p5": None, "median": None},
                "surface_separation_km": {
                    "median": None,
                    "p95": None,
                    "max": None,
                },
                "slant_distance_km": {
                    "median": None,
                    "p95": None,
                    "max": None,
                },
            },
            "diagnostics": 0,
            "difference": None,
        },
    }


def test_display_orbit_guides_are_fitted_from_formal_ecef_positions(
    collected: dict[str, object],
) -> None:
    slices = collected["slices"]
    assert slices[0]["orbit_guides"] == ()
    normals_by_orbit: dict[str, list[tuple[float, float, float]]] = {}

    for record in slices[1:]:
        positions = {
            position["node_id"]: position for position in record["positions"]
        }
        for guide in record["orbit_guides"]:
            normal = tuple(guide["plane_normal_ecef_unit"].values())
            basis_u = tuple(guide["basis_u_ecef_unit"].values())
            basis_v = tuple(guide["basis_v_ecef_unit"].values())
            normals_by_orbit.setdefault(guide["orbit_ref"], []).append(normal)
            assert guide["source"] == "formal-ecef-positions"
            assert guide["sample_count"] == len(guide["satellite_ids"])
            assert abs(sum(a * b for a, b in zip(normal, basis_u))) < 1e-12
            assert abs(sum(a * b for a, b in zip(normal, basis_v))) < 1e-12
            assert abs(hypot(*basis_u) - 1.0) < 1e-12
            assert abs(hypot(*basis_v) - 1.0) < 1e-12

            radii = []
            residuals = []
            for node_id in guide["satellite_ids"]:
                ecef = positions[node_id]["ecef_m"]
                vector = (ecef["x"], ecef["y"], ecef["z"])
                radius = hypot(*vector)
                radii.append(radius)
                residuals.append(
                    abs(
                        sum(
                            component * normal_component
                            for component, normal_component in zip(
                                vector,
                                normal,
                                strict=True,
                            )
                        )
                        / radius
                    )
                )
            assert max(residuals) < 1e-12
            assert guide["max_unit_plane_residual"] == pytest.approx(
                max(residuals),
                abs=1e-15,
            )
            assert guide["mean_radius_m"] == pytest.approx(
                sum(radii) / len(radii),
                abs=1e-6,
            )

    assert normals_by_orbit
    for normals in normals_by_orbit.values():
        assert len(normals) > 1
        first, second = normals[:2]
        assert 1.0 - abs(
            sum(a * b for a, b in zip(first, second, strict=True))
        ) > 1e-8


def test_projector_is_deterministic_and_does_not_change_slicer(
    slicer: Slicer,
    collected: dict[str, object],
) -> None:
    before = (
        slicer.get_retained_slice_ids(),
        slicer.get_latest_slice(),
        slicer.get_next_timestamp(),
        slicer.is_exhausted(),
    )
    repeated = collect_slice_difference_data(slicer)
    after = (
        slicer.get_retained_slice_ids(),
        slicer.get_latest_slice(),
        slicer.get_next_timestamp(),
        slicer.is_exhausted(),
    )
    assert repeated == collected
    assert after == before


def test_model_context_is_detached_and_tool_derived(
    slicer: Slicer,
) -> None:
    supplied = {
        "zone_service": {"minimum_elevation_deg": 25.0},
        "ground_station_access": {
            "minimum_elevation_deg": 5.0,
            "maximum_slant_distance_km": 3_000.0,
            "ground_station_capacity": 8,
        },
        "interpretation_notes": ["projected note"],
    }
    projected = collect_slice_difference_data(
        slicer,
        model_context=supplied,
    )

    assert supplied == {
        "zone_service": {"minimum_elevation_deg": 25.0},
        "ground_station_access": {
            "minimum_elevation_deg": 5.0,
            "maximum_slant_distance_km": 3_000.0,
            "ground_station_capacity": 8,
        },
        "interpretation_notes": ["projected note"],
    }
    context = projected["model_context"]
    assert context["zone_count"] == projected["inventory"]["zones"]
    assert sum(
        shell["satellite_count"] for shell in context["satellite_shells"]
    ) == projected["inventory"]["satellites"]
    assert (
        context["zone_service"][
            "approx_service_envelope_surface_radius_km"
        ]
        % 10.0
        == 0.0
    )
    assert "Tool-derived" in context["zone_service"][
        "service_envelope_authority"
    ]


def test_portability_uses_composite_orbit_refs_and_input_derived_counts(
    tmp_path: Path,
    portable_slicer: Slicer,
) -> None:
    collected = collect_slice_difference_data(portable_slicer)
    assert collected["inventory"] == {
        "nodes": 2,
        "satellites": 2,
        "ground_stations": 0,
        "orbits": 2,
        "zones": 0,
        "slices": 4,
        "differences": 3,
        "route_entries": 0,
    }
    orbits = collected["topology"]["orbits"]
    assert [orbit["orbit_id"] for orbit in orbits] == [
        "shared-orbit",
        "shared-orbit",
    ]
    assert {orbit["orbit_ref"] for orbit in orbits} == {
        "65201|alpha-shell|shared-orbit",
        "65202|beta-shell|shared-orbit",
    }
    nodes = collected["topology"]["nodes"]
    assert {
        (node["node_id"], node["orbit_ref"])
        for node in nodes
    } == {
        (
            "65201/satellite/alpha-probe",
            "65201|alpha-shell|shared-orbit",
        ),
        (
            "65202/satellite/beta-probe",
            "65202|beta-shell|shared-orbit",
        ),
    }
    assert all(not record["routes"] for record in collected["slices"])
    assert all(
        record["difference_from_previous"]["is_empty"]
        for record in collected["slices"][1:]
    )

    output = tmp_path / "portable-tool"
    write_slice_difference_tool(portable_slicer, output)
    html = (output / "index.html").read_text(encoding="utf-8")
    assert "65201|alpha-shell|shared-orbit" in html
    assert "65202|beta-shell|shared-orbit" in html
    assert "Select a Zone" in html
    assert "Difference from previous Slice" in html

    production_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (TOOL_SOURCE, TOOL_UI_SOURCE)
    )
    for reference_specific in (
        "65010",
        "dynamic-shell",
        "gs-london",
        "gs-porto-alegre-reference",
        "gs-marsala-reference",
        "gs-yamaguchi-reference",
        "north-america-east",
        "northeast-priority",
        "32 Satellites",
    ):
        assert reference_specific not in production_source


@pytest.mark.skipif(
    shutil.which("geckodriver") is None,
    reason="geckodriver is required for the real-browser portability smoke",
)
def test_portability_browser_smoke_handles_duplicate_orbits_and_empty_state(
    portable_slicer: Slicer,
) -> None:
    from scripts.check_slice_tool_browser import run_browser_smoke

    ignored_output = (
        PROJECT_ROOT / "examples/mini_satellite_internet/output"
    )
    ignored_output.mkdir(parents=True, exist_ok=True)
    browser_root = Path(
        tempfile.mkdtemp(prefix="portability-browser-", dir=ignored_output)
    )
    try:
        output = browser_root / "tool"
        screenshots = browser_root / "screenshots"
        write_slice_difference_tool(portable_slicer, output)
        result = run_browser_smoke(
            output / "index.html",
            geckodriver=shutil.which("geckodriver") or "",
            screenshot_directory=screenshots,
            profile="portability",
        )

        assert result["status"] == "PASS"
        assert result["profile"] == "portability"
        assert result["inventory"]["nodes"] == 2
        assert result["inventory"]["slices"] == 4
        assert result["chunk_object_count"] == 1
        assert result["stale_completion_guard"] == {
            "loadedSliceId": 3,
            "chunkIds": [3],
        }
        assert (screenshots / result["screenshot"]).is_file()
    finally:
        shutil.rmtree(browser_root)


def test_dynamic_tool_is_repository_local_readonly_and_not_exported() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TOOL_PIPELINE_SOURCES
    )
    for forbidden in (
        "CircularOrbitPositionModel",
        "GeometricConnectionModel",
        "HopCountRouteModel",
        "compute_positions(",
        "compute_connection_slice(",
        "compute_route_slice(",
        "_compare_topology_slices",
        "simulator.models",
        "astropy",
    ):
        assert forbidden not in source
    for required in (
        ".get_topology(",
        ".get_slice(",
        ".create_difference(",
    ):
        assert required in source
    display_analysis = (
        PROJECT_ROOT / "tools/slice/display_analysis.py"
    ).read_text(encoding="utf-8")
    assert "elevation_deg" in display_analysis
    assert "geodetic_to_ecef" in display_analysis
    assert "def geodetic_to_ecef(" not in source
    assert "def elevation_deg(" not in source

    assert "tools" not in skyseed_v2.__all__
    for path in (PROJECT_ROOT / "src/skyseed_v2").rglob("*.py"):
        core_source = path.read_text(encoding="utf-8")
        assert "from tools" not in core_source
        assert "import tools" not in core_source


def test_frontend_is_selection_driven_and_lazy_chunked() -> None:
    source = TOOL_UI_SOURCE.read_text(encoding="utf-8")
    for identifier in (
        "slice-globe",
        "slice-range",
        "pick-node",
        "pick-connection",
        "pick-zone",
        "clear-inspection",
        "show-difference",
        "selected-detail",
        "path-detail",
        "gateway-route-zone",
        "gateway-route-gateway",
        "gateway-route-direction",
        "node-select",
        "connection-select",
        "zone-select",
        "route-source",
        "route-target",
        "difference-counts",
        "difference-context",
        "diagnostics",
        "slice-context",
        "slice-physical-context",
        "model-context",
        "play-slices",
        "playback-speed",
    ):
        assert f'id="{identifier}"' in source
    assert "window.__SKYSEED_SLICE_CHUNKS__" in source
    assert "element.src=entry.chunk" in source
    assert "requestedSliceId=null,loadGeneration=0" in source
    assert "generation=++loadGeneration" in source
    assert "generation===loadGeneration&&id===requestedSliceId" in source
    assert "else if(id!==requestedSliceId)delete" in source
    assert "return promise.then(activateIfCurrent)" in source
    assert (
        "window.__SKYSEED_SLICE_CHUNKS__[value.slice_id]=value" in source
    )
    assert "delete window.__SKYSEED_SLICE_CHUNKS__[key]" in source
    assert "function restoreSelectedPath()" in source
    assert "browser display restoration from current compact next-hop tuples" in source
    assert "function selectAt(clientX,clientY)" in source
    assert "function hitAt(x,y,mode=pickMode)" in source
    assert "selectedNodeId=id" in source
    assert "selectedConnectionRef=ref" in source
    assert "selectedZoneId=id" in source
    assert "function renderDifferenceContext()" in source
    assert "function renderSliceContext()" in source
    assert "routeChangedSegments" in source
    assert "affectedRouteSource" in source
    assert "function drawSelectedLabel(" in source
    assert "selectedLabelText" in source
    assert "hoverLabelText" in source
    assert "const DISPLAY_GEOMETRY=" in source
    assert (
        "1+Number(heightM)/DISPLAY_GEOMETRY.earth_reference_radius_m"
        in source
    )
    assert "canvas.dataset.firstSatelliteDisplayRadius" in source
    assert "canvas.dataset.selectedConnectionX" in source
    assert "function switchVisualSelection(kind)" in source
    assert "function clearActiveSelection()" in source
    assert "function clearInspection()" in source
    assert "function playSlices()" in source
    assert "function pausePlayback()" in source
    assert "function schedulePlayback(" in source
    assert "document.body.dataset.slicePlaybackState" in source
    assert "当前模型 / 策略 · 为什么这个 Slice 呈现为当前状态" in source
    assert 'applyGatewayRouteSelection(true)' not in source
    assert "if(same){clearActiveSelection();return;}" in source
    assert 'event.key==="Escape"' in source
    assert "function gatewayCandidatesForZone(" in source
    assert "function routeEvidenceRows(" in source
    assert "Gateway ingress Satellite" in source
    assert "formal path composition" in source
    assert "direct Gateway/terminal formal access" in source
    assert "incidentContextConnections" in source
    assert "endpointContextNodes" in source
    assert 'color="#93aeb8"' in source
    assert 'routeDirection="zone-to-gateway"' in source
    assert "other Zones served by that Satellite" in source
    assert "representative point only; full-area coverage is not guaranteed" in source
    assert "multiple Zones may validly share one service Satellite" in source
    assert 'currentSlice.difference_from_previous?.removed_connection_ids' in source
    assert 'servedZonesForSatellite(selectedNodeId)' in source
    assert "difference-patch" not in source
    activate_source = source[
        source.index("function activateSlice") : source.index(
            "function populateFallbacks"
        )
    ]
    assert "selectedNodeId=\"\"" not in activate_source
    assert "selectedConnectionRef=\"\"" not in activate_source
    assert "selectedZoneId=\"\"" not in activate_source
    assert "fetch(" not in source
    assert "XMLHttpRequest" not in source
    assert "Raw JSON / Audit View" not in source
    assert "DATA.slices" not in source


def test_bundle_is_complete_self_contained_and_checksum_valid(
    tmp_path: Path,
    slicer: Slicer,
) -> None:
    output = tmp_path / "slice"
    result = write_slice_difference_tool(slicer, output)

    assert result["status"] == "PASS"
    assert {path.name for path in output.iterdir()} == EXPECTED_FILES
    inventory = json.loads(
        (output / "inventory.json").read_text(encoding="utf-8")
    )
    paths = {item["path"] for item in inventory["files"]}
    assert {"index.html", "slice-manifest.json", "summary.txt"} <= paths
    assert sum(path.startswith("chunks/") for path in paths) == 65
    for item in inventory["files"]:
        path = output / item["path"]
        assert item["size_bytes"] == path.stat().st_size
        assert item["sha256"] == sha256(path.read_bytes()).hexdigest()

    checksum_lines = (
        output / "checksums.sha256"
    ).read_text(encoding="utf-8").splitlines()
    assert len(checksum_lines) == len(inventory["files"]) + 1
    for line in checksum_lines:
        digest, relative = line.split("  ", 1)
        assert digest == sha256((output / relative).read_bytes()).hexdigest()

    data_text = (output / "slice-manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(data_text)
    assert "slices" not in manifest
    assert len(manifest["slice_manifest"]) == 65
    html = (output / "index.html").read_text(encoding="utf-8")
    for forbidden in (
        "NodeId(",
        "ConnectionId(",
        "MappingProxyType",
        str(PROJECT_ROOT),
        "file://",
        "https://",
        "http://",
        "fetch(",
        "XMLHttpRequest",
    ):
        assert forbidden not in data_text
        assert forbidden not in html
    for required_id in (
        'id="slice-range"',
        'id="previous-slice"',
        'id="next-slice"',
        'id="play-slices"',
        'id="playback-speed"',
        'id="clear-inspection"',
        'id="node-select"',
        'id="connection-select"',
        'id="zone-select"',
            'id="route-source"',
            'id="route-target"',
            'id="show-difference"',
            'id="difference-counts"',
            'id="difference-context"',
            'id="slice-context"',
            'id="slice-physical-context"',
            'id="model-context"',
            'id="selected-detail"',
            'id="path-detail"',
            'id="gateway-route-zone"',
            'id="gateway-route-gateway"',
            'id="gateway-route-direction"',
            'id="slice-globe"',
    ):
        assert required_id in html
    assert "当前模型 / 策略 · 为什么这个 Slice 呈现为当前状态" in html
    assert "场景与模型参数在产物生成时投影" in html
