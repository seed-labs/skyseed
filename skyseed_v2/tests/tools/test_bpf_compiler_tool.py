from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import tempfile

import pytest

from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.simu import (
    create_mini_satellite_internet_simulator,
    prepare_mini_satellite_internet_planning,
)
from skyseed_v2.backend.bpf import compile_bpf
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    Connection,
    ConnectionId,
    ConnectionKind,
    ConnectionSlice,
    ConnectionTransition,
    ConnectionTransitionOperation,
    DifferenceOperation,
    DirectionalConnectionParameters,
    EarthFixedPosition,
    ForwardingTransition,
    GroundStationRouteTarget,
    Plan,
    RouteKey,
    RouteSlice,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)
from tools.bpf_compiler import (
    collect_bpf_compiler_data,
    write_bpf_compiler_tool,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
SATELLITE = NodeId(65001, "satellite", "sat-1")
GROUND_STATION = NodeId(65001, "ground-station", "gs-1")
CONNECTION_ID = ConnectionId(
    ConnectionKind.GROUND_STATION_ACCESS,
    SATELLITE,
    GROUND_STATION,
)
ROUTE_KEY = RouteKey(
    SATELLITE,
    GroundStationRouteTarget(GROUND_STATION),
)


class _PositionModel:
    def compute_positions(self, topology, timestamp):
        return {
            SATELLITE: EarthFixedPosition(7_000_000.0, 0.0, 0.0),
            GROUND_STATION: EarthFixedPosition(6_378_137.0, 0.0, 0.0),
        }


class _SequenceConnectionModel:
    def __init__(self, values):
        self._values = iter(values)

    def compute_connection_slice(
        self, topology, timestamp, positions, previous_connection_slice
    ):
        return next(self._values)


class _SequenceRouteModel:
    def __init__(self, values):
        self._values = iter(values)

    def compute_route_slice(
        self, topology, timestamp, connection_slice, previous_route_slice
    ):
        return next(self._values)


@pytest.fixture(scope="module")
def canonical_compilation():
    emulator = build_mini_satellite_internet_emulator()
    simulator = create_mini_satellite_internet_simulator(emulator)
    simulator.get_slicer().create_all_slices()
    _, plans, _ = prepare_mini_satellite_internet_planning(simulator)
    realizations = _compile_sequence(simulator.get_slicer(), plans)
    return plans, realizations


@pytest.fixture(scope="module")
def generated(tmp_path_factory, canonical_compilation):
    plans, realizations = canonical_compilation
    output = tmp_path_factory.mktemp("bpf-compiler-tool")
    result = write_bpf_compiler_tool(plans, realizations, output)
    data = json.loads(
        (output / "bpf-compiler-inspection.json").read_text(encoding="utf-8")
    )
    return output, result, data


def test_projection_reads_continuous_compiled_sequence_without_recompiling(
    generated,
) -> None:
    _, result, data = generated

    assert result["status"] == "PASS"
    assert data["authority"] == {
        "status": "non-authoritative-view",
        "plans": "immutable public Plan publications",
        "compiled": "already-produced BpfCompiledRealization values",
        "compiler": "not invoked by this Tool",
        "simulator": "not queried or advanced by this Tool",
        "runtime": "not bound or inferred by this Tool",
    }
    assert [value["coverage"] for value in data["intervals"]] == [
        f"({start},{start + 4}]" for start in range(0, 64, 4)
    ]
    assert data["inventory"]["plans"] == 16
    assert data["inventory"]["resource_intents"] > 0
    assert data["inventory"]["parameter_intents"] > 0
    assert data["inventory"]["forwarding_intents"] > 0
    assert data["inventory"]["zone_edge_intents"] > 0


def test_exact_supported_lowering_relationships_are_exposed(generated) -> None:
    _, _, data = generated
    rows = [
        row for interval in data["intervals"] for row in interval["lowering"]
    ]

    assert any(
        row["family"] == "connection"
        and any(
            value["intent_id"].startswith("p0-resource-")
            for value in row["compiled_intents"]
        )
        and any(
            value["reason"] == "connection-establish"
            for value in row["compiled_intents"]
            if "reason" in value
        )
        for row in rows
    )
    assert any(
        row["family"] == "parameter" and row["compiled_intents"]
        for row in rows
    )
    assert all(
        row["mapping"]
        == "compiled-mechanical-output-no-plan-origin-claimed"
        for row in rows
        if row["family"] == "zone-edge"
    )


def test_zone_constraints_and_end_state_are_compiled_facts(generated) -> None:
    _, _, data = generated

    prefixes = {
        value["packet_prefix"]
        for interval in data["intervals"]
        for value in interval["end_state"]["zone_prefixes"]
    }
    assert len(prefixes) == 5
    assert all(value.startswith("198.18.") and value.endswith("/32") for value in prefixes)
    assert data["observed_behavior"]["plan_forwarding_precedences"] > 0
    assert data["observed_behavior"]["backend_technical_constraints"] > 0
    assert data["observed_behavior"]["runtime_bindings_claimed"] is False
    assert sum(
        len(interval["constraint_groups"]["plan-forwarding-precedence"])
        for interval in data["intervals"]
    ) == data["observed_behavior"]["plan_forwarding_precedences"]
    assert any(
        interval["end_state"]["resources"]
        and interval["end_state"]["parameters"]
        and interval["end_state"]["forwarding"]
        and interval["end_state"]["zone_edges"]
        for interval in data["intervals"]
    )


def test_same_next_hop_changed_remains_visible_as_formal_change(
    tmp_path: Path,
) -> None:
    plans, realizations = _same_next_hop_compilation()
    data = collect_bpf_compiler_data(plans, realizations)
    changed = data["intervals"][1]["compiled"]["forwarding"][0]

    assert changed["operation"] == "changed"
    assert changed["same_next_hop_changed"] is True
    assert changed["previous"]["next_hop"] == changed["current"]["next_hop"]
    write_bpf_compiler_tool(plans, realizations, tmp_path)
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert '"same_next_hop_changed":true' in page
    assert "SAME-NEXT-HOP CHANGED — NOT A NO-OP" in page


def test_page_prioritizes_compiled_backend_operations_and_is_offline(
    generated,
) -> None:
    output, _, _ = generated
    page = (output / "index.html").read_text(encoding="utf-8")

    for identifier in (
        "plan-timeline",
        "plan-select",
        "backend-operations",
        "lowering-list",
        "resource-list",
        "parameter-list",
        "forwarding-list",
        "zone-list",
        "constraint-list",
        "end-state",
        "raw-json",
    ):
        assert f'id="{identifier}"' in page
    assert "Compiled backend operations / symbolic BPF realization" in page
    assert "BpfCompiledRealization operation inventory" in page
    assert "RESOURCE ESTABLISH" in page
    assert "RESOURCE RETIRE" in page
    assert "ATTACHMENT REQUIRE" in page
    assert "QDISC TARGET INITIAL" in page
    assert "QDISC UPDATE SPARSE" in page
    assert "FORWARD ADD" in page
    assert "FORWARD CHANGE" in page
    assert "FORWARD REMOVE" in page
    assert "ZONE EDGE ACTIVATE" in page
    assert "Backend ordering / dependency relationships" in page
    assert 'class="tab active" data-view="operations"' in page
    assert 'data-view="lowering">Plan → BPF provenance' in page
    assert "SAME-NEXT-HOP CHANGED — NOT A NO-OP" in page
    assert "does not scan raw Slice assignments" in page
    assert "Previous intended baseline + interval work" in page
    assert "http://" not in page
    assert "https://" not in page
    assert "fetch(" not in page
    assert "<script src=" not in page
    assert "<link " not in page


def test_writer_is_deterministic_and_emits_checksummed_bundle(
    generated,
    canonical_compilation,
    tmp_path: Path,
) -> None:
    output, result, _ = generated
    plans, realizations = canonical_compilation
    repeated = write_bpf_compiler_tool(plans, realizations, tmp_path)

    assert result["files"] == repeated["files"] == (
        "index.html",
        "bpf-compiler-inspection.json",
        "summary.txt",
        "inventory.json",
        "checksums.sha256",
    )
    for filename in result["files"]:
        assert (output / filename).read_bytes() == (tmp_path / filename).read_bytes()
    assert "status=PASS" in (output / "summary.txt").read_text(encoding="utf-8")


def test_tool_sources_never_compile_or_depend_on_execution_details() -> None:
    root = Path(__file__).resolve().parents[2] / "tools/bpf_compiler"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in root.glob("*.py")
    )

    assert "compile_bpf(" not in source
    assert ".create_planner(" not in source
    assert ".synchronize(" not in source
    assert "RouteModel" not in source
    assert "Slicer" not in source
    for runtime_detail in (
        "bpftool",
        "ifindex",
        "netns",
        "veth",
        "preload",
        "generation",
    ):
        assert runtime_detail not in source.lower()


