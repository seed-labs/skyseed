from pathlib import Path
from subprocess import CompletedProcess

import pytest

from skyseed.deployment import deployer as module


@pytest.mark.parametrize('failed_container', [False, True])
def test_whole_shell_failure_reentry_preserves_confirmed_containers_and_evidence(tmp_path, monkeypatch, failed_container):
    import json
    from copy import deepcopy
    prefix = 'org.seedsecuritylabs.seedemu.meta.skyseed.'
    runtime = tmp_path/'deployment'; runtime.mkdir()
    services = {'gs': {'image': 'node'}, **{
        f'{group}-{i}': {'image': 'node', 'labels': {prefix+'entity_type': 'Satellite', prefix+'shell_id': group}}
        for group, count in [('A', 300), ('B', 3), ('C', 2)] for i in range(count)}}
    (runtime/'docker-compose.yml').write_text(module.safe_dump({'services': services}))
    alive, commands = {}, []
    failing = True
    def inspect(project, definitions, names, **kwargs):
        return [deepcopy(alive[n]) for n in names if n in alive]
    monkeypatch.setattr(module, '_inspect_startup_services', inspect)
    def invoke(command, **kwargs):
        if command[1] == 'rm':
            for identity in command[3:]:
                del alive[identity.removeprefix('id-')]
        return CompletedProcess(command, 0, '[{"Id":"image-id"}]', '')
    monkeypatch.setattr(module, 'run', invoke)
    def deploy(command, guard):
        nonlocal failing
        selected = module.safe_load(Path(command[command.index('-f')+1]).read_text())['services']
        requested = command[command.index('--no-recreate')+1:]
        commands.append(tuple(requested))
        assert command[3] == '8'
        assert all(n not in alive for n in requested)
        for name in requested:
            alive[name] = {'Id': 'id-'+name, 'Image': 'image-id', 'Config': {
                'Labels': {**selected[name]['labels'], 'com.docker.compose.project': 'project',
                           'com.docker.compose.service': name}},
                'State': {'Status': 'running', 'Running': True}, 'RestartCount': 0}
            if name.startswith('B') and failing:
                if failed_container:
                    alive[name]['State'].update(Status='exited', Running=False, ExitCode=1)
                failing = False
                raise module.DeploymentError('injected partial group')
    monkeypatch.setattr(module, '_startup_command', deploy)
    monkeypatch.setenv('SKYSEED_DEPLOY_BATCH_SIZE', '1')
    monkeypatch.delenv('SKYSEED_DEPLOY_PARALLEL', raising=False)
    with pytest.raises(module.DeploymentError, match='partial group'):
        module._controlled_startup(tmp_path, 'project')
    assert len(alive) == 302 and not any(n.startswith('C') for n in alive)
    evidence = runtime/'deployment-measurements.jsonl'
    original = evidence.read_bytes()
    stopped = json.loads(original.splitlines()[-1])
    assert stopped['completed_groups'] == ['shared', 'A']
    assert len(stopped['partial_state']) == 1
    preserved = deepcopy({name: container for name, container in alive.items()
                          if not failed_container or not name.startswith('B')})
    module._controlled_startup(tmp_path, 'project')
    assert len(alive) == len(services) and all(alive[n] == c for n, c in preserved.items())
    assert [len(c) for c in commands] == [1, 300, 3, 3 if failed_container else 2, 2]
    assert evidence.read_bytes().startswith(original)
    alive['A-0']['Image'] = 'different-image'
    with pytest.raises(module.DeploymentError, match='identity differs'):
        module._controlled_startup(tmp_path, 'project')
    assert len(commands) == 5


def test_group_input_preserves_internal_dependencies_and_checks_external_conditions(tmp_path):
    services = {'base': {}, 'inside': {'image': 'n'}, 'child': {'image': 'n', 'pid': 'service:inside',
        'depends_on': {'base': {'condition': 'service_healthy'}, 'inside': {'condition': 'service_started'}}}}
    path = tmp_path/'group.yml'
    identities = {'base': {'Id': 'immutable', 'State': {'Status': 'running', 'Health': {'Status': 'starting'}}}}
    with pytest.raises(module.DeploymentError, match='unsatisfied'):
        module._write_startup_group(path, {'services': services}, ['inside', 'child'], identities, {'inside': 'a', 'child': 'b'})
    identities['base']['State']['Health']['Status'] = 'healthy'
    module._write_startup_group(path, {'services': services}, ['inside', 'child'], identities, {'inside': 'a', 'child': 'b'})
    selected = module.safe_load(path.read_text())['services']
    assert selected['child']['pid'] == 'service:inside'
    assert selected['child']['depends_on'] == {'inside': {'condition': 'service_started'}}
    assert 'base' in services['child']['depends_on']


