"""Small real scenes, durable controls, reusable continuation and late rebasing."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from threading import Thread, Event, current_thread
from time import monotonic, sleep
import pytest
from examples.Sky00_tiny_network import tiny_network as tiny
from skyseed.experiment import ExperimentController, InteractionClient
from skyseed.experiment.materials import _MaterialCatalog, _verify_initial_reference
from skyseed.experiment.output.history import _reading_completed_range
from skyseed.backend.execution_materials.codec import _read_delivery_contract, _open_runner_artifact
from skyseed.backend.runner import Runner
from skyseed.simulator import OperationalConnection, EffectiveConnectionParameters, ParameterPlanningResult
from tools.observation.planner.reader import read_planner_history


class FeedbackParameters:
    def __init__(self, output):self.output,self.calls=output,[]
    def plan_effective_parameters(self, timestamp, observations, previous_memory):
        client=InteractionClient.from_output(self.output)
        row=client.user_get('delay')
        value=0 if row is None else row['value']
        # These independent reads and author state are deliberately not a framework snapshot.
        second=client.user_get('private')
        self.calls.append((timestamp,previous_memory,value,second))
        client.close()
        if value=='fail':raise ValueError('author candidate failure')
        memory=(previous_memory or 0)+1
        return ParameterPlanningResult(tuple(OperationalConnection(v.connection_id,
            EffectiveConnectionParameters(1_000_000,100+int(value)+memory,0),
            EffectiveConnectionParameters(1_000_000,100+int(value)+memory,0)) for v in observations),memory)


@contextmanager
def retained(output, backend, *, duration=80, delivery=20, acquisition=0):
    network=tiny.build_emulator(); network._skyseed_forwarding_backend=backend
    from unittest.mock import patch
    create = tiny.sky.simulator.create_default_simulator
    def configured(*args, **kwargs):
        kwargs['planner_configuration'] = replace(kwargs['planner_configuration'],
            inter_satellite_acquisition_duration=timedelta(seconds=acquisition),
            ground_station_acquisition_duration=timedelta(seconds=acquisition))
        return create(*args, **kwargs)
    with patch.object(tiny.sky.simulator, 'create_default_simulator', side_effect=configured):
        simulator=tiny.create_simulator(network,duration=timedelta(seconds=duration))
    model=FeedbackParameters(output);simulator.get_planner()._parameter_model=model
    controller=ExperimentController(network,simulator,output,entry_path=Path(tiny.__file__),
        input_files=(tiny.ZONE_LAYOUT_FILE,),traffic_enabled=False,internet_map_enabled=False)
    errors=[]
    def run():
        try:controller.serve(delivery_duration=timedelta(seconds=delivery))
        except BaseException as error:errors.append(error)
    thread=Thread(target=run);thread.start()
    try:
        end=monotonic()+25
        while not errors and controller.status()['state']!='IDLE':
            assert monotonic()<end
            sleep(.02)
        assert not errors
        client=controller.interaction
        while not client.state('computation')['available']:sleep(.02)
        yield controller,model,client
    finally:
        controller._cancel_computation();thread.join()
    assert not errors


@pytest.mark.parametrize('backend',['bpf','linux'])
def test_candidate_failure_rebase_selected_history_and_extension(tmp_path,monkeypatch,backend):
    output=tmp_path/'scene'
    with retained(output,backend) as (controller,model,client):
        original=_MaterialCatalog(output).selected()
        original_publication=json.loads((output/'results/experiment/completed.json').read_text())
        digests={r['manifest']:sha256((output/r['manifest']).read_bytes()).hexdigest() for r in original}
        calls_before=len(model.calls)
        client.user_put('delay','fail');client.user_put('private',{'never':'rollback'})
        assert len(model.calls)==calls_before  # A user write alone triggers nothing.
        failed=client.wait(client.submit('computation','revise'),timeout=20)
        assert failed['status']=='FAILED' and 'author candidate failure' in failed['result']['error']
        assert _MaterialCatalog(output).selected()==original
        client.user_put('delay',900)
        from skyseed.experiment import revision
        attempts=[]
        runner=Runner(compose_project_name='offline-race',runtime_artifact=output/original[0]['manifest'])
        runner.append_batch(output/original[1]['manifest'])
        # Represent the actual preparation decision under the real Runner lock;
        # no Docker/kernel operations are performed by this offline race fixture.
        def adopt(root,candidates,expected, **kwargs):
            attempts.append(candidates)
            if len(attempts) == 1:
                # Drive the real PREPARING decision, then hold slow artifact I/O.
                from types import SimpleNamespace
                from skyseed.backend.runner import lifecycle
                entered, release = Event(), Event()
                errors = []
                runner._runtime_realization = SimpleNamespace(admit_boundary_count=lambda count: None)
                def blocked(path):
                    entered.set()
                    assert release.wait(10)
                    raise ValueError('fixture stops before any kernel work')
                def prepare():
                    try:
                        runner._load_batch(runner._pending_batches[0], 0, 0)
                    except ValueError as error:
                        errors.append(error)
                with monkeypatch.context() as scope:
                    scope.setattr(lifecycle, '_open_runner_artifact', blocked)
                    thread = Thread(target=prepare); thread.start()
                    try:
                        assert entered.wait(10)
                        result = _MaterialCatalog(root).adopt(candidates,expected,runner=runner)
                    finally:
                        release.set(); thread.join()
                    assert errors and runner._preparing_batch == 1
                    return result
            return _MaterialCatalog(root).adopt(candidates,expected,runner=runner)
        monkeypatch.setattr(revision,'_adopt_revision',adopt)
        result=client.wait(client.submit('computation','revise', at=original[1]['timeline']['start']),timeout=20)
        assert result['status']=='SUCCEEDED',result
        assert len(attempts)==2
        assert result['result']['attempts'][0]['reason']=='preparation won selection'
        second=attempts[1][0]
        previous=_read_delivery_contract(output/original[1]['manifest'])
        candidate=_read_delivery_contract(output/second['manifest'])
        assert candidate['delivery']['previous_manifest_sha256']==previous['manifest_sha256']
        assert candidate['delivery']['prior_state_fingerprint']==previous['last_state']
        # B2 calculated after the losing candidate B1 was recomputed, not renamed.
        losing=_read_delivery_contract(output/attempts[0][1]['manifest'])
        assert losing['delivery']['prior_state_fingerprint']!=candidate['delivery']['prior_state_fingerprint']
        assert client.user_get('private')['value']=={'never':'rollback'}
        assert len(model.calls)>calls_before
        publication=json.loads((output/'results/experiment/completed.json').read_text())
        with _reading_completed_range(output,publication):
            evidence=read_planner_history(output/'results/planner/planning.jsonl.gz')
            assert evidence
        extension=client.wait(controller.continue_calculation(additional_duration=timedelta(seconds=20)),timeout=20)
        assert extension['status']=='SUCCEEDED'
        selected=_MaterialCatalog(output).selected()
        for before,after in zip(selected,selected[1:]):
            a=_read_delivery_contract(output/before['manifest']);b=_read_delivery_contract(output/after['manifest'])
            assert b['delivery']['previous_manifest_sha256']==a['manifest_sha256']
        assert all(sha256((output/path).read_bytes()).hexdigest()==digest for path,digest in digests.items())
        latest=json.loads((output/'results/experiment/completed.json').read_text())
        with _reading_completed_range(output,latest):
            assert read_planner_history(output/'results/planner/planning.jsonl.gz')

        # Inspect selected physical input, never the superseded candidate stream.
        from tools.observation._inspection_evidence import iter_physical_link_inspection
        with _reading_completed_range(output,latest):
            physical = list(iter_physical_link_inspection(output / 'results/physical-world/candidates/physical-link-observations.jsonl.gz'))
        assert len(physical[1:]) == latest['history']['counts']['formal_frames']
        assert len({r['timestamp'] for r in physical[1:]}) == len(physical)-1
        with _reading_completed_range(output, original_publication):
            before = list(iter_physical_link_inspection(output / 'results/physical-world/candidates/physical-link-observations.jsonl.gz'))
        assert len(before)-1 == original_publication['history']['counts']['formal_frames']
        _prepare_selected_chain(output, selected, backend, monkeypatch)


def _prepare_selected_chain(output, selected, backend, monkeypatch):
    """Real adapters validate revised continuation against offline kernel fixtures."""
    from types import SimpleNamespace
    artifacts = [_open_runner_artifact(output / row['manifest']) for row in selected]
    first = artifacts[0]
    if backend == 'bpf':
        from tests.unit.test_runner_runtime_optimization import _Kernel
        from skyseed.backend.bpf.dataplane.runtime import _TcBpfRuntimeRealization
        kernel = _Kernel(monkeypatch)
        bindings = {n: SimpleNamespace(pid=i+100, process_start_time_ticks=i+200, netns_inode=i+300)
                    for i,n in enumerate(first.physical_world_projection.node_ids)}
        runtime = _TcBpfRuntimeRealization(executor=kernel, pin_root='/pins', bindings=bindings,
            continuation_enabled=True, batch_capacity_factor=8)
    else:
        from tests.unit.test_linux_routes import RouteKernel
        from skyseed.backend.linux.runtime import _LinuxRuntime
        kernel = RouteKernel(monkeypatch, {n.value: n for n in first.physical_world_projection.node_ids})
        runtime = _LinuxRuntime(executor=kernel, bindings=kernel.bindings)
    try:
        runtime.initialize(first.physical_world_projection, zone_prefixes=first.zone_prefixes,
            runtime_capacity_demand=first.runtime_capacity_demand, dataplane=first.dataplane)
        times = []
        for index, artifact in enumerate(artifacts):
            plans = runtime.prepare_all(artifact.boundaries(), logical_ports=artifact.logical_ports,
                retirements=artifact.retirements,
                **({'runtime_capacity_demand': artifact.runtime_capacity_demand} if index else {}))
            for plan in plans:
                runtime.activate(plan)
                times.append(plan.nominal_time)
                if backend == 'bpf':
                    while runtime.reclaim().get('work_units'):
                        pass
        assert len(times) == len(set(times))
        assert times == sorted(times)
    finally:
        runtime.cleanup()
        for artifact in artifacts:
            artifact.close()


def _adopting_runtime_process(output, ready, stop):
    from skyseed.experiment._control import _serve_control
    from skyseed.experiment.output.occupancy import claim_runtime
    catalog = _MaterialCatalog(output)
    runner = Runner(compose_project_name='offline-runtime-authority', runtime_artifact=output / catalog.get(0)['manifest'])
    runner.append_batch(output / catalog.get(1)['manifest'])
    def dispatch(request):
        if request['command'] == 'adopt':
            from skyseed.experiment.materials import _read_adoption_proposal
            candidates, expected = _read_adoption_proposal(output, request)
            return catalog.adopt(candidates, expected, runner=runner)
        if request['command'] == 'cancel-runtime':
            stop.set()
        elif request['command'] != 'status':
            raise ValueError('unsupported fixture request')
        return {'invocation_id': 'offline-adoption', 'runner': runner.status(),
                'cancel_requested': stop.is_set(),
                'pending_manifest': str(runner._pending_batches[0]['path'])}
    with claim_runtime(output), _serve_control(output, 'runtime', dispatch):
        ready.set()
        stop.wait()


def test_revision_adoption_handoff_to_independent_runtime_process(tmp_path):
    from multiprocessing import get_context
    context = get_context('spawn')
    output = tmp_path / 'handoff'
    with retained(output, 'bpf') as (controller, model, client):
        ready, stop = context.Event(), context.Event()
        runtime = context.Process(target=_adopting_runtime_process, args=(output, ready, stop))
        runtime.start()
        try:
            assert ready.wait(15)
            client.user_put('delay', 700)
            result = client.wait(client.submit('computation', 'revise'), timeout=25)
            assert result['status'] == 'SUCCEEDED', result
            selected = _MaterialCatalog(output).get(1)
            end = monotonic()+10
            while client.state('runtime')['pending_manifest'] != str(output / selected['manifest']):
                assert monotonic() < end
                sleep(.05)
            assert selected['version'] != 'original'
            # The accepted request belongs to the independent runtime session.
            requests = client._read('requests/runtime/', prefix=True)
            assert any(row['value']['intent']['command']=='adopt' and row['value']['status']=='SUCCEEDED' for row in requests)
            arguments = next(row['value']['intent']['arguments'] for row in requests
                             if row['value']['intent']['command'] == 'adopt')
            assert set(arguments) == {'proposal', 'proposal_sha256'}
        finally:
            stop.set(); runtime.join(15)
        assert not runtime.is_alive() and runtime.exitcode == 0


def test_parameter_changes_apply_to_future_deliveries_and_new_resources(tmp_path):
    from skyseed.experiment.interaction import _resource_parameters
    output = tmp_path / 'parameters'
    with retained(output, 'bpf') as (controller, model, client):
        original = _MaterialCatalog(output).selected()
        values = {'delivery_duration_seconds': 10, 'route_workers': 1,
                  'runtime_workers': 2, 'deploy_parallel': 2, 'batch_capacity_factor': 5}
        result = client.wait(client.submit('computation', 'parameters', values=values), timeout=10)
        assert result['status'] == 'SUCCEEDED', result
        assert result['result']['effective']['delivery_duration_seconds'] == 'next-delivery'
        assert result['result']['effective']['runtime_workers'] == 'next-created-resources'
        assert _resource_parameters(output) == values
        invalid = client.wait(client.submit('computation', 'parameters',
            values={'delivery_duration_seconds': 20, 'runtime_workers': -1}), timeout=10)
        assert invalid['status'] == 'REJECTED'
        assert _resource_parameters(output) == values
        assert client.wait(controller.continue_calculation(additional_duration=timedelta(seconds=20)), timeout=20)['status'] == 'SUCCEEDED'
        selected = _MaterialCatalog(output).selected()
        assert selected[:len(original)] == original
        assert len(selected) == len(original) + 2
        contracts = [_read_delivery_contract(output / row['manifest']) for row in selected[-2:]]
        assert all(c['delivery']['observation_interval_seconds'] == 10 for c in contracts)
        assert contracts[1]['delivery']['previous_manifest_sha256'] == contracts[0]['manifest_sha256']


def test_evidence_cleanup_failure_after_adoption_retains_compatible_head(tmp_path, monkeypatch):
    from skyseed.experiment.output.physical_links import _PhysicalLinkInspectionWriter
    original_close = _PhysicalLinkInspectionWriter.close
    failed = []
    def close(self):
        original_close(self)
        if 'revisions' in self.path.parts and not failed:
            failed.append(True)
            raise OSError('fixture evidence close failed after selection')
    monkeypatch.setattr(_PhysicalLinkInspectionWriter, 'close', close)
    output = tmp_path / 'selected-cleanup'
    with retained(output, 'bpf') as (controller, model, client):
        client.user_put('delay', 800)
        result = client.wait(client.submit('computation', 'revise'), timeout=20)
        assert result['status'] == 'FAILED' and 'after selection' in result['result']['error']
        assert result['result']['adopted'] is True
        selected = _MaterialCatalog(output).selected()
        assert selected[-1]['version'] != 'original'
        assert client.wait(controller.continue_calculation(additional_duration=timedelta(seconds=20)), timeout=20)['status'] == 'SUCCEEDED'
        final = _MaterialCatalog(output).selected()
        before = _read_delivery_contract(output / selected[-1]['manifest'])
        after = _read_delivery_contract(output / final[-1]['manifest'])
        assert after['delivery']['previous_manifest_sha256'] == before['manifest_sha256']


@pytest.mark.parametrize('backend', ['bpf', 'linux'])
@pytest.mark.parametrize('duration,delivery', [(10, 60), (40, 20)])
def test_revision_replaces_initial_delivery_and_repeats_framework_inputs(tmp_path, monkeypatch, backend, duration, delivery):
    from skyseed.experiment.execution import _read_and_verify_execution
    output = tmp_path / 'initial'
    with retained(output, backend, duration=duration, delivery=delivery, acquisition=20) as (controller, model, client):
        original = _MaterialCatalog(output).selected()
        original_calls = list(model.calls)
        original_seals = {r['manifest']: sha256((output / r['manifest']).read_bytes()).hexdigest() for r in original}
        for delay in (400, 800):
            calls_before = len(model.calls)
            client.user_put('delay', delay)
            result = client.wait(client.submit('computation', 'revise'), timeout=20)
            assert result['status'] == 'SUCCEEDED', result
            assert result['result']['selection']['first_index'] == 0
            selected = _MaterialCatalog(output).selected()
            assert len(selected) == len(original)
            assert all(r['version'] == result['result']['selection']['version'] for r in selected)
            # Recompute the same initial acquisition/parameter inputs, while the
            # author's instance and accumulated private calls remain live.
            replay = model.calls[calls_before:]
            assert [(r[0], r[1]) for r in replay] == [(r[0], r[1]) for r in original_calls]
            assert all(r[2] == delay for r in replay)
            definition, _, _ = _read_and_verify_execution(output)
            _verify_initial_reference(definition, output / selected[0]['manifest'])
            from copy import deepcopy
            for field in ('backend', 'scene', 'timeline', 'cadence'):
                incompatible = deepcopy(definition)
                if field == 'backend': incompatible['backend'] = 'linux' if backend == 'bpf' else 'bpf'
                elif field == 'scene': incompatible['runner_artifact']['physical_world_fingerprint'] = 'different-scene'
                elif field == 'timeline': incompatible['runner_artifact']['timeline']['end'] = '2027-01-01T00:00:00+00:00'
                else: incompatible['simulation']['observation_interval_seconds'] += 1
                with pytest.raises(ValueError, match='generated execution inputs'):
                    _verify_initial_reference(incompatible, output / selected[0]['manifest'])
        assert all(sha256((output / p).read_bytes()).hexdigest() == seal for p, seal in original_seals.items())
        _prepare_selected_chain(output, selected, backend, monkeypatch)
        assert client.wait(controller.continue_calculation(additional_duration=timedelta(seconds=20)), timeout=20)['status'] == 'SUCCEEDED'
        final = _MaterialCatalog(output).selected()
        before = _read_delivery_contract(output / selected[-1]['manifest'])
        after = _read_delivery_contract(output / final[len(selected)]['manifest'])
        assert after['delivery']['previous_manifest_sha256'] == before['manifest_sha256']


@pytest.mark.parametrize('backend', ['bpf', 'linux'])
@pytest.mark.parametrize('duration,delivery', [(10, 60), (40, 20)])
def test_initial_preparation_wins_revision_rebases_or_rejects(tmp_path, monkeypatch, backend, duration, delivery):
    from skyseed.experiment import revision
    from skyseed.backend.runner import lifecycle
    output = tmp_path / 'freeze-initial'
    with retained(output, backend, duration=duration, delivery=delivery) as (controller, model, client):
        catalog = _MaterialCatalog(output)
        original = catalog.selected()
        runner = Runner(compose_project_name='offline-initial-freeze', runtime_artifact=output / original[0]['manifest'])
        if len(original) > 1:
            runner.append_batch(output / original[1]['manifest'])
        assert runner.status()['frozen_through'] == -1
        entered, release = Event(), Event()
        errors, attempts, loaded = [], [], []
        def blocked(path):
            loaded.append(path)
            entered.set()
            assert release.wait(20)
            raise ValueError('fixture stops before kernel work')
        monkeypatch.setattr(lifecycle, '_open_runner_artifact', blocked)
        def prepare():
            try: runner.prepare()
            except ValueError as error: errors.append(error)
        thread = Thread(target=prepare)
        def adopt(root, candidates, expected, **kwargs):
            attempts.append(candidates)
            if len(attempts) == 1:
                thread.start()
                assert entered.wait(20)
                assert runner.status()['frozen_through'] == 0
            return catalog.adopt(candidates, expected, runner=runner)
        monkeypatch.setattr(revision, '_adopt_revision', adopt)
        client.user_put('delay', 750)
        try:
            result = client.wait(client.submit('computation', 'revise'), timeout=20)
            assert attempts[0][0]['index'] == 0
            assert loaded == [output / original[0]['manifest']]
            assert catalog.get(0) == original[0]
            if len(original) == 1:
                assert result['status'] == 'REJECTED', result
                assert result['result']['attempts'][0]['next_index'] == 1
            else:
                assert result['status'] == 'SUCCEEDED', result
                assert attempts[1][0]['index'] == 1
                contract = _read_delivery_contract(output / catalog.get(1)['manifest'])
                assert contract['delivery']['previous_manifest_sha256'] == _read_delivery_contract(loaded[0])['manifest_sha256']
                assert runner._pending_batches[0]['path'] == output / catalog.get(1)['manifest']
        finally:
            release.set()
            if thread.ident is not None: thread.join()
            runner.cleanup()
        assert len(errors) == 1


@pytest.mark.parametrize('backend', ['bpf', 'linux'])
@pytest.mark.parametrize('winner', ['offline', 'runtime'])
def test_runtime_claim_and_first_reader_observe_authoritative_selection(tmp_path, monkeypatch, backend, winner):
    from skyseed.experiment import execution, revision
    from skyseed.backend.runner import lifecycle
    from skyseed.experiment.output.occupancy import claim_runtime
    output = tmp_path / 'startup-selection'
    with retained(output, backend, duration=20) as (controller, model, client):
        original = _MaterialCatalog(output).selected()
        proposal_ready, allow_adoption = Event(), Event()
        claim_boundary, allow_claim = Event(), Event()
        before_prepare, allow_prepare = Event(), Event()
        loaded, runners, errors = [], [], []
        real_adopt, real_prepare = revision._adopt_revision, Runner.prepare
        real_open = lifecycle._open_runner_artifact
        def adopt(*args, **kwargs):
            proposal_ready.set()
            assert allow_adoption.wait(20)
            return real_adopt(*args, **kwargs)
        @contextmanager
        def gated_claim(path):
            if winner == 'offline':
                claim_boundary.set()
                assert allow_claim.wait(20)
            with claim_runtime(path):
                if winner == 'runtime':
                    claim_boundary.set()
                    assert allow_claim.wait(20)
                yield
        def prepare(runner):
            runners.append(runner)
            before_prepare.set()
            assert allow_prepare.wait(20)
            return real_prepare(runner)
        def open_artifact(path):
            loaded.append(path)
            return real_open(path)
        def stop_before_docker(*args):
            raise RuntimeError('offline fixture stops before Docker discovery')
        monkeypatch.setattr(revision, '_adopt_revision', adopt)
        monkeypatch.setattr(execution, 'claim_runtime', gated_claim)
        monkeypatch.setattr(Runner, 'prepare', prepare)
        monkeypatch.setattr(lifecycle, '_open_runner_artifact', open_artifact)
        monkeypatch.setattr(lifecycle._Deployment, 'discover', stop_before_docker)
        monkeypatch.setattr(execution, '_record_container_checkpoint', lambda *args: None)
        def run():
            try: execution._run_generated_bpf_experiment(output)
            except BaseException as error: errors.append(error)
        thread = Thread(target=run)
        client.user_put('delay', 640)
        receipt = client.submit('computation', 'revise')
        try:
            assert proposal_ready.wait(20)
            thread.start()
            assert claim_boundary.wait(20)
            if winner == 'offline':
                allow_adoption.set()
                result = client.wait(receipt, timeout=20)
                assert result['status'] == 'SUCCEEDED', result
                assert json.loads((output / 'results/compiler/selection.json').read_text())['authority'] == 'computation'
                allow_claim.set()
                assert before_prepare.wait(20)
            else:
                allow_adoption.set()
                # Runtime owns the claim before publishing its session. Offline
                # selection is impossible throughout this gap.
                with pytest.raises(RuntimeError, match='runtime is active'):
                    with claim_runtime(output): pass
                assert not (output / 'results/compiler/selection.json').exists()
                allow_claim.set()
                assert before_prepare.wait(20)
                result = client.wait(receipt, timeout=20)
                assert result['status'] == 'SUCCEEDED', result
                assert json.loads((output / 'results/compiler/selection.json').read_text())['authority'] == 'runtime'
            selected = _MaterialCatalog(output).get(0)
            assert selected['manifest'] != original[0]['manifest']
            assert runners[0]._artifact_path == output / selected['manifest']
            assert runners[0].status()['frozen_through'] == -1
            definition, path, snapshot = execution._read_and_verify_execution(output)
            assert path == output / selected['manifest']
            assert snapshot['manifest_sha256'] == _read_delivery_contract(path)['manifest_sha256']
        finally:
            allow_adoption.set(); allow_claim.set(); allow_prepare.set()
            if thread.ident is not None: thread.join()
        assert loaded == [output / selected['manifest']]
        assert len(errors) == 1 and str(errors[0]) == 'offline fixture stops before Docker discovery'
        assert runners[0]._artifact is None  # Reader/resources closed by actual cleanup.


@pytest.mark.parametrize('cancelled', [True, False])
def test_adoption_pre_submission_outage_can_cancel_or_recover(tmp_path, monkeypatch, cancelled):
    from skyseed.experiment import revision
    from skyseed.experiment.interaction import InteractionUnavailable, _OwnerPublisher
    from skyseed.experiment.output.physical_links import _PhysicalLinkInspectionWriter
    output = tmp_path / 'adoption-outage'
    outage, entered, release = Event(), Event(), Event()
    closed = []
    real_call, real_adopt = InteractionClient._call, revision._adopt_revision
    real_close = _PhysicalLinkInspectionWriter.close
    def call(self, *args, **kwargs):
        if outage.is_set():
            entered.set()
            assert release.wait(20)
            raise InteractionUnavailable('fixture pre-submission store outage')
        return real_call(self, *args, **kwargs)
    def adopt(*args, **kwargs):
        outage.set()
        return real_adopt(*args, **kwargs)
    def close(self):
        real_close(self)
        closed.append(self.path)
    with retained(output, 'bpf', duration=10) as (controller, model, client):
        original = _MaterialCatalog(output).selected()
        client.user_put('delay', 350)
        monkeypatch.setattr(InteractionClient, '_call', call)
        monkeypatch.setattr(revision, '_adopt_revision', adopt)
        monkeypatch.setattr(_PhysicalLinkInspectionWriter, 'close', close)
        receipt = client.submit('computation', 'revise')
        try:
            assert entered.wait(20)
            if cancelled:
                controller._cancel_computation()
                release.set()
                deadline = monotonic() + 20
                while not controller._serve_finished:
                    assert monotonic() < deadline, 'local calculation must close without store recovery'
                    sleep(.01)
                assert outage.is_set()
                assert controller._producer._closed
                assert any(r['status'] == 'CANCELLED' for r in controller._producer._revisions.values())
                assert _MaterialCatalog(output).selected() == original
                assert not (output / '.control/revision-proposals').exists()
            else:
                outage.clear(); release.set()
                assert client.wait(receipt, timeout=20)['status'] == 'SUCCEEDED'
                assert _MaterialCatalog(output).get(0)['version'] != 'original'
            assert any('revisions' in p.parts for p in closed)
        finally:
            outage.clear(); release.set()
            if cancelled and controller._serve_finished:
                # Test-only reconciliation permits scoped service cleanup after
                # proving local closure while the durable owner state was stale.
                _OwnerPublisher(client, 'computation').publish(controller.status(), live=False)


def test_lost_adoption_ack_and_local_cancel_wait_for_authoritative_result(tmp_path, monkeypatch):
    from skyseed.experiment._control import _serve_control
    from skyseed.experiment.output.occupancy import claim_runtime
    from skyseed.experiment.materials import _read_adoption_proposal
    from skyseed.experiment.interaction import InteractionUnavailable, _OwnerPublisher
    output = tmp_path / 'adoption-lost-ack'
    with retained(output, 'bpf', duration=10) as (controller, model, client):
        catalog = _MaterialCatalog(output)
        runner = Runner(compose_project_name='offline-lost-ack', runtime_artifact=output / catalog.get(0)['manifest'])
        pending_entered, allow_decision, lost = Event(), Event(), Event()
        real_pending, real_submit = _OwnerPublisher.pending, InteractionClient.submit
        effects = []
        def pending(self):
            if self.owner == 'runtime' and not pending_entered.is_set():
                pending_entered.set()
                assert allow_decision.wait(20)
            return real_pending(self)
        def submit(self, owner, command, **kwargs):
            receipt = real_submit(self, owner, command, **kwargs)
            if command == 'adopt' and not lost.is_set():
                controller._cancel_computation()
                lost.set()
                raise InteractionUnavailable('fixture loses accepted adoption acknowledgement')
            return receipt
        def dispatch(request):
            if request['command'] == 'adopt':
                candidates, expected = _read_adoption_proposal(output, request)
                effects.append(request['request_id'])
                return catalog.adopt(candidates, expected, runner=runner)
            return {'invocation_id': 'lost-ack', 'runner': runner.status()}
        monkeypatch.setattr(_OwnerPublisher, 'pending', pending)
        monkeypatch.setattr(InteractionClient, 'submit', submit)
        with claim_runtime(output), _serve_control(output, 'runtime', dispatch):
            try:
                assert pending_entered.wait(20)
                client.user_put('delay', 375)
                client.submit('computation', 'revise')
                assert lost.wait(20)
                assert controller._producer._stop.is_set()
                assert not controller._serve_finished
                assert catalog.get(0)['version'] == 'original'
            finally:
                allow_decision.set()
            deadline = monotonic() + 20
            while not controller._serve_finished:
                assert monotonic() < deadline
                sleep(.01)
            selected = catalog.get(0)
            assert selected['version'] != 'original'
            assert len(effects) == 1
            checkpoint = controller._producer._writer_checkpoint.restore()
            assert checkpoint._sealed_metadata['manifest_sha256'] == _read_delivery_contract(output / selected['manifest'])['manifest_sha256']
            assert runner._artifact_path == output / selected['manifest']
        runner.cleanup()


@pytest.mark.parametrize('fault,adoption_error', [('record', False), ('publish', True), ('open', False)])
def test_candidate_evidence_failure_does_not_poison_normal_continuation(tmp_path, monkeypatch, fault, adoption_error):
    from skyseed.experiment import revision
    from skyseed.experiment.output.physical_links import _PhysicalLinkInspectionWriter
    from tools.observation._inspection_evidence import iter_physical_link_inspection
    output = tmp_path / 'isolated-evidence'
    with retained(output, 'linux', duration=20, delivery=10) as (controller, model, client):
        original = _MaterialCatalog(output).selected()
        method = '__init__' if fault == 'open' else fault
        real = getattr(_PhysicalLinkInspectionWriter, method)
        def fail(self, *args, **kwargs):
            path = Path(args[0]) if fault == 'open' else self.path
            if 'revisions' in path.parts:
                raise OSError('candidate-only evidence failure')
            return real(self, *args, **kwargs)
        def reject(*args, **kwargs):
            if adoption_error:
                raise ValueError('candidate rejected by validation')
            return {'adopted': False, 'reason': 'fixture rejects candidate', 'next_index': None}
        monkeypatch.setattr(_PhysicalLinkInspectionWriter, method, fail)
        monkeypatch.setattr(revision, '_adopt_revision', reject)
        result = client.wait(client.submit('computation', 'revise'), timeout=20)
        assert result['status'] == ('FAILED' if adoption_error else 'REJECTED'), result
        assert 'candidate-only' in result['result']['evidence_attempts'][0]['diagnostics']['failure']
        assert _MaterialCatalog(output).selected() == original
        assert controller._producer.snapshot()['formal_observation_sink_failure'] is None
        assert client.wait(controller.continue_calculation(additional_duration=timedelta(seconds=10)), timeout=20)['status'] == 'SUCCEEDED'
        publication = json.loads((output / 'results/experiment/completed.json').read_text())
        assert publication['physical_links'] is not None
        with _reading_completed_range(output, publication):
            records = list(iter_physical_link_inspection(output / 'results/physical-world/candidates/physical-link-observations.jsonl.gz'))
        assert len(records) - 1 == publication['history']['counts']['formal_frames']


def test_candidate_success_does_not_erase_original_evidence_failure(tmp_path, monkeypatch):
    from skyseed.experiment import revision
    from skyseed.experiment.output.physical_links import _PhysicalLinkInspectionWriter
    real = _PhysicalLinkInspectionWriter.record
    def record(self, value):
        if 'revisions' not in self.path.parts:
            raise OSError('original stream failed')
        return real(self, value)
    monkeypatch.setattr(_PhysicalLinkInspectionWriter, 'record', record)
    monkeypatch.setattr(revision, '_adopt_revision', lambda *a, **kw:
        {'adopted': False, 'reason': 'fixture rejects', 'next_index': None})
    output = tmp_path / 'ordinary-failure'
    with retained(output, 'linux', duration=20, delivery=10) as (controller, model, client):
        before = controller._producer.snapshot()['formal_observation_sink_failure']
        result = client.wait(client.submit('computation', 'revise'), timeout=20)
        assert result['status'] == 'REJECTED'
        assert result['result']['evidence_attempts'][0]['diagnostics']['failure'] is None
        assert controller._producer.snapshot()['formal_observation_sink_failure'] == before
        assert 'original stream failed' in before


@pytest.mark.parametrize('prepare', [False, True])
def test_catalog_validation_allows_delivery_then_rechecks_preparation(tmp_path, monkeypatch, prepare):
    from skyseed.backend.execution_materials import codec
    from skyseed.backend.runner import lifecycle
    output = tmp_path / 'nonblocking-catalog'
    with retained(output, 'linux', duration=30, delivery=10):
        catalog = _MaterialCatalog(output)
        original = catalog.selected()
        runner = Runner(compose_project_name='offline-catalog-lock', runtime_artifact=output / original[0]['manifest'])
        runner._delivery_tail = _read_delivery_contract(runner._artifact_path)
        entered, release, delivered = Event(), Event(), Event()
        prepared, release_prepare = Event(), Event()
        outcomes, errors = [], []
        real = codec._read_delivery_contract
        candidate_thread = None
        def read(path):
            if current_thread() is candidate_thread and path == output / original[0]['manifest']:
                entered.set()
                assert release.wait(15)
            return real(path)
        def adopt():
            try:
                outcomes.append(catalog.adopt([dict(r, version='candidate') for r in original[1:]],
                    [r['manifest'] for r in original], runner=runner))
            except BaseException as error: errors.append(error)
        def deliver():
            try:
                assert catalog.get(0) == original[0]
                assert catalog.register(1, runner)
                delivered.set()
            except BaseException as error: errors.append(error)
        def blocked(path):
            prepared.set()
            assert release_prepare.wait(15)
            raise ValueError('offline fixture stops before kernel work')
        def preparation():
            try: runner._load_batch(runner._pending_batches[0], 0, 0)
            except ValueError: pass
            except BaseException as error: errors.append(error)
        from types import SimpleNamespace
        runner._runtime_realization = SimpleNamespace(admit_boundary_count=lambda count: None)
        monkeypatch.setattr(codec, '_read_delivery_contract', read)
        monkeypatch.setattr(lifecycle, '_open_runner_artifact', blocked)
        candidate_thread = Thread(target=adopt)
        delivery_thread, preparation_thread = Thread(target=deliver), Thread(target=preparation)
        candidate_thread.start()
        try:
            assert entered.wait(10)
            delivery_thread.start()
            assert delivered.wait(10), 'normal delivery must complete while candidate validation is held'
            if prepare:
                preparation_thread.start()
                assert prepared.wait(10)
            release.set(); candidate_thread.join()
            assert not errors
            assert outcomes[0]['adopted'] is not prepare
            if prepare:
                assert outcomes[0]['reason'] == 'preparation won selection'
                assert catalog.get(1) == original[1]
            else:
                assert catalog.get(1)['version'] == 'candidate'
        finally:
            release.set(); release_prepare.set()
            for thread in (candidate_thread, delivery_thread, preparation_thread):
                if thread.ident is not None: thread.join()


def test_registration_reads_outside_catalog_lock_and_rechecks_selection(tmp_path, monkeypatch):
    from skyseed.backend.execution_materials import codec
    output = tmp_path / 'registration-selection'
    with retained(output, 'linux', duration=20, delivery=10):
        catalog = _MaterialCatalog(output)
        original = catalog.selected()
        runner = Runner(compose_project_name='offline-registration', runtime_artifact=output / original[0]['manifest'])
        runner._delivery_tail = _read_delivery_contract(runner._artifact_path)
        entered, release = Event(), Event()
        results, errors = [], []
        real = codec._read_delivery_contract
        def read(path):
            if current_thread() is thread:
                entered.set()
                assert release.wait(15)
            return real(path)
        def register():
            try: results.append(catalog.register(1, runner))
            except BaseException as error: errors.append(error)
        thread = Thread(target=register)
        monkeypatch.setattr(codec, '_read_delivery_contract', read)
        thread.start()
        try:
            assert entered.wait(10)
            assert catalog.get(0) == original[0]
            assert catalog.adopt([dict(r, version='new') for r in original[1:]],
                [r['manifest'] for r in original], runner=runner)['adopted']
        finally:
            release.set(); thread.join()
        assert not errors and results == [False]
        assert not runner._pending_batches
        assert catalog.register(1, runner)


def test_checkpoint_pruning_uses_published_frontier_without_network_or_rebuilding(tmp_path, monkeypatch):
    output = tmp_path / 'pruning'
    with retained(output, 'linux', duration=30, delivery=10) as (controller, model, client):
        producer = controller._producer
        original = producer._window_checkpoints
        producer._prune_continuations()
        assert producer._window_checkpoints is original
        producer._observe_runtime({'available': False, 'runner': {'frozen_through': 2}})
        producer._prune_continuations()
        assert producer._window_checkpoints is original
        producer._observe_runtime({'available': True, 'runner': {'frozen_through': 1}})
        deadline = monotonic() + 5
        while producer._pruned_through < 1:
            assert monotonic() < deadline
            sleep(.01)
        assert min(producer._window_checkpoints) == 1
        pruned = producer._window_checkpoints
        producer._observe_runtime({'available': True, 'runner': {'frozen_through': 0}})
        producer._prune_continuations()
        assert producer._window_checkpoints is pruned


@pytest.mark.parametrize('transition,ack_status', [('reset', 'RUNNING'), ('reset', 'SUCCEEDED'),
                                                 ('closure', 'RUNNING'), ('closure', 'SUCCEEDED')])
def test_confirmed_adoption_survives_lost_ack_session_end_and_continues(tmp_path, monkeypatch, transition, ack_status):
    from skyseed.experiment import _control
    from skyseed.experiment.output.occupancy import claim_runtime
    from skyseed.experiment.materials import _read_adoption_proposal
    from skyseed.experiment.interaction import _OwnerPublisher, InteractionUnavailable
    output = tmp_path / 'adoption-session-end'
    with retained(output, 'bpf', duration=40) as (controller, model, client):
        catalog = _MaterialCatalog(output)
        runner = Runner(compose_project_name='offline-session-end', runtime_artifact=output / catalog.get(0)['manifest'])
        entered, allow, submitted, owner_stop, lost = (Event() for _ in range(5))
        invocation = {'id': 'first'}
        controls, effects, errors = [], [], []
        real_event, real_pending = _control.Event, _OwnerPublisher.pending
        real_update, real_submit = _OwnerPublisher.update, InteractionClient.submit
        def event():
            value = real_event(); controls.append(value); return value
        def pending(self):
            if self.owner == 'runtime' and not entered.is_set():
                entered.set(); assert allow.wait(20)
            return real_pending(self)
        def submit(self, owner, command, **kwargs):
            value = real_submit(self, owner, command, **kwargs)
            if command == 'adopt': submitted.set()
            return value
        def update(self, key, row, status, **facts):
            value = real_update(self, key, row, status, **facts)
            command = row['intent']['command']
            trigger = (command == ('reset' if transition == 'reset' else 'adopt') and
                       status == 'RUNNING' and 'result' in facts) if ack_status == 'RUNNING' else command == 'adopt' and status == 'SUCCEEDED'
            if self.owner == 'runtime' and trigger and not lost.is_set():
                lost.set()
                if transition == 'closure':
                    owner_stop.set(); assert controls[0].wait(20)
                raise InteractionUnavailable('committed result acknowledgement lost across ' + transition)
            return value
        def dispatch(request):
            if request['command'] == 'adopt':
                candidates, expected = _read_adoption_proposal(output, request)
                effects.append(request['request_id'])
                return catalog.adopt(candidates, expected, runner=runner)
            if request['command'] == 'reset': invocation['id'] = 'second'
            return {'invocation_id': invocation['id'], 'runner': runner.status(),
                    'outcome': {'status': 'completed'}}
        def runtime():
            try:
                with claim_runtime(output), _control._serve_control(output, 'runtime', dispatch):
                    owner_stop.wait()
            except BaseException as error: errors.append(error)
        monkeypatch.setattr(_control, 'Event', event)
        monkeypatch.setattr(_OwnerPublisher, 'pending', pending)
        monkeypatch.setattr(_OwnerPublisher, 'update', update)
        monkeypatch.setattr(InteractionClient, 'submit', submit)
        thread = Thread(target=runtime); thread.start()
        try:
            assert entered.wait(20)
            client.user_put('delay', 500)
            revision = client.submit('computation', 'revise')
            assert submitted.wait(20)
            unfinished = client.submit('runtime', 'resume', at='2027-01-01T00:00:00+00:00')
            if transition == 'reset': reset = client.submit('runtime', 'reset')
            allow.set()
            result = client.wait(revision, timeout=20)
            assert result['status'] == 'SUCCEEDED', result
            assert lost.is_set() and len(effects) == 1
            assert client.wait(unfinished, timeout=10)['status'] == 'EXPIRED'
            if transition == 'reset': assert client.wait(reset, timeout=10)['status'] == 'SUCCEEDED'
            selected = catalog.selected()
            assert all(row['version'] != 'original' for row in selected)
            head = controller._producer._writer_checkpoint.restore()._sealed_metadata
            assert head['manifest_sha256'] == _read_delivery_contract(output / selected[-1]['manifest'])['manifest_sha256']
            assert runner._artifact_path == output / selected[0]['manifest']
            continued = client.wait(controller.continue_calculation(additional_duration=timedelta(seconds=20)), timeout=20)
            assert continued['status'] == 'SUCCEEDED'
            selected = catalog.selected()
            for before, after in zip(selected, selected[1:]):
                a, b = (_read_delivery_contract(output / row['manifest']) for row in (before, after))
                assert b['delivery']['previous_manifest_sha256'] == a['manifest_sha256']
                assert b['delivery']['prior_state_fingerprint'] == a['last_state']
        finally:
            allow.set(); owner_stop.set(); thread.join(); runner.cleanup()
        assert not errors
