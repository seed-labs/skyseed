from pathlib import Path
from subprocess import CompletedProcess

import pytest

from skyseed_v2.compose import launcher as module


def test_shell_membership_and_dependency_order_come_from_metadata():
    prefix = 'org.seedsecuritylabs.seedemu.meta.skyseed.'
    services = {'s5': {'labels': {prefix+'entity_type': 'Satellite', prefix+'shell_id': 'S5'}, 'depends_on': ['base']},
                'gs': {'depends_on': ['base']}, 'base': {},
                's1': {'labels': {prefix+'entity_type': 'Satellite', prefix+'shell_id': 'S1'}}}
    assert module._startup_groups(services) == [('shared', ('base', 'gs')), ('S1', ('s1',)), ('S5', ('s5',))]
    services['gs']['depends_on'] = ['s5']
    with pytest.raises(module.ComposeMaterializationError, match='later Shell'):
        module._startup_groups(services)



@pytest.mark.parametrize('message', ['Error: No such object: node', 'error: no such object: node'])
def test_optional_inspection_accepts_only_explicit_absence(tmp_path, monkeypatch, message):
    monkeypatch.setattr(module, 'run', lambda *args, **kwargs: CompletedProcess([], 1, '\n', message))
    assert module._inspect_startup_services('project', {'node': {}}, ['node'], allow_missing=True) == []
    monkeypatch.setattr(module, 'run', lambda *args, **kwargs: CompletedProcess([], 1, '', 'daemon unavailable'))
    with pytest.raises(module.ComposeMaterializationError, match='unavailable'):
        module._inspect_startup_services('project', {'node': {}}, ['node'], allow_missing=True)