def test_measurements_neither_impose_a_deadline_nor_mask_operations(tmp_path, monkeypatch):
    guard = module._StartupMeasurements(tmp_path / 'missing' / 'events')
    guard.record('completed', elapsed_seconds=999999)
    assert guard.failure is not None
    assert not hasattr(guard, 'remaining')


def test_client_completion_waits_for_daemon_state_without_attempt_cutoff(monkeypatch):
    observations = iter([[], *([{'Config': {'Labels': {'com.docker.compose.service': 'node'}}, 'State': {'Status': 'created'}}] for _ in range(45)),
        [{'Config': {'Labels': {'com.docker.compose.service': 'node'}}, 'State': {'Status': 'running'}}]])
    monkeypatch.setattr(module, '_inspect_startup_services', lambda *a, **kw: next(observations))
    monkeypatch.setattr(module, 'sleep', lambda seconds: None)
    assert module._settle_services('project', {'node': {}}, ['node'], require_all=True)[0]['State']['Status'] == 'running'


def test_shell_membership_and_dependency_order_come_from_metadata():
    prefix = 'org.seedsecuritylabs.seedemu.meta.skyseed.'
    services = {'s5': {'labels': {prefix+'entity_type': 'Satellite', prefix+'shell_id': 'S5'}, 'depends_on': ['base']},
                'gs': {'depends_on': ['base']}, 'base': {},
                's1': {'labels': {prefix+'entity_type': 'Satellite', prefix+'shell_id': 'S1'}}}
    assert module._startup_groups(services) == [('shared', ('base', 'gs')), ('S1', ('s1',)), ('S5', ('s5',))]
    services['gs']['depends_on'] = ['s5']
    services['s5'].pop('depends_on')
    assert [g for g, _ in module._startup_groups(services)] == ['S1', 'S5', 'shared']
    services['s5']['depends_on'] = ['gs']
    with pytest.raises(module.DeploymentError, match='cyclic'):
        module._startup_groups(services)



@pytest.mark.parametrize('message', ['Error: No such object: node', 'error: no such object: node'])
def test_optional_inspection_accepts_only_explicit_absence(tmp_path, monkeypatch, message):
    monkeypatch.setattr(module, 'run', lambda *args, **kwargs: CompletedProcess([], 1, '\n', message))
    assert module._inspect_startup_services('project', {'node': {}}, ['node'], allow_missing=True) == []
    monkeypatch.setattr(module, 'run', lambda *args, **kwargs: CompletedProcess([], 1, '', 'daemon unavailable'))
    with pytest.raises(module.DeploymentError, match='unavailable'):
        module._inspect_startup_services('project', {'node': {}}, ['node'], allow_missing=True)



def test_docker_query_timeout_stops_without_retry(tmp_path, monkeypatch):
    from subprocess import TimeoutExpired
    calls = []
    def invoke(command, **kwargs):
        calls.append(command)
        raise TimeoutExpired(command, 10)
    monkeypatch.setattr(module, 'run', invoke)
    with pytest.raises(TimeoutExpired):
        module._inspect_startup_services('project', {'node': {}}, ['node'], allow_missing=True,
                                        guard=module._StartupMeasurements(tmp_path/'events.jsonl'))
    assert len(calls) == 1


def test_inspection_rejects_duplicate_service_as_incomplete_coverage(monkeypatch):
    import json
    c = {'Config': {'Labels': {'com.docker.compose.project': 'p', 'com.docker.compose.service': 'a'}}}
    monkeypatch.setattr(module, 'run', lambda *args, **kwargs: CompletedProcess([], 0, json.dumps(c)+'\n'+json.dumps(c), ''))
    with pytest.raises(module.DeploymentError, match='duplicate'):
        module._inspect_startup_services('p', {'a': {}, 'b': {}}, ('a', 'b'))


