"""Contract tests for independent late nodes and fixed scheduled times."""
from datetime import datetime, timedelta, timezone
import json
import time

from skyseed.pc.compiler import BackendInstruction, PhysicalOperation, PhysicalPlan
from skyseed.runner import Runner, Deployment
import skyseed.runner.runner as implementation


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestDeployment(Deployment):
    __test__ = False

    def containers(self):
        return {node: {"Config": {"Image": "test", "Labels": {
                    "org.seedsecuritylabs.seedemu.meta.skyseed.scene_id": node}},
                "State": {"Running": True, "Pid": 1}, "NetworkSettings": {"Networks": {}}}
                for node in ("a", "b")}


class SlowNodeChannel:
    def __init__(self, *args):
        self.jobs = {}
        self.generation = 0
        self.active = 0

    def request(self, command, **parameters):
        started = time.monotonic()
        result = None
        if command == "prepare_node":
            self.jobs[parameters["job"]] = [100 if parameters["node"] == "a" else 1, 1]
        elif command == "generation":
            self.generation += 1
            self.jobs[parameters["job"]] = [1, self.generation]
        elif command == "reclaim":
            self.jobs[parameters["job"]] = [1, None]
        elif command == "step":
            job = self.jobs[parameters["job"]]
            time.sleep(.002)
            job[0] -= 1
            result = {"done": job[0] == 0, "value": job[1]}
        elif command == "publish":
            result = {"previous": self.active, "activated": time.monotonic()}
            self.active = parameters["generation"]
        return {"result": result, "started": started, "completed": time.monotonic()}

    def close(self):
        pass


def test_late_table_does_not_block_another_node_or_move_the_origin(monkeypatch, tmp_path):
    monkeypatch.setattr(implementation, "CommandChannel", SlowNodeChannel)
    targets = {"nodes": tuple({"node_id": node, "service": node, "static_addresses": (),
                               "identity_address": f"198.18.0.{index}/32"}
                              for index, node in enumerate(("a", "b"), 1)),
               "destinations": (), "address_pools": ("198.18.0.0/16",)}
    timestamp = START + timedelta(seconds=.02)
    operations = tuple(PhysicalOperation(timestamp, "install", node, (
        BackendInstruction("bpf.rule", node, {}),)) for node in ("a", "b"))
    plan = PhysicalPlan(START, START + timedelta(seconds=.4), "route", "bpf", "one",
                        targets, operations=operations)
    with Runner([plan], TestDeployment(tmp_path), output=tmp_path / "run", program=b"test") as runner:
        runner.run()
    records = [json.loads(line) for line in (tmp_path / "run/execution.jsonl").read_text().splitlines()]
    clock, = [record for record in records if record["kind"] == "clock"]
    adoption = {node: record for record in records if record["kind"] == "route"
                for item in record["adoptions"] for node in item["nodes"]}
    assert adoption["b"]["activated"] < adoption["a"]["activated"]
    assert adoption["b"]["activated"] >= clock["origin"] + .02
    assert adoption["a"]["activated"] >= clock["origin"] + .02
    assert adoption["a"]["adoptions"][0]["scheduled"] == timestamp.isoformat()
    assert records[-1]["completed"] >= clock["origin"] + .4


def inputs(times):
    targets = {"nodes": tuple({"node_id": node, "service": node, "static_addresses": (),
                               "identity_address": f"198.18.0.{index}/32"}
                              for index, node in enumerate(("a", "b"), 1)),
               "destinations": (), "address_pools": ("198.18.0.0/16",)}
    return [PhysicalPlan(START + timedelta(seconds=start), START + timedelta(seconds=end),
                         "route", "bpf", "one", targets, operations=tuple(
                             PhysicalOperation(START + timedelta(seconds=at), "install", node,
                                               (BackendInstruction("bpf.rule", node, {}),))
                             for at, node in operations))
            for start, end, operations in times]


def publications(tmp_path):
    records = [json.loads(line) for line in (tmp_path / "run/execution.jsonl").read_text().splitlines()]
    origin = next(record["origin"] for record in records if record["kind"] == "clock")
    return [(node, record["activated"] - origin) for record in records if record["kind"] == "route"
            for adoption in record["adoptions"] for node in adoption["nodes"]]


def test_three_slow_node_updates_do_not_block_later_independent_work(monkeypatch, tmp_path):
    monkeypatch.setattr(implementation, "CommandChannel", SlowNodeChannel)
    plans = inputs([(0, .01, []), (.01, .02, [(.01, "a")]),
                    (.02, .03, [(.02, "a")]), (.03, .04, [(.03, "a")]),
                    (.04, .1, [(.04, "b")])])
    with Runner(plans, TestDeployment(tmp_path), output=tmp_path / "run", program=b"test") as runner:
        runner.run()
    adopted = publications(tmp_path)
    assert adopted[0][0] == "b"
    assert [node for node, _ in adopted].count("a") == 3


