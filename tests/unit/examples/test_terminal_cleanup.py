"""A failed setup must not hide or retain later independent resources."""
import errno
import io
from unittest.mock import Mock

from examples.s4_handover.experiment.ut.attachment import Attachment


def test_unserved_ut_and_unplanned_ground_station_can_prepare(tiny_scene, tmp_path, monkeypatch):
    import json
    from datetime import timedelta
    from types import SimpleNamespace

    import yaml
    from seedemu import Docker, Routing
    from skyseed import SkyBase, SkyEmulator, PlanCompiler, save_plans
    from skyseed.pc.network import ForwardingRule, RouteDestination, RoutePlan
    from examples.s4_handover.experiment.ut import attachment

    tiny_scene.create_ground_station("G2", latitude_deg=0, longitude_deg=0)
    tiny_scene.create_zone("zone", latitude_deg=30, longitude_deg=120)
    tiny_scene.data["experiment"] = {"zone": "zone", "ut_address": "192.0.2.2"}
    emulator, base = SkyEmulator(), SkyBase()
    autonomous_system = base.createSkyAutonomousSystem(150)
    autonomous_system.loadScene(tiny_scene)
    autonomous_system.createNetwork("lan", "10.150.0.0/24")
    emulator.addLayer(base)
    emulator.addLayer(Routing())
    emulator.getNode("G1").joinNetwork("lan", "10.150.0.1")
    emulator.getNode("G2").joinNetwork("lan", "10.150.0.2")
    emulator.render()
    directory = tmp_path / "deployment"
    emulator.compile(Docker(internetMapEnabled=False), str(directory))

    # The real plan references G1 but neither G2 nor the UT's Zone.
    start = tiny_scene.get_shells()[0].epoch
    rule = ForwardingRule(RouteDestination("node", "G1"), "pop_deliver", incoming_label=31)
    plans = list(PlanCompiler(directory).compile_plans(route_plans=[
        RoutePlan(start, start + timedelta(seconds=60), {"G1": (rule,)})]))
    assert {node["node_id"] for node in plans[0].targets["nodes"]} == {"G1"}
    plan_file = tmp_path / "plans.plan"
    save_plans(plan_file, plans)

    # Only replace live Docker boundaries; retain SEED's real identity labels.
    compose = yaml.safe_load((directory / "docker-compose.yml").read_text())
    containers = {service: {"Name": "/" + service, "State": {"Pid": index},
                           "Config": {"Labels": spec.get("labels", {}), "Image": "fixture-image"}}
                  for index, (service, spec) in enumerate(compose["services"].items(), start=1)}
    deployment = SimpleNamespace(containers=lambda: containers)
    docker = Mock(side_effect=[SimpleNamespace(stdout="ut-id\n"),
                              SimpleNamespace(stdout=json.dumps([{"State": {"Pid": 999}}])),
                              SimpleNamespace(), SimpleNamespace()])
    helper = Mock(stdin=io.StringIO(), stdout=io.StringIO("ready\n"))
    helper.wait.return_value = 0
    monkeypatch.setattr(attachment.subprocess, "run", docker)
    monkeypatch.setattr(attachment.subprocess, "Popen", Mock(return_value=helper))

    with attachment.TerminalAttachment(tiny_scene, deployment, plan_file, tmp_path,
                                        ground_stations=("G2", "G1")):
        config = json.loads((tmp_path / "terminal.json").read_text())
        assert config["nodes"] == {}
        assert config["changes"] == {}
        assert config["ground_ips"] == {"G1": plans[0].targets["nodes"][0]["identity_address"].split("/")[0]}
        assert set(config["containers"]) == {"G1", "G2"}
    helper.wait.assert_called_once()


def test_unserved_ut_keeps_identity_and_explicit_unattached_state(tmp_path):
    import json

    attachment = Attachment.__new__(Attachment)
    attachment.config = {"zone": "zone", "ut_address": "192.0.2.2", "ground_ips": {"G1": "198.18.0.1"}}
    channel = Mock()
    channel.link_index.return_value = 7
    attachment.nodes = {"ut": {"socket": channel}}
    attachment.output = tmp_path
    attachment.current = None
    attachment.changes = {}
    attachment.stopped = False
    attachment.log = io.StringIO()
    attachment.prepare()
    channel.address.assert_called_once_with(7, "192.0.2.2/32")
    channel.route.assert_called_once_with("198.18.0.1/32", protocol=252)
    assert json.loads((tmp_path / "attachment-state.json").read_text()) == {"node": None}

    events = [{"kind": "route", "activated": 101, "adoptions": [{
        "scheduled": "2026-01-01T00:00:00+00:00", "nodes": ["G1"], "instruction_counts": {"G1": 1}}]},
        {"kind": "stopped"}]
    (tmp_path / "execution.jsonl").write_text(''.join(json.dumps(event) + '\n' for event in events))
    attachment.follow()
    assert attachment.current is None
    assert attachment.stopped
    channel.create_veth.assert_not_called()