def test_generated_teardown_bounds_actual_resources_and_preserves_failure_retry(tmp_path):
    import json, os, subprocess
    runtime = tmp_path/'deployment'; runtime.mkdir()
    services = {'base': {}, **{f'node{i}': {'depends_on': ['base']} for i in range(23)}}
    (runtime/'docker-compose.yml').write_text(module.safe_dump(
        {'services': services, 'networks': {'external': {'external': True}, 'owned': {}}}))
    (runtime / 'compose-project-name').write_text('owned-project\n')
    module._write_deployment_scripts(tmp_path, module.sys.executable, None)
    state = tmp_path/'state'
    state.write_text(json.dumps({'containers': {f'id-{name}': name for name in services},
                                'networks': {'net-owned': 'owned', 'net-external': 'external'},
                                'stopped': [], 'calls': [], 'fail': True}))
    bin_dir = tmp_path/'bin'; bin_dir.mkdir()
    docker = bin_dir/'docker'
    docker.write_text("#!" + module.sys.executable + "\n" + r"""
import json,os,sys
from pathlib import Path
path=Path(os.environ['STATE']);state=json.loads(path.read_text());args=sys.argv[1:]
state['calls'].append(args);code=0
if args[0]=='ps':
    for identity,service in state['containers'].items():
        print(json.dumps(identity),json.dumps('owned-project'),json.dumps(service))
elif args[:2]==['network','ls']:
    for identity,key in state['networks'].items():
        print(json.dumps(identity),json.dumps('owned-project'),json.dumps(key))
elif args[0]=='stop':
    assert len(args[1:])<=23
    if 'id-base' in args[1:]:assert set(state['containers'])=={'id-base'}
    state['stopped'].extend(args[1:])
elif args[0]=='rm':
    args.remove('--force')
    assert len(args[1:])<=23 and all(i in state['stopped'] for i in args[1:])
    if state['fail']:
        state['fail']=False;code=7;print('injected remove failure',file=sys.stderr)
    else:
        for identity in args[1:]:del state['containers'][identity]
elif args[:2]==['network','rm']:
    assert not state['containers'] and len(args[2:])<=8
    for identity in args[2:]:
        assert state['networks'][identity]!='external'
        del state['networks'][identity]
else:raise AssertionError(args)
path.write_text(json.dumps(state));sys.exit(code)
""")
    docker.chmod(0o755)
    env = {**os.environ, 'PATH': str(bin_dir)+':'+os.environ['PATH'], 'STATE': str(state),
           'COMPOSE_PARALLEL_LIMIT': '-1', 'SKYSEED_CLEANUP_BATCH_SIZE': '8'}
    first = subprocess.run([str(tmp_path/'cleanup.sh')], env=env, capture_output=True, text=True)
    assert first.returncode != 0 and 'injected remove failure' in first.stderr
    failed = json.loads(state.read_text())
    assert len(failed['containers']) == 24
    assert not any('id-base' in call for call in failed['calls'] if call[0] in ('stop', 'rm'))
    assert subprocess.run([str(tmp_path/'cleanup.sh')], env=env).returncode == 0
    done = json.loads(state.read_text())
    assert done['containers'] == {} and done['networks'] == {'net-external': 'external'}
    mutations = [call for call in done['calls'] if call[0] in ('stop', 'rm')]
    assert any(len(call[1:]) == 23 for call in mutations)
    assert subprocess.run([str(tmp_path/'cleanup.sh')], env=env).returncode == 0
    again = json.loads(state.read_text())
    assert [call for call in again['calls'] if call[0] in ('stop','rm')] == mutations


def test_teardown_rejects_wrong_owner_before_mutation(tmp_path, monkeypatch):
    runtime=tmp_path/'deployment';runtime.mkdir()
    (runtime/'docker-compose.yml').write_text('services: {}\n')
    calls=[]
    def invoke(command, **kwargs):
        calls.append(command)
        return CompletedProcess(command, 0, '\"id\" \"unrelated\" \"service\"\n', '')
    monkeypatch.setattr(module, 'run', invoke)
    with pytest.raises(module.DeploymentError, match='ownership'):
        module._controlled_teardown(tmp_path, 'owned-project')
    assert len(calls)==1 and calls[0][1]=='ps'


def test_fixed_group_concurrency_ignores_retired_count_controls(monkeypatch):
    monkeypatch.delenv('SKYSEED_DEPLOY_PARALLEL', raising=False)
    monkeypatch.setenv('SKYSEED_DEPLOY_BATCH_SIZE', '1')
    monkeypatch.setenv('SKYSEED_DEPLOY_RESIDENT_STEP', '1')
    assert module._startup_parallel() == 8
    monkeypatch.setenv('SKYSEED_DEPLOY_PARALLEL', '12')
    assert module._startup_parallel() == 12
    monkeypatch.setenv('SKYSEED_DEPLOY_PARALLEL', '0')
    with pytest.raises(ValueError):
        module._startup_parallel()


