"""Build an S4-scale static Emulator with a small explicit ground Internet."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from json import dumps
from pathlib import Path
from time import perf_counter

from seedemu.core import Emulator
from seedemu.layers import Ebgp, Ibgp, Ospf, PeerRelationship, Routing
from yaml import safe_load

from examples.reference.v1_static_input_adapter import V1StaticInputAdapter
from skyseed_v2 import (
    ConstellationBuildResult,
    ConstellationBuilder,
    GroundStationBuildResult,
    GroundStationBuilder,
    SimulationEntityCatalog,
    SimulationEntityCatalogBuilder,
)
from skyseed_v2.seed import (
    SatelliteBase,
    SkyseedAutonomousSystem,
    SkyseedDockerCompiler,
)


SKYSEED_ASN = 65000
GROUND_ASNS = (65101, 65102)
IX_IDS = (100, 101)
GROUND_PREFIXES = ("10.210.0.0/24", "10.220.0.0/24")
GROUND_ROUTER_ADDRESSES = ("10.210.0.254", "10.220.0.254")
GROUND_ROUTER_IX_ADDRESSES = ("10.100.0.254", "10.101.0.254")
GROUND_ROUTER_NAMES = ("ground-a-router", "ground-b-router")


@dataclass(frozen=True, slots=True)
class StaticScaleSummary:
    shell_count: int
    orbit_count: int
    satellite_count: int
    ground_station_count: int
    ordinary_as_count: int
    ix_count: int
    ebgp_peering_count: int
    catalog_node_count: int
    compose_service_count: int | None = None
    compose_file_size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class ScaleScenario:
    emulator: Emulator
    skyseed_as: SkyseedAutonomousSystem
    ordinary_ases: tuple[object, ...]
    ground_routers: tuple[object, ...]
    peering_ground_stations: tuple[object, ...]
    constellation: ConstellationBuildResult
    ground_stations: GroundStationBuildResult
    catalog: SimulationEntityCatalog
    ebgp: Ebgp
    summary: StaticScaleSummary
    timings_seconds: dict[str, float]


def default_reference_root() -> Path:
    return Path(__file__).resolve().parents[2] / "skyseed_v1"


def reference_adapter(reference_root: Path) -> V1StaticInputAdapter:
    return V1StaticInputAdapter(
        orbit_catalog_path=(
            reference_root
            / "data/orbits/planned_shell_orbit_data_s1_s5.json"
        ),
        gateway_catalog_path=reference_root / "data/ground/starlink_gateways.json",
        gateway_binding_path=(
            reference_root
            / "data/ground/s4_visualization_gateway_binding_12.json"
        ),
    )


def build_scenario(reference_root: Path | None = None) -> ScaleScenario:
    """Construct the scale experiment without compiling or starting containers."""

    parse_started = perf_counter()
    specifications = reference_adapter(
        reference_root or default_reference_root()
    ).load(
        reference_shell_label="S4",
        logical_shell_id="scale-shell",
        operator="Starlink",
        station_type="gateway",
    )
    input_parse_seconds = perf_counter() - parse_started

    construction_started = perf_counter()
    emulator = Emulator()
    base = SatelliteBase()
    for ix_id in IX_IDS:
        base.createInternetExchange(ix_id, create_rs=False)

    skyseed_as = SkyseedAutonomousSystem(SKYSEED_ASN)
    skyseed_as.setIbgpMode("disabled")
    base.setAutonomousSystem(skyseed_as)
    constellation = ConstellationBuilder().build(
        skyseed_as, specifications.shell
    )
    ground_stations = GroundStationBuilder().build(
        skyseed_as, specifications.ground_stations
    )

    stations_by_ix: dict[int, list[object]] = {ix_id: [] for ix_id in IX_IDS}
    for station_index, station in enumerate(ground_stations.ground_stations):
        ix_position = station_index % len(IX_IDS)
        ix_id = IX_IDS[ix_position]
        host_octet = 10 + station_index // len(IX_IDS)
        station.joinNetwork(
            f"ix{ix_id}",
            f"10.{ix_id}.0.{host_octet}",
        )
        stations_by_ix[ix_id].append(station)

    ordinary_ases = []
    ground_routers = []
    for (
        asn,
        ix_id,
        prefix,
        service_address,
        ix_address,
        router_name,
    ) in zip(
        GROUND_ASNS,
        IX_IDS,
        GROUND_PREFIXES,
        GROUND_ROUTER_ADDRESSES,
        GROUND_ROUTER_IX_ADDRESSES,
        GROUND_ROUTER_NAMES,
    ):
        ground_as = base.createAutonomousSystem(asn)
        ground_as.createNetwork("service-net", prefix)
        ground_router = ground_as.createRouter(router_name)
        ground_router.joinNetwork("service-net", service_address)
        ground_router.joinNetwork(f"ix{ix_id}", ix_address)
        ordinary_ases.append(ground_as)
        ground_routers.append(ground_router)

    peering_ground_stations = tuple(
        stations_by_ix[ix_id][0] for ix_id in IX_IDS
    )
    ospf = Ospf().maskAsn(SKYSEED_ASN)
    ibgp = Ibgp().maskAsn(SKYSEED_ASN)
    ebgp = Ebgp()
    for (
        ix_id,
        station,
        ground_asn,
        ground_router_name,
    ) in zip(
        IX_IDS,
        peering_ground_stations,
        GROUND_ASNS,
        GROUND_ROUTER_NAMES,
    ):
        ebgp.addPrivatePeeringByRouters(
            ix_id,
            SKYSEED_ASN,
            station.ground_station_id,
            ground_asn,
            ground_router_name,
            PeerRelationship.Unfiltered,
        )
    for layer in (base, Routing(), ospf, ibgp, ebgp):
        emulator.addLayer(layer)
    domain_construction_seconds = perf_counter() - construction_started

    catalog_started = perf_counter()
    catalog = SimulationEntityCatalogBuilder().build(
        (skyseed_as, *ordinary_ases)
    )
    catalog_construction_seconds = perf_counter() - catalog_started
    summary = StaticScaleSummary(
        shell_count=len(constellation.shells),
        orbit_count=len(constellation.orbits),
        satellite_count=len(constellation.satellites),
        ground_station_count=len(ground_stations.ground_stations),
        ordinary_as_count=len(ordinary_ases),
        ix_count=len(IX_IDS),
        ebgp_peering_count=len(ebgp.getPrivatePeerings()),
        catalog_node_count=len(catalog.list_nodes()),
    )
    return ScaleScenario(
        emulator=emulator,
        skyseed_as=skyseed_as,
        ordinary_ases=tuple(ordinary_ases),
        ground_routers=tuple(ground_routers),
        peering_ground_stations=peering_ground_stations,
        constellation=constellation,
        ground_stations=ground_stations,
        catalog=catalog,
        ebgp=ebgp,
        summary=summary,
        timings_seconds={
            "input_parse": input_parse_seconds,
            "domain_construction": domain_construction_seconds,
            "catalog_construction": catalog_construction_seconds,
        },
    )


def compile_scenario(
    scenario: ScaleScenario,
    output: Path,
    *,
    override: bool,
    internet_map_enabled: bool,
) -> tuple[StaticScaleSummary, dict[str, float]]:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    render_started = perf_counter()
    scenario.emulator.render()
    render_seconds = perf_counter() - render_started

    compile_started = perf_counter()
    scenario.emulator.compile(
        SkyseedDockerCompiler(internet_map_enabled=internet_map_enabled),
        str(output),
        override=override,
    )
    compile_seconds = perf_counter() - compile_started

    compose_path = output / "docker-compose.yml"
    compose = safe_load(compose_path.read_text(encoding="utf-8"))
    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict):
        raise RuntimeError("compiled Compose must contain a services mapping")
    summary = StaticScaleSummary(
        **{
            **asdict(scenario.summary),
            "compose_service_count": len(compose["services"]),
            "compose_file_size_bytes": compose_path.stat().st_size,
        }
    )
    timings = {
        **scenario.timings_seconds,
        "render": render_seconds,
        "compile": compile_seconds,
    }
    report = {
        "scenario": "s4-scale-static-emulator",
        "semantics": {
            "s4_is_scale_reference_only": True,
            "dynamic_satellite_network": False,
            "reference_epoch_in_emulator": False,
            "historical_service_prefixes_imported": False,
        },
        "summary": asdict(summary),
        "timings_seconds": {
            key: round(value, 6) for key, value in sorted(timings.items())
        },
    }
    (output / "scenario-summary.json").write_text(
        dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary, timings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference-root",
        type=Path,
        default=default_reference_root(),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("output")
        / "s4_scale_static_emulator",
    )
    parser.add_argument("--override", action="store_true")
    parser.add_argument("--no-internet-map", action="store_true")
    arguments = parser.parse_args()

    scenario = build_scenario(arguments.reference_root)
    summary, timings = compile_scenario(
        scenario,
        arguments.output,
        override=arguments.override,
        internet_map_enabled=not arguments.no_internet_map,
    )
    print(dumps(asdict(summary), sort_keys=True))
    print(
        dumps(
            {
                key: round(value, 6)
                for key, value in sorted(timings.items())
            },
            sort_keys=True,
        )
    )
    print(f"compiled S4-scale static Emulator to {arguments.output}")


if __name__ == "__main__":
    main()