@pytest.mark.skipif(
    shutil.which("geckodriver") is None,
    reason="geckodriver is required for the real-browser Compiler smoke",
)
def test_canonical_bpf_compiler_page_browser_smoke(
    canonical_compilation,
) -> None:
    from scripts.check_bpf_compiler_tool_browser import (
        run_bpf_compiler_browser_smoke,
    )

    project_root = Path(__file__).resolve().parents[2]
    ignored_output = project_root / "examples/mini_satellite_internet/output"
    ignored_output.mkdir(parents=True, exist_ok=True)
    browser_root = Path(
        tempfile.mkdtemp(prefix="bpf-compiler-browser-", dir=ignored_output)
    )
    try:
        plans, realizations = canonical_compilation
        tool_output = browser_root / "tool"
        screenshots = browser_root / "screenshots"
        write_bpf_compiler_tool(plans, realizations, tool_output)
        result = run_bpf_compiler_browser_smoke(
            tool_output / "index.html",
            geckodriver=shutil.which("geckodriver") or "",
            screenshot_directory=screenshots,
        )

        assert result["status"] == "PASS"
        assert result["plans"] == 16
        assert (screenshots / result["screenshot"]).is_file()
    finally:
        shutil.rmtree(browser_root)


def _same_next_hop_compilation():
    parameters = DirectionalConnectionParameters(1_000_000, 10, 20)
    connection = Connection(CONNECTION_ID, parameters, parameters)
    simulator = Simulator(
        SimulationDefinition(
            SimulatorStaticInput(
                satellites=(
                    SatelliteSimulationInput(
                        SATELLITE,
                        "sat-1",
                        "shell-1",
                        "orbit-1",
                        0,
                        550.0,
                        53.0,
                        0.0,
                        0.0,
                        START,
                    ),
                ),
                ground_stations=(
                    GroundStationSimulationInput(
                        GROUND_STATION,
                        "gs-1",
                        0.0,
                        0.0,
                        0.0,
                        "gateway",
                        "Skyseed",
                    ),
                ),
            ),
            SimulationTimeline(
                START,
                START + timedelta(seconds=10),
                timedelta(seconds=10),
            ),
        )
    )
    simulator.create_topology()
    slicer = simulator.create_slicer(
        position_model=_PositionModel(),
        connection_model=_SequenceConnectionModel(
            (ConnectionSlice((connection,)), ConnectionSlice((connection,)))
        ),
        route_model=_SequenceRouteModel(
            (
                RouteSlice(((ROUTE_KEY, GROUND_STATION),)),
                RouteSlice(((ROUTE_KEY, GROUND_STATION),)),
            )
        ),
    )
    slicer.create_all_slices()
    first_forwarding = ForwardingTransition(
        START,
        ROUTE_KEY,
        DifferenceOperation.ADDED,
        GROUND_STATION,
    )
    first = Plan(
        start_slice_id=0,
        start_timestamp=None,
        end_slice_id=1,
        end_timestamp=START,
        connection_transitions=(
            ConnectionTransition(
                START,
                ConnectionTransitionOperation.ESTABLISH,
                CONNECTION_ID,
                connection,
            ),
        ),
        forwarding_transitions=(first_forwarding,),
    )
    second = Plan(
        start_slice_id=1,
        start_timestamp=START,
        end_slice_id=2,
        end_timestamp=START + timedelta(seconds=10),
        forwarding_transitions=(
            ForwardingTransition(
                START + timedelta(seconds=10),
                ROUTE_KEY,
                DifferenceOperation.CHANGED,
                GROUND_STATION,
            ),
        ),
    )
    first_result = compile_bpf(first, slicer=slicer)
    second_result = compile_bpf(second, slicer=slicer, previous=first_result)
    return (first, second), (first_result, second_result)


def _compile_sequence(slicer, plans):
    compiled = []
    previous = None
    for plan in plans:
        result = compile_bpf(plan, slicer=slicer, previous=previous)
        compiled.append(result)
        previous = result
    return tuple(compiled)