def test_whole_cleanup_keeps_real_dependencies_and_graceful_hooks(tmp_path, monkeypatch):
    import json
    runtime=tmp_path/'deployment';runtime.mkdir()
    services={'base': {}, **{f'n{i:04}': {'depends_on':['base'], 'x-skyseed-disposable-network':True} for i in range(2020)},
              'grace': {'x-skyseed-disposable-network':True, 'stop_grace_period':'3s','depends_on':['base']},
              'signal': {'x-skyseed-disposable-network':True, 'stop_signal':'SIGQUIT','depends_on':['base']},
              'hook': {'network_mode':'service:base','pre_stop':[{'command':['sh','-c','echo saved'],
                        'user':'123','working_dir':'/tmp','privileged':True,'environment':{'VALUE':'retained'}}]}}
    (runtime/'docker-compose.yml').write_text(module.safe_dump({'services':services,'networks':{'owned':{},'external':{'external':True}}}))
    alive={f'id-{name}':name for name in services};networks={'owned-id':'owned','external-id':'external'};calls=[];stopped=set()
    clock = [0.0]
    monkeypatch.setattr(module, 'monotonic', lambda: clock[0])
    def run(command, **kwargs):
        # Each completed operation exceeds the former whole-stage ceiling.
        # Real resource/dependency state still governs every following action.
        clock[0] += 3601
        assert kwargs.get('timeout') is None
        calls.append(command);args=command[1:];out=''
        if args[0]=='ps':out='\n'.join(f'{json.dumps(i)} "project" {json.dumps(n)}' for i,n in alive.items())
        elif args[:1]==['compose']:out=json.dumps({'name':'project','services':services})
        elif args[:2]==['network','ls']:out='\n'.join(f'{json.dumps(i)} "project" {json.dumps(n)}' for i,n in networks.items())
        elif args[0]=='inspect':out='{"Running":true}'
        elif args[0]=='exec':assert 'id-hook' in args and 'base' not in args
        elif args[0] in ('stop','rm'):
            ids=args[1:];fast=ids[0]=='--force'
            if fast:ids=ids[1:]
            if 'id-base' in ids:assert len(alive)==1
            if args[0]=='stop':stopped.update(ids)
            else:
                for i in ids:
                    if not module._disposable_network_service(services[alive[i]]):assert i in stopped
                    del alive[i]
        elif args[:2]==['network','rm']:
            assert not alive
            for i in args[2:]:assert networks.pop(i)=='owned'
        else:raise AssertionError(command)
        return CompletedProcess(command,0,out,'')
    monkeypatch.setattr(module,'run',run)
    module._controlled_teardown(tmp_path,'project')
    assert not alive and networks=={'external-id':'external'}
    forced=[c for c in calls if c[:3]==['docker','rm','--force']]
    assert [len(c[3:]) for c in forced] == [2023, 1]
    hook=next(c for c in calls if c[:2]==['docker','exec'])
    assert hook==['docker','exec','--user','123','--workdir','/tmp','--privileged','--env','VALUE=retained','id-hook','sh','-c','echo saved']
    assert calls.index(hook)<next(i for i,c in enumerate(calls) if c[:2]==['docker','stop'] and 'id-hook' in c)


def test_fast_cleanup_failure_stops_later_dependency_removal(tmp_path, monkeypatch):
    runtime=tmp_path/'deployment';runtime.mkdir()
    (runtime/'docker-compose.yml').write_text(module.safe_dump({'services':{'base':{},'fast':{'depends_on':['base'],'x-skyseed-disposable-network':True}}}))
    calls=[]
    def run(command, **kwargs):
        calls.append(command)
        if command[1]=='ps':return CompletedProcess(command,0,'"fast-id" "project" "fast"\n"base-id" "project" "base"\n','')
        return CompletedProcess(command,7,'','force removal failed')
    monkeypatch.setattr(module,'run',run)
    with pytest.raises(module.DeploymentError,match='force removal failed'):
        module._controlled_teardown(tmp_path,'project')
    assert calls[-1]==['docker','rm','--force','fast-id']
    assert len(calls)==2


