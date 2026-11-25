from datetime import datetime, timedelta, timezone

import pytest

from skyseed.pc.compiler import BackendInstruction, PhysicalOperation, PhysicalPlan
from skyseed.runner.plan_queue import groups


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(seconds):
    return START + timedelta(seconds=seconds)


def op(seconds, target):
    return PhysicalOperation(at(seconds), "install", target, (BackendInstruction("bpf.rule", target, {}),))


def plan(start, end, *, stream="route", initial=(), operations=(), context=(), deployment="one"):
    return PhysicalPlan(at(start), at(end), stream, "bpf", deployment,
                        {"nodes": (), "destinations": ()}, initial, operations, context)


def test_overlap_tail_orders_actual_times_and_does_not_replay_initial_or_context():
    delayed = op(7, "delayed")
    first = plan(0, 8, initial=(op(0, "first"),), operations=(delayed,))
    second = plan(5, 10, initial=(op(5, "snapshot"),),
                  operations=(op(6, "next"),), context=(delayed,))
    result = list(groups([first, second]))
    assert [event.timestamp for event in result] == [at(0), at(5), at(6), at(7), at(10)]
    assert [operation.target for event in result for operation in event.routes] == ["first", "next", "delayed"]


def test_independent_link_and_route_streams_restore_once_and_keep_final_tail():
    result = list(groups([
        plan(0, 5, stream="link", initial=(op(0, "link"),)),
        plan(0, 8, initial=(op(0, "route"),), operations=(op(7, "tail"),)),
        plan(5, 7, stream="link", initial=(op(5, "duplicate"),)),
    ]))
    assert [operation.target for event in result for operation in event.links] == ["link"]
    assert result[-1].timestamp == at(8)


def test_starting_from_a_middle_batch_restores_its_future_context_once():
    result = list(groups([plan(5, 10, initial=(op(5, "initial"),),
                              context=(op(7, "prior-delayed"),), operations=(op(8, "new"),))]))
    assert [(operation.timestamp, operation.target) for event in result for operation in event.routes] == [
        (at(5), "initial"), (at(7), "prior-delayed"), (at(8), "new")]


def test_plan_loader_uses_the_next_start_as_watermark_without_reading_the_whole_run():
    read = []
    def source():
        for index in range(100):
            read.append(index)
            yield plan(index, index + 2, operations=(op(index + .5, str(index)),))
    queue = groups(source())
    assert next(queue).timestamp == at(0)
    assert read == [0, 1]
    assert next(queue).timestamp == at(.5)
    assert read == [0, 1]


@pytest.mark.parametrize("inputs,message", [
    ([plan(1, 2), plan(0, 1)], "ascend"),
    ([plan(0, 1), plan(1, 2, deployment="other")], "deployment"),
])
def test_wrong_run_material_fails_at_the_owner(inputs, message):
    with pytest.raises(ValueError, match=message):
        list(groups(inputs))


def test_later_batch_may_overlap_other_nodes_but_must_not_overtake_same_node():
    from skyseed.runner.plan_queue import PlanQueue
    queue = PlanQueue()
    queue.accept(plan(0, 8, operations=(op(7, "a"),)))
    queue.accept(plan(5, 10, operations=(op(6, "b"),)))
    with pytest.raises(ValueError, match="per-owner order"):
        queue.accept(plan(5, 10, operations=(op(6, "a"),)))