def test_partial_attachment_cleanup_continues_after_missing_route_and_ingress(monkeypatch):
    attachment = Attachment.__new__(Attachment)
    attachment.config = {"ground_ips": {"gs": "198.18.0.1"}, "ut_address": "192.0.2.2"}
    attachment.current = "star"
    attachment.original = 99
    attachment.log = io.StringIO()
    star, ut = Mock(), Mock()
    star.ingress.side_effect = OSError(errno.ENOENT, "missing rule")
    star.link_index.return_value = 11
    ut.route.side_effect = OSError(errno.ESRCH, "missing route")
    attachment.nodes = {"star": {"socket": star, "namespace": 10},
                        "ut": {"socket": ut, "namespace": 20, "identity": 22}}
    close = Mock()
    monkeypatch.setattr("examples.s4_handover.experiment.ut.attachment.os.close", close)
    attachment.close()
    assert [call.args[0] for call in star.delete_link.call_args_list] == [11]
    assert [call.args[0] for call in ut.delete_link.call_args_list] == [22]
    assert [call.args[0] for call in close.call_args_list] == [10, 20, 99]
    star.close.assert_called_once()
    ut.close.assert_called_once()


def test_late_same_time_adoption_does_not_attach_to_future_service_state():
    attachment = Attachment.__new__(Attachment)
    attachment.current = None
    attachment.config = {}
    attachment.change_positions = {}
    attachment.changes = {'2026-01-01T00:00:00+00:00|a': [True, None, False]}
    active = set()
    def event(count):
        return {'adoptions': [{'scheduled': '2026-01-01T00:00:00+00:00', 'nodes': ['a'], 'instruction_counts': {'a': count}}]}
    assert attachment.adopted_service(event(1), active) == 'a'
    attachment.current = 'a'
    assert attachment.adopted_service(event(1), active) == 'a'
    assert attachment.adopted_service(event(1), active) is None
    assert active == set()


def test_follower_only_changes_attachment_for_adopted_routes(tmp_path, monkeypatch):
    import json

    attachment = Attachment.__new__(Attachment)
    attachment.output = tmp_path
    attachment.stopped = False
    attachment.config = {}
    attachment.current = None
    attachment.log = io.StringIO()
    attachment.change_positions = {}
    attachment.changes = {'2026-01-01T00:00:00+00:00|a': [True, False]}
    attachment.attach = Mock()
    events = [{'kind': 'clock', 'origin': 100},
              {'kind': 'link', 'activated': 100.1},
              *[{'kind': 'route', 'activated': time, 'adoptions': [{
                  'scheduled': '2026-01-01T00:00:00+00:00', 'nodes': ['a'],
                  'instruction_counts': {'a': 1}}]} for time in (101, 102)],
              {'kind': 'stopped'}]
    (tmp_path / 'execution.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events))
    launch = Mock(side_effect=AssertionError('attachment must not start application or capture processes'))
    monkeypatch.setattr('examples.s4_handover.experiment.ut.attachment.subprocess.Popen', launch)
    attachment.follow()
    assert [(call.args, call.kwargs) for call in attachment.attach.call_args_list] == [
        (('a',), {'activated': 101}), ((None,), {'activated': 102})]
    assert attachment.stopped
    launch.assert_not_called()


def test_follower_waits_for_complete_receipt(tmp_path, monkeypatch):
    import json

    attachment = Attachment.__new__(Attachment)
    attachment.output = tmp_path
    attachment.stopped = False
    attachment.current = None
    attachment.log = io.StringIO()
    attachment.config = {}
    attachment.change_positions = {}
    attachment.changes = {'2026-01-01T00:00:00+00:00|a': [True]}
    attachment.attach = Mock()
    event = {'kind': 'route', 'activated': 101, 'adoptions': [{
        'scheduled': '2026-01-01T00:00:00+00:00', 'nodes': ['a'], 'instruction_counts': {'a': 1}}]}
    receipt = json.dumps(event) + '\n'
    journal = tmp_path / 'execution.jsonl'
    journal.write_text(receipt[:32])

    def finish_write(_):
        attachment.attach.assert_not_called()
        with journal.open('a') as stream:
            stream.write(receipt[32:] + '{"kind":"stopped"}\n')

    monkeypatch.setattr('examples.s4_handover.experiment.ut.attachment.time.sleep', finish_write)
    attachment.follow()
    attachment.attach.assert_called_once_with('a', activated=101)
    assert attachment.stopped