def test_received_operations_run_while_later_input_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(implementation, "CommandChannel", SlowNodeChannel)
    plans = inputs([(0, .1, []), (.1, .2, [(.15, "b")]), (.2, .5, [])])
    def source():
        yield plans[0]
        yield plans[1]
        time.sleep(.4)
        yield plans[2]
    with Runner(source(), TestDeployment(tmp_path), output=tmp_path / "run", program=b"test") as runner:
        runner.run()
    adopted = publications(tmp_path)
    assert adopted[0][0] == "b"
    assert .15 <= adopted[0][1] < .3


def test_prepare_restores_delayed_same_start_stream_before_clock(monkeypatch, tmp_path):
    from dataclasses import replace
    calls = []
    class Channel(SlowNodeChannel):
        def request(self, command, **parameters):
            if command == "apply":
                calls.extend(parameters["instructions"])
            return super().request(command, **parameters)
    monkeypatch.setattr(implementation, "CommandChannel", Channel)
    first, second, later = inputs([(0, .1, []), (0, .1, [(0, "b")]), (.1, .2, [(.15, "a")])])
    first = replace(first, stream="link", forwarding_backend="linux")
    second = replace(second, forwarding_backend="linux", initial_operations=second.operations, operations=())
    later = replace(later, forwarding_backend="linux")
    def source():
        yield first
        time.sleep(.05)
        yield second
        yield later
    with Runner(source(), TestDeployment(tmp_path), output=tmp_path / "run") as runner:
        assert runner.origin is None
        assert [item["node_id"] for item in calls] == ["b"]


def test_stop_finishes_current_call_without_waiting_for_future_plan_end(monkeypatch, tmp_path):
    from threading import Timer
    monkeypatch.setattr(implementation, "CommandChannel", SlowNodeChannel)
    plans = inputs([(0, 600, [])])
    with Runner(plans, TestDeployment(tmp_path), output=tmp_path / "run", program=b"test") as runner:
        timer = Timer(.05, runner.stop)
        timer.start()
        runner.run()
        timer.join()
    records = [json.loads(line) for line in (tmp_path / "run/execution.jsonl").read_text().splitlines()]
    assert records[-1]['kind'] == 'stopped'
    assert not any(event['kind'] == 'drained' for event in records)


def test_stop_during_backend_call_does_not_submit_next_due_operation(monkeypatch, tmp_path):
    class StopChannel(SlowNodeChannel):
        calls = 0
        def request(self, command, **parameters):
            response = super().request(command, **parameters)
            if command == 'apply':
                self.calls += 1
                runner.stop()
            return response
    monkeypatch.setattr(implementation, 'CommandChannel', StopChannel)
    targets = {'nodes': (), 'destinations': ()}
    operation = PhysicalOperation(START + timedelta(seconds=.01), 'establish', (), ())
    plan = PhysicalPlan(START, START + timedelta(seconds=600), 'link', 'linux', 'one', targets,
                        operations=(operation, operation))
    with Runner([plan], TestDeployment(tmp_path), output=tmp_path / 'run') as runner:
        runner.run()
        assert runner._channel.calls == 1
    records = [json.loads(line) for line in (tmp_path / 'run/execution.jsonl').read_text().splitlines()]
    assert [record['kind'] for record in records].count('link') == 1
    assert records[-1]['kind'] == 'stopped'


def test_stop_during_publication_does_not_start_background_work(monkeypatch, tmp_path):
    class StopOnPublish(SlowNodeChannel):
        def __init__(self, *args):
            super().__init__(*args)
            self.commands = []
        def request(self, command, **parameters):
            self.commands.append(command)
            response = super().request(command, **parameters)
            if command == 'publish':
                runner.stop()
            return response
    monkeypatch.setattr(implementation, 'CommandChannel', StopOnPublish)
    plans = inputs([(0, .1, [(.01, 'b')]), (.1, 600, [(.1, 'b')])])
    with Runner(plans, TestDeployment(tmp_path), output=tmp_path / 'run', program=b'test') as runner:
        runner.run()
        assert runner._channel.commands[-1] == 'publish'
    records = [json.loads(line) for line in (tmp_path / 'run/execution.jsonl').read_text().splitlines()]
    assert records[-1]['kind'] == 'stopped'
