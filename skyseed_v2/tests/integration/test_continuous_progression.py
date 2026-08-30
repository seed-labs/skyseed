from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from examples.mini_satellite_internet import main as reference_main
from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.simu import (
    create_mini_satellite_internet_progression,
    create_mini_satellite_internet_simulator,
    prepare_mini_satellite_internet_planning,
)
from skyseed_v2.experiment.execution import _RollingRealizationSupplier
from skyseed_v2.backend.bpf.runner import _resolve_nominal_boundaries
from skyseed_v2.shared import NodeId
from skyseed_v2.shared._durable_evidence import _semantic_value
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    ConnectionSlice,
    DefaultCoordinationPolicy,
    DependencyAwareForwardingPlanningModel,
    EarthFixedPosition,
    MidpointConnectionPlanningModel,
    RouteSlice,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)
from skyseed_v2.simulator.input import (
    SatelliteSimulationInput,
    SimulatorStaticInput,
)
from skyseed_v2.simulator._history_codec import _difference_data
from tools.bpf_compiler.reader import read_bpf_compiler_history
from tools.slice.reader import read_durable_slice_differences, read_slice_history
from tools.planner.reader import (
    read_durable_plan_sequence,
    read_durable_planner_inspection,
    read_planner_history,
)
from tools.planner.projection import project_planner_data


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
INPUTS = Path("examples/mini_satellite_internet/output/inputs")


def _canonical_full_chain():
    simulator = create_mini_satellite_internet_simulator(
        build_mini_satellite_internet_emulator()
    )
    slicer = simulator.get_slicer()
    slicer.create_all_slices()
    planner, plans, _ = prepare_mini_satellite_internet_planning(simulator)
    realizations = reference_main._compile_bpf_realizations(slicer, plans)
    return planner, plans, realizations


def _rolling_supplier(slicer, planner, directory, *, lookahead):
    slicer._enable_durable_history(directory / "slice-history.jsonl.gz")
    planner._enable_durable_history(directory / "planning-history.jsonl.gz")
    return _RollingRealizationSupplier(
        slicer,
        planner,
        generation_lookahead=lookahead,
        compiled_history_path=directory / "compiled-history.jsonl.gz",
    )


def _canonical_rolling_chain(directory: Path):
    simulator, planner = create_mini_satellite_internet_progression(INPUTS)
    slicer = simulator.get_slicer()
    supplier = _rolling_supplier(
        slicer,
        planner,
        directory,
        lookahead=timedelta(seconds=300),
    )
    realizations = []
    peaks = _owner_working_set(slicer, planner)
    while (value := supplier()) is not None:
        realizations.append(value)
        current = _owner_working_set(slicer, planner)
        peaks = {
            key: max(peaks[key], current[key]) for key in peaks
        }
    history = read_planner_history(directory / "planning-history.jsonl.gz")
    return planner, tuple(realizations), supplier, history, peaks


def _owner_working_set(slicer, planner) -> dict[str, int]:
    return {
        "slices": len(slicer.get_retained_slice_ids()),
        "timestamp_index": len(slicer._slices_by_timestamp),
        "differences": len(planner._differences),
        "route_contexts": len(
            set(planner._route_next_hops)
            | set(planner._route_path_connection_ids)
        ),
        "decisions": len(planner._decisions),
    }


def test_simulator_builder_does_not_advance_bounded_timeline() -> None:
    simulator, planner = create_mini_satellite_internet_progression(INPUTS)
    slicer = simulator.get_slicer()

    assert slicer.get_latest_slice_id() == 0
    assert planner._processed_through_slice_id == 0
    assert len(slicer.create_all_slices()) == reference_main.REAL_SLICE_COUNT


@pytest.fixture(scope="module")
def canonical_comparison(tmp_path_factory):
    directory = tmp_path_factory.mktemp("canonical-rolling-history")
    return _canonical_full_chain(), _canonical_rolling_chain(directory), directory


