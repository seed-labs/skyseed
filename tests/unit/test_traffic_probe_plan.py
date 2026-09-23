"""Fixed selection and sender ownership replace route-derived workload planning."""
from types import SimpleNamespace
from threading import Event
from datetime import datetime, timezone
import pytest
from examples.Sky00_tiny_network import tiny_network as tiny
from tools.generation.traffic.workload import select_workload, write_workload, read_workload
from tools.generation.traffic.probe_execution import TrafficExecutor


def test_static_selection_deterministic_explicit_and_route_independent(tmp_path):
    simulator=tiny.create_simulator(tiny.build_emulator())
    world=simulator.get_physical_world()
    try:
        workload=select_workload(world)
        assert workload==select_workload(world)
        c=workload.communications[0]
        explicit=select_workload(world,pairs=[(c.ground_station_id,c.zone_id)],direction='ground-to-zone',interval_seconds=5,duration_seconds=10)
        assert explicit.communications==(c,)
        assert read_workload(write_workload(explicit,tmp_path/'workload.json'))==explicit
        assert not hasattr(explicit,'episodes')
        with pytest.raises(ValueError):select_workload(world,pairs=[('unknown',c.zone_id)])
    finally:simulator.shutdown()


def test_persistent_source_pause_migration_absence_and_cleanup(tmp_path,monkeypatch):
    simulator=tiny.create_simulator(tiny.build_emulator())
    workload=select_workload(simulator.get_physical_world(),direction='zone-to-ground')
    simulator.shutdown();communication=workload.communications[0]
    executor=TrafficExecutor(workload=workload,evidence_path=tmp_path/'events.jsonl')
    current={'source':'a','cancel':False};launched=[];stopped=[]
    class Runner:
        def status(self):return {'paused':True,'cancel_requested':current['cancel']}
        def communication_endpoints(self,*args):
            return {'available':True,'active_frontier':'2026-01-01T00:00:00+00:00',
                'ground':{'node_id':'g'},'service':None if current['source'] is None else {'node_id':current['source']}}
    executor._runner=Runner()
    def launch(c,facts,source):
        launched.append(source['node_id']);return SimpleNamespace(binding=SimpleNamespace(node_id=source['node_id']))
    def stop(sender,reason):
        stopped.append(sender.binding.node_id);executor._active.pop(communication.communication_id,None)
        if current['source']=='b':current['source']='c'  # activation advanced while draining
    monkeypatch.setattr(executor,'_launch_generator',launch);monkeypatch.setattr(executor,'_stop_generator',stop)
    executor._reconcile(communication);executor._reconcile(communication)
    assert launched==['a'] and not stopped  # topology pause leaves sender running
    current['source']='b';executor._reconcile(communication)
    assert launched==['a','c'] and stopped==['a']  # never launches stale b
    current['source']=None;executor._reconcile(communication)
    assert not executor._active and stopped==['a','c']
    current['source']='d';executor._reconcile(communication)
    assert launched==['a','c','d']
    executor._stop.set();executor._reconcile(communication)
    assert launched==['a','c','d']
    executor._selector.close()
