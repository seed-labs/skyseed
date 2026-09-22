"""Build admission/reuse through Deployer, with Docker as the process boundary."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from shutil import copytree
from subprocess import CompletedProcess

import pytest
from yaml import safe_dump, safe_load

from skyseed_v2.deployment import deployer as d
from skyseed_v2.deployment.deployer import _BUILD_KEY, _external_images
from skyseed_v2.deployment.metadata import _write_compose_build_plan


class DockerImages:
    def __init__(self, monkeypatch):
        self.images, self.calls, self.batches = {}, [], []
        self.fail = None
        self.live = False
        self.pull_updates = {}
        self.add('test/base:1', 'base-v1')
        self.add('test/map:1', 'map-v1')
        monkeypatch.setattr(d, 'run', self.query)
        monkeypatch.setattr(d, '_startup_command', self.mutate)

    def add(self, reference, identity, labels=None):
        image = {'Id': identity, 'RepoTags': [reference], 'Config': {'Labels': labels or {}}}
        self.images[reference] = image
        return image

    def alias(self, image, reference):
        self.images[reference] = image
        image['RepoTags'].append(reference)

    def query(self, command, **kwargs):
        self.calls.append(command)
        if command[1:3] == ['image', 'inspect']:
            refs = command[3:]
            missing = any(ref not in self.images for ref in refs)
            return CompletedProcess(command, int(missing), json.dumps([self.images[r] for r in refs if r in self.images]),
                                    'No such image' if missing else '')
        if command[1] == 'ps':
            return CompletedProcess(command, 0, 'container' if self.live else '', '')
        if 'config' in command:
            path = Path(command[command.index('-f') + 1])
            config = safe_load(path.read_text())
            for service in config['services'].values():
                if 'build' not in service:
                    continue
                if isinstance(service['build'], str):
                    service['build'] = {'context': service['build']}
                service['build']['context'] = str((path.parent / service['build']['context']).resolve())
            return CompletedProcess(command, 0, json.dumps(config), '')
        raise AssertionError(command)

    def mutate(self, command, guard):
        self.calls.append(command)
        if command[1] == 'pull':
            reference = command[2]
            if reference in self.pull_updates:
                self.add(reference, self.pull_updates[reference])
            return
        if command[1:3] == ['image', 'tag']:
            image = next(i for i in self.images.values() if i['Id'] == command[3])
            self.alias(image, command[4])
            return
        assert command[1] == 'compose' and 'build' in command
        config = safe_load(Path(command[command.index('-f') + 1]).read_text())
        project = command[command.index('-p') + 1]
        batch = command[command.index('build') + 1:]
        assert len(batch) <= int(command[command.index('--parallel') + 1])
        self.batches.append(tuple(batch))
        for name in batch:
            service = config['services'][name]
            build = service['build']
            for context in build.get('additional_contexts', {}).values():
                assert context.removeprefix('docker-image://') in self.images
            if name == self.fail:
                continue
            reference = service['image'].replace('${COMPOSE_PROJECT_NAME}', project)
            image = self.add(reference, 'sha256:' + sha256((build['labels'][_BUILD_KEY] + project).encode()).hexdigest(),
                             {**build['labels'], 'com.docker.compose.project': project})
            for tag in build['tags']:
                self.alias(image, tag)
        if self.fail in batch:
            raise d.DeploymentError('accepted Compose batch drained with failure')


@pytest.fixture
def docker(monkeypatch):
    monkeypatch.setenv('SKYSEED_DEPLOY_PARALLEL', '2')
    return DockerImages(monkeypatch)


def output(path, project='first'):
    runtime = path / 'deployment'
    (runtime / 'dummies').mkdir(parents=True)
    (runtime / 'dummies/base').write_text('FROM test/base:1\n')
    services = {'base': {'image': 'base', 'build': {'context': '.', 'dockerfile': 'dummies/base'}}}
    for name in ('a', 'b', 'c', 'd', 'map'):
        context = runtime / name; context.mkdir()
        (context / 'Dockerfile').write_text('FROM ' + ('test/map:1' if name == 'map' else 'base') + '\nCOPY payload /payload\n')
        (context / 'payload').write_text(name)
        services[name] = {'image': name, 'build': './' + name}
    (runtime / 'docker-compose.yml').write_text(safe_dump({'services': services}))
    (runtime / 'compose-project-name').write_text(project)
    _write_compose_build_plan(runtime)
    return d.Deployer(path)


def regenerated(owner, destination, project):
    copytree(owner.output, destination)
    (destination / 'deployment/compose-project-name').write_text(project)
    return d.Deployer(destination)


def test_prerequisite_order_and_compose_bounded_batches(tmp_path, docker):
    owner = output(tmp_path / 'one')
    owner.build()
    assert docker.batches == [('base',), ('a', 'b'), ('c', 'd'), ('map',)]
    count = len(docker.calls)
    owner.build()
    assert len(docker.batches) == 4
    assert not any(c[1:3] == ['image', 'tag'] for c in docker.calls[count:])


def test_cross_output_reuse_skips_build_not_just_layer_work(tmp_path, docker):
    first = output(tmp_path / 'one'); first.build()
    second = regenerated(first, tmp_path / 'two', 'second')
    second.build()
    assert len(docker.batches) == 4
    services = safe_load((second.output / 'deployment/docker-compose.yml').read_text())['services']
    for service in services.values():
        ref = service['image']
        assert docker.images[ref.replace('${COMPOSE_PROJECT_NAME}', 'first')]['Id'] == docker.images[ref.replace('${COMPOSE_PROJECT_NAME}', 'second')]['Id']


@pytest.mark.parametrize('change', ['content', 'mode', 'build-option'])
def test_changed_input_invalidates_only_its_group(tmp_path, docker, change):
    first = output(tmp_path / 'one'); first.build()
    second = regenerated(first, tmp_path / 'two', 'second')
    runtime = second.output / 'deployment'
    if change == 'content':
        (runtime / 'map/payload').write_text('changed')
    elif change == 'mode':
        (runtime / 'map/payload').chmod(0o700)
    else:
        path = runtime / 'docker-compose.yml'; config = safe_load(path.read_text())
        config['services']['map']['build'] = {'context': './map', 'args': {'REVISION': '2'}}
        path.write_text(safe_dump(config))
    second.build()
    assert docker.batches[4:] == [('map',)]


def test_base_identity_change_invalidates_prerequisite_and_dependents(tmp_path, docker):
    first = output(tmp_path / 'one'); first.build()
    second = regenerated(first, tmp_path / 'two', 'second')
    docker.add('test/base:1', 'base-v2')
    second.build()
    assert docker.batches[4:] == [('base',), ('a', 'b'), ('c', 'd')]


def test_failed_batch_drains_and_retry_reuses_successful_members(tmp_path, docker):
    owner = output(tmp_path / 'one'); docker.fail = 'a'
    with pytest.raises(d.DeploymentError, match='drained with failure'):
        owner.build()
    assert docker.batches == [('base',), ('a', 'b')]
    assert not (owner.output / 'deployment/deployment-build-pending.json').exists()
    docker.fail = None; owner.build()
    assert docker.batches[2:] == [('a', 'c'), ('d', 'map')]


def test_active_project_rejects_image_replacement(tmp_path, docker):
    owner = output(tmp_path / 'one'); owner.build(); docker.live = True
    owner.build()  # Current identities permit ordinary startup re-entry.
    (owner.output / 'deployment/map/payload').write_text('new')
    with pytest.raises(d.DeploymentError, match='project containers exist'):
        owner.build()
    assert len(docker.batches) == 4


def test_lost_batch_client_reconciles_every_declared_output_before_retry(tmp_path, docker, monkeypatch):
    owner = output(tmp_path / 'one')
    original = docker.mutate
    def lost(command, guard):
        original(command, guard)
        if 'build' in command:
            raise d._ClientLost('lost client after image export')
    monkeypatch.setattr(d, '_startup_command', lost)
    with pytest.raises(d._ClientLost):
        owner.build()
    journal = owner.output / 'deployment/deployment-build-pending.json'
    assert json.loads(journal.read_text())['version'] == 2
    monkeypatch.setattr(d, '_startup_command', original)
    owner.build()
    assert docker.batches == [('base',), ('a', 'b'), ('c', 'd'), ('map',)]
    assert not journal.exists()


def test_failed_build_journal_removal_preserves_failure_and_allows_retry(tmp_path, docker, monkeypatch):
    owner = output(tmp_path / 'one'); docker.fail = 'a'
    journal = owner.output / 'deployment/deployment-build-pending.json'
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == journal and json.loads(path.read_text()).get('command_failed'):
            raise OSError('journal removal failed')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', unlink)
        with pytest.raises(d.DeploymentError, match='drained with failure') as caught:
            owner.build()
    assert any('journal removal failed' in note for note in caught.value.__notes__)
    docker.fail = None; owner.build()
    assert docker.batches[2:] == [('a', 'c'), ('d', 'map')]


def test_multistage_refs_and_unsafe_interpolation():
    assert _external_images('FROM node:22 AS builder\nFROM node:22\nCOPY --from=builder /a /b\n') == ('node:22',)
    assert _external_images('ARG BASE\nFROM ${BASE}\n') is None


def test_prerequisite_dockerfile_change_invalidates_dependents(tmp_path, docker):
    first = output(tmp_path / 'one'); first.build()
    second = regenerated(first, tmp_path / 'two', 'second')
    (second.output / 'deployment/dummies/base').write_text('# changed prerequisite\nFROM test/base:1\n')
    second.build()
    assert docker.batches[4:] == [('base',), ('a', 'b'), ('c', 'd')]


def test_explicit_no_cache_does_not_reuse_completed_image(tmp_path, docker):
    owner = output(tmp_path / 'one')
    path = owner.output / 'deployment/docker-compose.yml'
    config = safe_load(path.read_text())
    config['services']['map']['build'] = {'context': './map', 'no_cache': True}
    path.write_text(safe_dump(config))
    owner.build(); owner.build()
    assert docker.batches[4:] == [('map',)]


def test_multiline_shell_quoting_is_not_an_image_dependency():
    assert _external_images('FROM base\nRUN echo "hello \\\nworld"\nRUN <<EOF\nit\'s a heredoc\nEOF\n') == ('base',)


def test_dockerfile_outside_context_is_part_of_reuse_key(tmp_path, docker):
    owner = output(tmp_path / 'one')
    runtime = owner.output / 'deployment'
    path = runtime / 'docker-compose.yml'
    config = safe_load(path.read_text())
    config['services']['map']['build'] = {'context': './map', 'dockerfile': '../map.Dockerfile'}
    path.write_text(safe_dump(config))
    source = runtime / 'map.Dockerfile'
    source.write_text('FROM test/map:1\nCOPY payload /payload\n')
    owner.build()
    source.write_text(source.read_text() + 'RUN touch /changed\n')
    owner.build()
    assert docker.batches[4:] == [('map',)]


def test_pull_refreshes_prerequisite_before_keying_dependents(tmp_path, docker):
    owner = output(tmp_path / 'one'); owner.build()
    path = owner.output / 'deployment/docker-compose.yml'
    config = safe_load(path.read_text())
    config['services']['a']['build'] = {'context': './a', 'pull': True}
    path.write_text(safe_dump(config))
    docker.pull_updates['test/base:1'] = 'base-v2'
    owner.build()
    assert [c for c in docker.calls if c[1] == 'pull'] == [['docker', 'pull', 'test/base:1']]
    assert docker.batches[4:] == [('base',), ('a', 'b'), ('c', 'd')]


def test_lost_pull_client_does_not_adopt_preexisting_image(tmp_path, docker, monkeypatch):
    owner = output(tmp_path / 'one')
    path = owner.output / 'deployment/docker-compose.yml'
    config = safe_load(path.read_text())
    config['services']['base']['build']['pull'] = True
    path.write_text(safe_dump(config))
    original = docker.mutate
    def lost(command, guard):
        original(command, guard)
        if command[1] == 'pull':
            raise d._ClientLost('lost pull client')
    monkeypatch.setattr(d, '_startup_command', lost)
    with pytest.raises(d._ClientLost):
        owner.build()
    pending = json.loads((owner.output / 'deployment/deployment-build-pending.json').read_text())
    assert pending['images'][0]['previous_image_id'] == 'base-v1'
    with pytest.raises(d.DeploymentError, match='unresolved'):
        owner.build()


def test_shell_arguments_do_not_declare_dependencies():
    assert _external_images('FROM base\nRUN printf "%s" "--from=example" --mount=from=wrong\n') == ('base',)


def test_heredoc_data_is_not_dockerfile_syntax():
    source = "FROM base\nRUN <<'EOF'\nFROM example\nCOPY --from=wrong /a /b\nEOF\nCOPY <<-END /file\n\tFROM wrong\n\tEND\nCOPY --from=real /a /b\nRUN --mount=type=bind,from=other,target=/x cat /x/file\n"
    assert _external_images(source) == ('base', 'real', 'other')


def test_unknown_dependency_syntax_uses_bounded_ordinary_build(tmp_path, docker):
    owner = output(tmp_path / 'one')
    source = owner.output / 'deployment/a/Dockerfile'
    source.write_text('ARG BASE=base\nFROM ${BASE}\nRUN echo "--from=example"\n')
    owner.build()
    assert docker.batches == [('base',), ('a', 'b'), ('c', 'd'), ('map',)]
    owner.build()
    assert docker.batches[4:] == [('a',)]
    assert not any('example' in call for call in docker.calls)
    decisions = [json.loads(line) for line in (owner.output / 'deployment/deployment-measurements.jsonl').read_text().splitlines()]
    assert any(row.get('service') == 'a' and row.get('cacheable') is False for row in decisions)


@pytest.mark.parametrize('source, expected', [
    ('FROM base AS base\nFROM base\n', ('base',)),
    ('FROM 123\n', None),
    ('FROM base\nCOPY "--from=example" /file\n', ('base',)),
    ('FROM base\nRUN --mount="type=bind,from=other,target=/src" echo "--from=example"\n', ('base', 'other')),
    ('FROM base\nRUN <<A <<B\nFROM data-a\nA\nFROM data-b\nB\nCOPY --from=real /x /y\n', ('base', 'real')),
])
def test_only_unquoted_docker_option_prefixes_and_actual_instructions(source, expected):
    assert _external_images(source) == expected



def test_nontrivial_prerequisite_uses_ordinary_build_instead_of_rejection(tmp_path, docker):
    owner = output(tmp_path / 'one')
    (owner.output / 'deployment/dummies/base').write_text('FROM test/base:1\nLABEL custom=1\n')
    owner.build()
    assert docker.batches == [('base',), ('a', 'b'), ('c', 'd'), ('map',)]
    decisions = [json.loads(line) for line in (owner.output / 'deployment/deployment-measurements.jsonl').read_text().splitlines()]
    assert next(r for r in decisions if r.get('service') == 'base')['cacheable'] is False