def test_stop_hooks_use_compose_resolved_options_without_name_based_stop(tmp_path):
    import json
    original={'node':{'pre_stop':[{'command':'echo ${VALUE}', 'environment':{'VALUE':'${VALUE}'}}]}}
    calls=[]
    def command(argv):
        calls.append(argv)
        return json.dumps({'name':'project','services':{'node':{'pre_stop':[{'command':['echo','resolved'], 'environment':{'VALUE':'resolved'}}]}}})
    resolved=module._resolved_stop_services(original,tmp_path/'compose.yml','project',command)
    assert calls==[['docker','compose','-p','project','-f',str(tmp_path/'compose.yml'),'config','--format','json']]
    assert module._pre_stop_commands(resolved['node'],'immutable-id')==[['docker','exec','--env','VALUE=resolved','immutable-id','echo','resolved']]
    assert original['node']['pre_stop'][0]['environment']['VALUE']=='${VALUE}'


def test_image_preparation_preserves_builds_and_external_images_without_runtime_fields(tmp_path):
    from yaml import safe_dump,safe_load
    runtime=tmp_path/'deployment';runtime.mkdir()
    source=runtime/'docker-compose.yml'
    base={'image':'base','build':{'context':'.','dockerfile':'dummies/base'}}
    node={'image':'${COMPOSE_PROJECT_NAME}-nodes','build':'./node','platform':'linux/amd64',
          'network_mode':'service:base','depends_on':['base'],'volumes':['/host:/host']}
    services={'base':base,'node':node,'replica':{'image':node['image'],'command':['sleep','infinity']},
              'map':{'image':'external/map:v1','ports':['8888:80']}}
    source.write_text(safe_dump({'services':services,'secrets':{'token':{'file':'./token'}}}))
    before=source.read_bytes()
    derived=module._prepare_controlled_image_compose(tmp_path,'project')
    value=safe_load(derived.read_text())
    assert source.read_bytes()==before
    assert value['services']=={'base':base,'node':{key:node[key] for key in ('image','build','platform')},
                              'map':{'image':'external/map:v1'}}
    assert value['secrets']=={'token':{'file':'./token'}}
    services['replica'].pop('image');source.write_text(safe_dump({'services':services}))
    with pytest.raises(module.DeploymentError,match='lacks image'):
        module._prepare_controlled_image_compose(tmp_path,'project')


@pytest.mark.parametrize('field,observed_field', [('network_mode', 'NetworkMode'), ('ipc', 'IpcMode'), ('pid', 'PidMode')])
@pytest.mark.parametrize('owner_state', ['failed', 'missing', 'replaced', 'matching'])
def test_retry_reconciles_actual_cross_group_namespace_bindings(tmp_path, monkeypatch, field, observed_field, owner_state):
    import json
    from copy import deepcopy
    prefix = 'org.seedsecuritylabs.seedemu.meta.skyseed.'
    def shell(name):
        return {prefix+'entity_type': 'Satellite', prefix+'shell_id': name}
    services = {'base': {'image': 'node'},
                'child': {'image': 'node', 'labels': shell('S1'), field: 'service:base',
                          'stop_grace_period': '2s', 'pre_stop': [{'command': ['save']}]},
                'leaf': {'image': 'node', 'labels': shell('S2'), 'pid': 'service:child'},
                'unrelated': {'image': 'node', 'labels': shell('S1'), 'depends_on': ['base']}}
    runtime = tmp_path/'deployment'; runtime.mkdir()
    (runtime/'docker-compose.yml').write_text(module.safe_dump({'services': services}))
    def container(name, identity):
        return {'Id': identity, 'Image': 'image-id', 'RestartCount': 0,
                'Config': {'Labels': {'com.docker.compose.project': 'project', 'com.docker.compose.service': name,
                                     module._DEPLOYMENT_CONFIG_LABEL: module._deployment_service_digest(services[name], 'image-id')}},
                'HostConfig': {}, 'State': {'Status': 'running', 'Running': True}}
    alive = {n: container(n, 'old-'+n) for n in services}
    alive['child']['HostConfig'][observed_field] = 'container:old-base'
    alive['leaf']['HostConfig']['PidMode'] = 'container:old-child'
    if owner_state == 'failed':
        alive['base']['State'].update(Status='exited', Running=False, ExitCode=1)
    elif owner_state == 'missing':
        del alive['base']
    elif owner_state == 'replaced':
        alive['base']['Id'] = 'new-base'
    retained = deepcopy(alive['unrelated']); calls=[]; started=[]
    monkeypatch.setattr(module, '_inspect_startup_services', lambda project, definitions, names, **kw: [deepcopy(alive[n]) for n in names if n in alive])
    monkeypatch.setattr(module, '_resolved_stop_services', lambda *args: services)
    def invoke(command, **kw):
        calls.append(command)
        if command[1] == 'rm':
            for identity in command[3:]:
                name = next(n for n,c in alive.items() if c['Id'] == identity)
                # Namespace consumers must be removed before their owners.
                assert not any('container:'+identity in c['HostConfig'].values() for n,c in alive.items() if n != name)
                del alive[name]
        return CompletedProcess(command, 0, '[{"Id":"image-id"}]', '')
    monkeypatch.setattr(module, 'run', invoke)
    def deploy(command, guard):
        selected = module.safe_load(Path(command[command.index('-f')+1]).read_text())['services']
        for name in command[command.index('--no-recreate')+1:]:
            started.append(name)
            alive[name] = container(name, 'new-'+name)
            for f, hf in [('network_mode','NetworkMode'),('ipc','IpcMode'),('pid','PidMode')]:
                if f in selected[name]:
                    binding = selected[name][f]
                    if binding.startswith('service:'):
                        binding = 'container:'+alive[binding[8:]]['Id']
                    alive[name]['HostConfig'][hf] = binding
    monkeypatch.setattr(module, '_startup_command', deploy)
    module._controlled_startup(tmp_path, 'project')
    assert alive['unrelated'] == retained
    assert alive['child']['HostConfig'][observed_field] == 'container:'+alive['base']['Id']
    assert alive['leaf']['HostConfig']['PidMode'] == 'container:'+alive['child']['Id']
    if owner_state == 'matching':
        assert not started and not any(c[1] in ('rm','stop','exec') for c in calls)
    else:
        assert started == (['base'] if owner_state in ('failed','missing') else []) + ['child','leaf']
        hook = ['docker','exec','old-child','save']
        assert calls.index(hook) < calls.index(['docker','stop','old-child'])
        events = [json.loads(v) for v in (runtime/'deployment-measurements.jsonl').read_text().splitlines()]
        assert any(e['kind'] == 'retry-unsuccessful-containers' for e in events)
        assert events[-1]['kind'] == 'deployment-complete'


