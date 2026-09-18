"""Small generic in-memory Producer fixture for semantic contract tests."""
from skyseed_v2.experiment.producer import Producer
from datetime import datetime, timedelta, timezone
from skyseed_v2.simulator.planner import (
    ConnectionSelectionState, OperationalConnectionId, OperationalConnectionKind, PlannerConfiguration,
)


def drain_simulator(simulator, *, evidence_path=None, planner=None):
    active = simulator.get_planner()
    assert planner is None or planner is active
    producer = Producer(simulator.get_timeline(),
        simulator.get_physical_world(), simulator.get_physical_link_model(), active,
        evidence_path=evidence_path)
    return drain_producer(simulator, producer)


def drain_producer(simulator, producer):
    plans, realizations = [], []
    try:
        while (realization := producer.produce_next_realization()) is not None:
            plans.append(producer.latest_plan)
            realizations.append(realization)
        snapshot = producer.snapshot()
    finally:
        producer.close()
    return tuple(plans), tuple(realizations), snapshot


MODEL_TIMESTAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def selection_state(retained_pairs=(), *, timestamp=MODEL_TIMESTAMP,
                    acquisition_duration=timedelta(0), operational=True):
    identifiers = frozenset(OperationalConnectionId(
        OperationalConnectionKind.INTER_SATELLITE if a.entity_kind == b.entity_kind == "satellite"
        else OperationalConnectionKind.GROUND_STATION_ACCESS, a, b) for a, b in retained_pairs)
    return ConnectionSelectionState(timestamp, timedelta(seconds=5), acquisition_duration,
        {key: timestamp for key in identifiers}, identifiers if operational else frozenset())