def test_rolling_300_seconds_preserves_complete_canonical_semantics(
    canonical_comparison,
) -> None:
    (full_planner, full_plans, full), (
        rolling_planner,
        rolling,
        _supplier,
        history,
        _peaks,
    ), directory = canonical_comparison

    rolling_plan_semantics = tuple(value["semantic"] for value in history["plans"])
    dynamic = read_slice_history(directory / "slice-history.jsonl.gz")
    compiled = read_bpf_compiler_history(directory / "compiled-history.jsonl.gz")
    full_slicer = full_planner._slicer

    assert rolling_plan_semantics == tuple(
        _semantic_value(value) for value in full_plans
    )
    assert tuple(
        (value["slice_id"], value["timestamp"], value["positions"])
        for value in dynamic["slices"]
    ) == tuple(
        (
            slice_id,
            None
            if (item := full_slicer.get_slice(slice_id)).timestamp is None
            else item.timestamp.isoformat(),
            {
                node_id.value: [
                    float(position.x_m),
                    float(position.y_m),
                    float(position.z_m),
                ]
                for node_id, position in item.positions.items()
            },
        )
        for slice_id in full_slicer.get_retained_slice_ids()
    )
    assert dynamic["differences"] == ()
    assert tuple(value["semantic"] for value in history["differences"]) == tuple(
        _difference_data(full_slicer.create_difference(slice_id - 1, slice_id))
        for slice_id in range(1, full_slicer.get_latest_slice_id() + 1)
    )
    assert len(rolling) == len(full) == 16
    assert all(
        current.start_slice_id == previous.end_slice_id
        and current.start_timestamp == previous.end_timestamp
        for previous, current in zip(rolling, rolling[1:])
    )
    assert rolling_planner.get_planning_decisions() == (
        full_planner.get_planning_decisions()
    )
    assert rolling_planner.get_consumed_differences() == (
        full_planner.get_consumed_differences()
    )
    assert rolling == full
    assert tuple(value["semantic"] for value in compiled["realizations"]) == tuple(
        _semantic_value(value) for value in full
    )
    assert _resolve_nominal_boundaries(rolling) == _resolve_nominal_boundaries(full)
    assert tuple(
        intent
        for realization in rolling
        for intent in realization.zone_edge_realizations
    ) == tuple(
        intent
        for realization in full
        for intent in realization.zone_edge_realizations
    )


def test_canonical_supplier_is_lazy_bounded_and_exhausts_once(
    canonical_comparison,
) -> None:
    _full, (
        planner,
        rolling,
        supplier,
        history,
        peaks,
    ), directory = canonical_comparison
    slicer = planner._slicer
    dynamic = read_slice_history(directory / "slice-history.jsonl.gz")
    compiled = read_bpf_compiler_history(directory / "compiled-history.jsonl.gz")

    assert len(rolling) == 16
    assert slicer.get_latest_slice_id() == 64
    assert supplier._realization_count == 16
    assert supplier._input_exhausted is True
    assert len(slicer.get_retained_slice_ids()) == 1
    assert len(slicer._slices_by_timestamp) == 1
    assert len(planner._differences) == 0
    assert not planner._route_next_hops
    assert not planner._route_path_connection_ids
    assert supplier._previous_realization is rolling[-1]
    # Slice 0 plus the 300-second generation window are simultaneously live
    # immediately before the first compiled frontier is released.
    assert peaks["slices"] <= 32
    assert len(dynamic["slices"]) == 65
    assert dynamic["differences"] == ()
    assert len(history["differences"]) == 64
    assert len(compiled["realizations"]) == 16
    assert supplier() is None
    assert supplier() is None