def test_cross_group_retry_removal_failure_preserves_owner_and_partial_evidence(tmp_path, monkeypatch):
    import json
    from copy import deepcopy
    services={'base':{'image':'node'},'child':{'image':'node','network_mode':'service:base',
        'labels':{'org.seedsecuritylabs.seedemu.meta.skyseed.entity_type':'Satellite',
                  'org.seedsecuritylabs.seedemu.meta.skyseed.shell_id':'S1'}}}
    runtime=tmp_path/'deployment';runtime.mkdir();(runtime/'docker-compose.yml').write_text(module.safe_dump({'services':services}))
    alive={n:{'Id':'old-'+n,'Image':'image-id','Config':{'Labels':{
        'com.docker.compose.service':n,module._DEPLOYMENT_CONFIG_LABEL:module._deployment_service_digest(s,'image-id')}},
        'State':{'Status':'running','Running':True},'HostConfig':{}} for n,s in services.items()}
    alive['base']['State'].update(Status='exited',Running=False,ExitCode=1)
    alive['child']['HostConfig']['NetworkMode']='container:old-base'
    monkeypatch.setattr(module,'_inspect_startup_services',lambda p,s,n,**kw:[deepcopy(alive[k]) for k in n])
    calls=[]
    def invoke(command,**kw):
        calls.append(command)
        if command[1]=='rm':return CompletedProcess(command,1,'','injected removal failure')
        return CompletedProcess(command,0,'[{"Id":"image-id"}]','')
    monkeypatch.setattr(module,'run',invoke)
    monkeypatch.setattr(module,'_startup_command',lambda *a:pytest.fail('failure must stop admission'))
    with pytest.raises(module.DeploymentError,match='removal failure'):module._controlled_startup(tmp_path,'project')
    assert not any('old-base' in c for c in calls if c[1] in ('stop','rm'))
    event=json.loads((runtime/'deployment-measurements.jsonl').read_text().splitlines()[-1])
    assert event['kind']=='admission-stopped' and event['failing_group']=='deployment-reconciliation'
    assert {c['Id'] for c in event['partial_state']}=={'old-base','old-child'}
    assert event['confirmed_groups_preserved'] is True


@pytest.fixture(autouse=True)
def fake_mutation_boundary(monkeypatch):
    def invoke(command):
        result = module.run(command, capture_output=True, text=True)
        if result.returncode:
            raise module.DeploymentError(result.stderr or result.stdout)
        return result
    monkeypatch.setattr(module, '_mutation', invoke)