def test_docker_query_timeout_pauses_before_bounded_retry(tmp_path, monkeypatch):
    from subprocess import TimeoutExpired
    guard = module._StartupMeasurements(tmp_path/'pressure.jsonl')
    calls = []
    monkeypatch.setattr(module, 'sleep', lambda seconds: None)
    def invoke(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise TimeoutExpired(command, 10)
        return CompletedProcess(command, 1, '\n', 'error: no such object: node')
    monkeypatch.setattr(module, 'run', invoke)
    assert module._inspect_startup_services('project', {'node': {}}, ['node'], allow_missing=True, guard=guard) == []
    assert all(c[:3] == ['docker', 'container', 'inspect'] for c in (calls[0], calls[1]))


def test_generated_batch_excludes_future_services_and_keeps_project_networks(tmp_path):
    from yaml import safe_load
    original = {'services': {'base': {'image': 'base'},
                            's1': {'image': 'node', 'depends_on': ['base'], 'network_mode': 'none'},
                            's5': {'image': 'node'}}, 'networks': {'default': {'driver': 'bridge'}}}
    path = tmp_path / 'batch.yml'
    module._write_startup_batch(path, original, ['s1'])
    selected = safe_load(path.read_text())
    assert set(selected['services']) == {'s1'}
    assert 'depends_on' not in selected['services']['s1']
    assert selected['networks'] == original['networks']
    assert original['services']['s1']['depends_on'] == ['base']


@pytest.mark.parametrize('reuse', [False, True])
def test_controlled_path_covers_mixed_dependency_waves(tmp_path, monkeypatch, reuse):
    from yaml import safe_dump, safe_load
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    services = {name: {'image': 'node'} for name in 'abcdef'}
    services['e']['depends_on'] = ['d']
    services['f']['depends_on'] = ['a']
    (runtime / 'docker-compose.yml').write_text(safe_dump({'services': services}))
    monkeypatch.setattr(module, 'sleep', lambda seconds: None)
    monkeypatch.setenv('SKYSEED_DEPLOY_BATCH_SIZE', '3')
    monkeypatch.setenv('SKYSEED_DEPLOY_PARALLEL', '2')
    started = {'a', 'd'} if reuse else set()
    commands = []
    def invoke(command, guard):
        selected = safe_load(Path(command[command.index('-f')+1]).read_text())['services']
        assert 1 <= len(selected) <= 3 and '--no-deps' in command and command[3] == '2'
        assert not (set(selected) & started)
        assert all(set(services[name].get('depends_on', ())) <= started for name in selected)
        commands.append(tuple(selected))
        started.update(selected)
    monkeypatch.setattr(module, '_bounded_startup_command', invoke)
    def inspect(project, services, names, **kwargs):
        return [{'Id': name, 'Config': {'Image': services[name]['image'],
                    'Labels': {'com.docker.compose.project': project, 'com.docker.compose.service': name}},
                 'State': {'Status': 'running', 'Running': True,
                           'ExitCode': 0, 'Pid': 0}, 'RestartCount': 0}
                for name in names if name in started]
    monkeypatch.setattr(module, '_inspect_startup_services', inspect)
    module._controlled_startup(tmp_path, 'project')
    assert started == set(services)
    assert commands == ([('b', 'c'), ('f',), ('e',)] if reuse else [('a', 'b', 'c'), ('d', 'f'), ('e',)])
    import json
    events = [json.loads(line) for line in (runtime/'deployment-measurements.jsonl').read_text().splitlines()]
    batches = [event for event in events if event['kind'] == 'batch-complete']
    handled = [name for event in batches for name in event['services']]
    assert sorted(handled) == sorted(services)
    assert next(event for event in events if event['kind'] == 'deployment-complete')['services'] == 6


def test_inspection_rejects_duplicate_service_as_incomplete_coverage(monkeypatch):
    import json
    c = {'Config': {'Labels': {'com.docker.compose.project': 'p', 'com.docker.compose.service': 'a'}}}
    monkeypatch.setattr(module, 'run', lambda *args, **kwargs: CompletedProcess([], 0, json.dumps(c)+'\n'+json.dumps(c), ''))
    with pytest.raises(module.ComposeMaterializationError, match='duplicate'):
        module._inspect_startup_services('p', {'a': {}, 'b': {}}, ('a', 'b'))


def test_generated_teardown_bounds_actual_resources_and_preserves_failure_retry(tmp_path):
    import json, os, subprocess
    runtime = tmp_path/'runtime'; runtime.mkdir()
    services = {'base': {}, **{f'node{i}': {'depends_on': ['base']} for i in range(23)}}
    (runtime/'docker-compose.yml').write_text(module.safe_dump(
        {'services': services, 'networks': {'external': {'external': True}, 'owned': {}}}))
    module._write_compose_cleanup_script(tmp_path, 'owned-project')
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
    assert len(args[1:])<=8
    if 'id-base' in args[1:]:assert set(state['containers'])=={'id-base'}
    state['stopped'].extend(args[1:])
elif args[0]=='rm':
    assert len(args[1:])<=8 and all(i in state['stopped'] for i in args[1:])
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
    assert all(len(call[1:]) <= 8 for call in mutations)
    assert any(len(call[1:]) == 8 for call in mutations)
    assert subprocess.run([str(tmp_path/'cleanup.sh')], env=env).returncode == 0
    again = json.loads(state.read_text())
    assert [call for call in again['calls'] if call[0] in ('stop','rm')] == mutations


def test_teardown_rejects_wrong_owner_before_mutation(tmp_path, monkeypatch):
    runtime=tmp_path/'runtime';runtime.mkdir()
    (runtime/'docker-compose.yml').write_text('services: {}\n')
    calls=[]
    def invoke(command, **kwargs):
        calls.append(command)
        return CompletedProcess(command, 0, '\"id\" \"unrelated\" \"service\"\n', '')
    monkeypatch.setattr(module, 'run', invoke)
    with pytest.raises(module.ComposeMaterializationError, match='ownership'):
        module._controlled_teardown(tmp_path, 'owned-project')
    assert len(calls)==1 and calls[0][1]=='ps'


def test_resident_policy_separates_batch_size_from_requests(monkeypatch):
    for key in ('SKYSEED_DEPLOY_BATCH_SIZE','SKYSEED_DEPLOY_PARALLEL','SKYSEED_CLEANUP_BATCH_SIZE','SKYSEED_DEPLOY_RESIDENT_STEP'):
        monkeypatch.delenv(key, raising=False)
    assert module._deployment_bounds('startup', 1999) == (64, 8)
    assert module._deployment_bounds('startup', 2000) == (64, 8)
    assert module._deployment_bounds('startup', 4765) == (64, 8)
    monkeypatch.setenv('SKYSEED_DEPLOY_BATCH_SIZE', '12')
    monkeypatch.setenv('SKYSEED_DEPLOY_PARALLEL', '3')
    assert module._deployment_bounds('startup', 4765) == (12, 3)
    assert module._deployment_bounds('cleanup', 4765) == (8, 8)
    assert module._deployment_bounds('cleanup', 1999) == (16, 16)
    monkeypatch.setenv('SKYSEED_CLEANUP_BATCH_SIZE', '65')
    with pytest.raises(ValueError): module._deployment_bounds('cleanup', 1)


def test_namespace_dependencies_use_saved_container_ids(tmp_path):
    services = {'base': {}, 'dependent': {'network_mode': 'service:base', 'pid': 'service:base'}}
    assert module._startup_groups(services) == [('shared', ('base', 'dependent'))]
    module._write_startup_batch(tmp_path/'batch.yml', {'services': services}, ['dependent'], {'base': 'immutable-base-id'})
    selected = module.safe_load((tmp_path/'batch.yml').read_text())['services']
    assert selected['dependent']['network_mode'] == 'container:immutable-base-id'
    assert selected['dependent']['pid'] == 'container:immutable-base-id'
    with pytest.raises(module.ComposeMaterializationError, match='not materialized'):
        module._write_startup_batch(tmp_path/'batch.yml', {'services': services}, ['dependent'])


def test_actual_cleanup_requests_follow_remaining_count_and_preserve_hooks(tmp_path, monkeypatch):
    import json
    runtime=tmp_path/'runtime';runtime.mkdir()
    services={'base': {}, **{f'n{i:04}': {'depends_on':['base'], 'x-skyseed-disposable-network':True} for i in range(2020)},
              'grace': {'x-skyseed-disposable-network':True, 'stop_grace_period':'3s','depends_on':['base']},
              'signal': {'x-skyseed-disposable-network':True, 'stop_signal':'SIGQUIT','depends_on':['base']},
              'hook': {'network_mode':'service:base','pre_stop':[{'command':['sh','-c','echo saved'],
                        'user':'123','working_dir':'/tmp','privileged':True,'environment':{'VALUE':'retained'}}]}}
    (runtime/'docker-compose.yml').write_text(module.safe_dump({'services':services,'networks':{'owned':{},'external':{'external':True}}}))
    alive={f'id-{name}':name for name in services};networks={'owned-id':'owned','external-id':'external'};calls=[];stopped=set()
    def run(command, **kwargs):
        calls.append(command);args=command[1:];out=''
        if args[0]=='ps':out='\n'.join(f'{json.dumps(i)} "project" {json.dumps(n)}' for i,n in alive.items())
        elif args[:1]==['compose']:out=json.dumps({'name':'project','services':services})
        elif args[:2]==['network','ls']:out='\n'.join(f'{json.dumps(i)} "project" {json.dumps(n)}' for i,n in networks.items())
        elif args[0]=='inspect':out='{"Running":true}'
        elif args[0]=='exec':assert 'id-hook' in args and 'base' not in args
        elif args[0] in ('stop','rm'):
            ids=args[1:];fast=ids[0]=='--force'
            if fast:ids=ids[1:]
            assert len(ids)<=module._deployment_bounds('cleanup',len(alive))[1]
            if 'id-base' in ids:assert len(alive)==1
            if args[0]=='stop':stopped.update(ids)
            else:
                for i in ids:
                    assert fast==module._disposable_network_service(services[alive[i]])
                    if not fast:assert i in stopped
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
    assert any(len(c[3:])==8 for c in forced) and any(len(c[3:])==16 for c in forced)
    hook=next(c for c in calls if c[:2]==['docker','exec'])
    assert hook==['docker','exec','--user','123','--workdir','/tmp','--privileged','--env','VALUE=retained','id-hook','sh','-c','echo saved']
    assert calls.index(hook)<next(i for i,c in enumerate(calls) if c[:2]==['docker','stop'] and 'id-hook' in c)


def test_fast_cleanup_failure_stops_later_dependency_removal(tmp_path, monkeypatch):
    runtime=tmp_path/'runtime';runtime.mkdir()
    (runtime/'docker-compose.yml').write_text(module.safe_dump({'services':{'base':{},'fast':{'depends_on':['base'],'x-skyseed-disposable-network':True}}}))
    calls=[]
    def run(command, **kwargs):
        calls.append(command)
        if command[1]=='ps':return CompletedProcess(command,0,'"fast-id" "project" "fast"\n"base-id" "project" "base"\n','')
        return CompletedProcess(command,7,'','force removal failed')
    monkeypatch.setattr(module,'run',run)
    with pytest.raises(module.ComposeMaterializationError,match='force removal failed'):
        module._controlled_teardown(tmp_path,'project')
    assert calls[-1]==['docker','rm','--force','fast-id']
    assert len(calls)==2


@pytest.mark.parametrize('fail', [False, True])
def test_startup_failure_rollback_keeps_reverse_namespace_order(tmp_path, monkeypatch, fail):
    services={'base':{},'child':{'network_mode':'service:base','stop_signal':'SIGUSR1'}}
    alive={'base','child'};calls=[]
    def inspect(project, services, names, **kwargs):
        return [{'Id':'id-'+n,'Config':{'Labels':{'com.docker.compose.service':n}},'State':{'Running':True}} for n in names if n in alive]
    monkeypatch.setattr(module,'_inspect_startup_services',inspect)
    def run(command, **kwargs):
        calls.append(command)
        if command[1]=='rm':
            assert command==['docker','rm','--force','id-child'] if 'child' in alive else command==['docker','rm','--force','id-base']
            if fail:return CompletedProcess(command,1,'','child removal failed')
            for identity in command[3:]:alive.remove(identity.removeprefix('id-'))
        if command[1:3]==['container','inspect']:return CompletedProcess(command,1,'','No such object')
        return CompletedProcess(command,0,'','')
    monkeypatch.setattr(module,'run',run)
    guard=module._StartupMeasurements(tmp_path/'rollback.jsonl')
    if fail:
        with pytest.raises(module.ComposeMaterializationError,match='incomplete'):
            module._controlled_startup_cleanup('project',services,['base','child'],guard)
        assert alive=={'base','child'} and not any('id-base' in c for c in calls)
    else:
        module._controlled_startup_cleanup('project',services,['base','child'],guard)
        assert not alive
    assert calls[0]==['docker','stop','id-child']


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