def test_released_histories_feed_tools_without_domain_reexecution(
    canonical_comparison,
) -> None:
    (full_planner, full_plans, _full), (
        rolling_planner,
        _rolling,
        _supplier,
        _history,
        _peaks,
    ), directory = canonical_comparison
    persisted_plans = read_durable_plan_sequence(
        directory / "planning-history.jsonl.gz"
    )
    slicer = rolling_planner._slicer
    slice_read = read_durable_slice_differences(
        directory / "slice-history.jsonl.gz",
        directory / "planning-history.jsonl.gz",
        topology=slicer.get_topology(),
        timeline=slicer.get_timeline(),
    )
    planner_read = read_durable_planner_inspection(
        directory / "slice-history.jsonl.gz",
        directory / "planning-history.jsonl.gz",
        persisted_plans,
        {"planning_windows": ()},
        topology=slicer.get_topology(),
        timeline=slicer.get_timeline(),
    )

    assert len(slice_read.snapshot["slices"]) == 65
    assert len(planner_read.snapshot["plans"]) == len(full_plans) == 16
    projected = project_planner_data(planner_read)
    assert projected["inventory"]["plans"] == 16
    assert projected["inventory"]["differences"] == 64
    assert persisted_plans == full_plans
    assert full_planner.get_plan() == full_plans[-1]


class _FixedPositionModel:
    def __init__(self, node_id: NodeId) -> None:
        self._node_id = node_id

    def compute_positions(self, topology, timestamp):
        return {self._node_id: EarthFixedPosition(7_000_000.0, 0.0, 0.0)}


class _EmptyConnectionModel:
    def compute_connection_slice(
        self, topology, timestamp, positions, previous_connection_slice
    ):
        return ConnectionSlice()


class _EmptyRouteModel:
    def compute_route_slice(
        self, topology, timestamp, connection_slice, previous_route_slice
    ):
        return RouteSlice()


def _unbounded_progression():
    node_id = NodeId(65001, "satellite", "unbounded-satellite")
    static_input = SimulatorStaticInput(
        satellites=(
            SatelliteSimulationInput(
                node_id=node_id,
                satellite_id="unbounded-satellite",
                shell_id="shell-1",
                orbit_id="orbit-1",
                orbit_index=0,
                altitude_km=550.0,
                inclination_deg=53.0,
                right_ascension_deg=0.0,
                mean_anomaly_deg=0.0,
                epoch_utc=START,
            ),
        )
    )
    simulator = Simulator(
        SimulationDefinition(
            static_input,
            SimulationTimeline(START, None, timedelta(seconds=10)),
        )
    )
    simulator.create_topology()
    slicer = simulator.create_slicer(
        position_model=_FixedPositionModel(node_id),
        connection_model=_EmptyConnectionModel(),
        route_model=_EmptyRouteModel(),
    )
    planner = simulator.create_planner(
        connection_model=MidpointConnectionPlanningModel(),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(),
        forwarding_model=DependencyAwareForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    )
    return slicer, planner


def test_open_ended_1000_step_working_sets_remain_bounded(tmp_path) -> None:
    slicer, planner = _unbounded_progression()
    supplier = _rolling_supplier(
        slicer,
        planner,
        tmp_path,
        lookahead=timedelta(seconds=20),
    )

    peaks = _owner_working_set(slicer, planner)
    while slicer.get_latest_slice_id() < 1_005:
        assert supplier() is not None
        current = _owner_working_set(slicer, planner)
        peaks = {key: max(peaks[key], current[key]) for key in peaks}

    assert slicer.get_latest_slice_id() >= 1_005
    assert planner._processed_through_slice_id >= 1_005
    assert supplier._realization_count >= 500
    assert len(slicer.get_retained_slice_ids()) == 1
    assert len(slicer._slices_by_timestamp) == 1
    assert not planner._differences
    assert not planner._route_next_hops
    assert not planner._route_path_connection_ids
    assert not planner._decisions
    assert supplier._previous_realization is not None
    # The previous frontier plus three generated observations can coexist at
    # the compile handoff; the count remains independent of total progress.
    assert peaks["slices"] <= 4
    assert peaks["differences"] <= 3
