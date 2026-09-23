"""Launch and verify one already-generated Compose project."""

from __future__ import annotations

from hashlib import sha256
from json import dumps, loads
from pathlib import Path
from re import fullmatch, sub
from copy import deepcopy
from platform import machine
from shlex import quote, split
import os
import sys
from subprocess import CompletedProcess, Popen, PIPE, run
from functools import wraps
import fcntl
from time import monotonic, sleep, time
from typing import Sequence
from uuid import uuid4

from ._dockerfile import external_images as _external_images
from skyseed.shared._console import _console

from yaml import safe_dump, safe_load


class DeploymentError(RuntimeError):
    """Layer: deployment. Public operation error, not a successful deployment result."""


def _host_startup_sample() -> dict:
    """Read host pressure without contacting Docker or enumerating processes."""
    memory = {line.split(':')[0]: int(line.split()[1]) * 1024
              for line in Path('/proc/meminfo').read_text().splitlines()}
    vm = dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
    pressure = {}
    for resource in ('memory', 'io'):
        for line in Path(f'/proc/pressure/{resource}').read_text().splitlines():
            kind, *fields = line.split()
            pressure[f'{resource}_{kind}'] = dict(
                (key, float(value)) for key, value in (field.split('=') for field in fields))
    scopes = {}
    for name in ('docker.service', 'containerd.service'):
        path = Path('/sys/fs/cgroup/system.slice') / name
        try:
            scopes[name] = {key: (path / key).read_text().strip()
                            for key in ('memory.current', 'memory.events', 'pids.current')}
        except OSError as error:
            scopes[name] = {'unavailable': str(error)}
    return {'monotonic': monotonic(), 'wall_time': time(), 'memory': memory,
            'cpu_jiffies': Path('/proc/stat').read_text().splitlines()[0].split()[1:],
            'pressure': pressure, 'pswpin': int(vm['pswpin']),
            'pswpout': int(vm['pswpout']), 'service_cgroups': scopes}


class _StartupMeasurements:
    """Lightweight deployment checkpoints, with no admission or abort policy."""

    def __init__(self, path: Path):
        self.path = path
        self.failure = None

    def record(self, kind: str, **fields) -> None:
        try:
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(dumps({'kind': kind, 'monotonic': monotonic(), **fields}) + '\n')
        except Exception as error:
            self.failure = error
            try:
                print(f"deployment measurement degraded: {error}", file=sys.stderr)
            except Exception:
                pass

    def checkpoint(self):
        try:
            sample = _host_startup_sample()
            self.record('host', sample=sample)
            return sample['memory']['MemAvailable']
        except Exception as error:
            self.failure = error
            self.record('measurement-unavailable', error=str(error))
            return None


def _startup_parallel(value=None) -> int:
    value = os.environ.get('SKYSEED_DEPLOY_PARALLEL', '8') if value is None else value
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('startup concurrency must be an integer')
    parallel = int(value)
    if not 1 <= parallel <= 64:
        raise ValueError('startup concurrency must be between 1 and 64')
    return parallel


def _compose_dependencies(service: dict) -> set[str]:
    result = set(service.get('depends_on', ()))
    for field in ('network_mode', 'ipc', 'pid'):
        value = service.get(field, '')
        if isinstance(value, str) and value.startswith('service:'):
            result.add(value.removeprefix('service:'))
    return result


def _disposable_network_service(service: dict) -> bool:
    return service.get('x-skyseed-disposable-network') is True and not any(
        key in service for key in ('stop_signal', 'stop_grace_period', 'pre_stop'))


def _resolved_stop_services(services: dict, path: Path, project: str, command) -> dict:
    """Let Compose resolve hook interpolation/options, without lifecycle actions."""
    hooked = [name for name, service in services.items() if service.get('pre_stop')]
    if not hooked:
        return services
    resolved = loads(command(['docker', 'compose', '-p', project, '-f', str(path),
                              'config', '--format', 'json']))
    if resolved.get('name') != project:
        raise DeploymentError('resolved hook project identity differs')
    result = dict(services)
    for name in hooked:
        value = resolved.get('services', {}).get(name)
        if not value or not value.get('pre_stop'):
            raise DeploymentError('resolved service stop hook missing')
        result[name] = {**services[name], 'pre_stop': value['pre_stop']}
    return result


def _pre_stop_commands(service: dict, identity: str) -> list[list[str]]:
    """Preserve Compose hook options while addressing the saved container ID."""
    result = []
    for hook in service.get('pre_stop', ()):
        if set(hook) - {'command', 'user', 'privileged', 'working_dir', 'environment'}:
            raise DeploymentError('unsupported generated pre_stop option')
        command = ['docker', 'exec']
        for key, flag in (('user', '--user'), ('working_dir', '--workdir')):
            if hook.get(key) is not None:
                command.extend([flag, str(hook[key])])
        if hook.get('privileged'):
            command.append('--privileged')
        environment = hook.get('environment', {})
        if isinstance(environment, dict):
            environment = [f'{key}={value}' if value is not None else key for key, value in environment.items()]
        for value in environment:
            command.extend(['--env', value])
        argv = hook.get('command')
        argv = split(argv) if isinstance(argv, str) else argv
        if not argv:
            raise DeploymentError('empty generated pre_stop command')
        result.append([*command, identity, *argv])
    return result


def _startup_groups(services: dict) -> list[tuple[str, tuple[str, ...]]]:
    """Use generated entity/shell labels, with dependency order inside each group."""
    by_group: dict[str, list[str]] = {'shared': []}
    membership = {}
    for name, service in services.items():
        labels = service.get('labels', {})
        kind = labels.get('org.seedsecuritylabs.seedemu.meta.skyseed.entity_type')
        group = labels.get('org.seedsecuritylabs.seedemu.meta.skyseed.shell_id') if kind == 'Satellite' else 'shared'
        if not isinstance(group, str) or not group:
            raise DeploymentError(f'missing generated Shell identity: {name}')
        membership[name] = group
        by_group.setdefault(group, []).append(name)
    dependencies = {name: _compose_dependencies(service) for name, service in services.items()}
    if any(values - services.keys() for values in dependencies.values()):
        raise DeploymentError('unknown generated Compose dependency')
    group_dependencies = {group: {membership[dep] for name in names for dep in dependencies[name]
                                 if membership[dep] != group}
                          for group, names in by_group.items()}
    ordered = []
    while len(ordered) < len(by_group):
        ready = sorted((g for g in by_group if g not in ordered and group_dependencies[g] <= set(ordered)),
                       key=lambda g: (g != 'shared', g))
        if not ready:
            raise DeploymentError('cyclic deployment group dependencies')
        ordered.append(ready[0])
    result = []
    completed: set[str] = set()
    for group in ordered:
        pending = set(by_group[group])
        output = []
        while pending:
            ready = []
            for name in sorted(pending):
                if dependencies[name] <= completed:
                    ready.append(name)
            if not ready:
                raise DeploymentError('cyclic generated Compose dependencies')
            output.extend(ready)
            completed.update(ready)
            pending.difference_update(ready)
        result.append((group, tuple(output)))
    return result


class _ClientLost(DeploymentError):
    """The client disappeared; daemon completion has not been established."""


def _mutation(command, *, display_output=False):
    """Join the admitted local client even after executor cancellation."""
    # Inherited descriptors let Docker own native output and TTY progress. Only
    # commands whose output is consumed internally need captured streams.
    stream = None if display_output else PIPE
    process = Popen(command, start_new_session=True, stdout=stream, stderr=stream, text=True)
    interrupted = None
    while True:
        try:
            stdout, stderr = process.communicate()
            break
        except KeyboardInterrupt as error:
            if interrupted is None:
                _console("Deployment cancellation requested: waiting for the admitted Docker client to drain; completion is not yet confirmed.")
            interrupted = error
    if process.returncode < 0:
        raise _ClientLost(f"Docker client exited by signal {-process.returncode}; daemon work is unresolved")
    result = CompletedProcess(command, process.returncode, stdout or '', stderr or '')
    if result.returncode:
        error = DeploymentError(result.stderr or result.stdout or f"Docker operation failed: exit {result.returncode}; see Docker output above")
        if interrupted is not None:
            error.add_note("cancellation requested while the accepted client was draining")
        raise error
    if interrupted is not None:
        raise interrupted
    return result


def _accepted_mutation(command, output):
    """Retain uncertain admission so explicit re-entry cannot duplicate its work."""
    path = output / 'deployment/deployment-mutation.json'
    _reconcile_mutation(output)
    path.write_text(dumps({'command': list(command)}) + '\n')
    try:
        result = _mutation(command)
    except _ClientLost:
        raise  # No daemon-completion claim; the next mutation must reconcile.
    except BaseException:
        path.unlink(missing_ok=True)  # The local client has drained normally.
        raise
    path.unlink()
    return result


def _reconcile_mutation(output):
    path = output / 'deployment/deployment-mutation.json'
    if not path.exists():
        return
    command = loads(path.read_text())['command']
    action = command[1]
    if action == 'exec':
        raise DeploymentError('pre-stop client was lost; hook completion is unresolved; resources are retained')
    if action not in {'stop', 'rm', 'network'}:
        raise DeploymentError('accepted deployment operation remains unresolved')
    network = action == 'network'
    ids = [value for value in command[3 if network else 2:] if value != '--force']
    while True:
        query = ['docker', 'network' if network else 'container', 'inspect', '--format', '{{json .}}', *ids]
        result = run(query, capture_output=True, text=True)
        objects = _startup_inspected_objects(result, allow_missing=True)
        complete = not objects if action != 'stop' else all(not obj['State'].get('Running') for obj in objects)
        if complete:
            path.unlink()
            return
        sleep(1.0)


def _reconcile_build(output, project):
    path = output / 'deployment/deployment-build-pending.json'
    if not path.exists():
        return
    pending = loads(path.read_text())
    if pending.get('version') == 2:
        _reconcile_image_batch(path, pending)
        return
    reference = pending['image']
    while True:
        result = run(['docker', 'image', 'inspect', reference], capture_output=True, text=True)
        if result.returncode:
            if 'no such' not in result.stderr.lower():
                raise DeploymentError(result.stderr)
        else:
            images = loads(result.stdout)
            if len(images) == 1 and images[0].get('Config', {}).get('Labels', {}).get('com.docker.compose.project') == project:
                if images[0]['Id'] == pending.get('previous_image_id'):
                    raise DeploymentError('build client was lost; the pre-existing image does not establish completion; build remains unresolved')
                path.unlink()
                return
            raise DeploymentError('completed image belongs to another deployment')
        sleep(1.0)


def _startup_command(command: Sequence[str], guard: _StartupMeasurements) -> None:
    started = monotonic()
    try:
        result = _mutation(command, display_output=True)
        guard.record('command', command=list(command), exit_code=result.returncode,
                     elapsed_seconds=monotonic() - started)
    except BaseException as error:
        guard.record('command', command=list(command), error=str(error),
                     elapsed_seconds=monotonic() - started)
        raise


class _SettledFailure(DeploymentError):
    """Accepted containers finished, with a real unsuccessful state."""


def _settle_services(project, services, names, *, require_all):
    """Wait for accepted daemon work; failures are terminal facts, not elapsed time."""
    while True:
        containers = _inspect_startup_services(project, services, names, allow_missing=True)
        pending = len(containers) != len(names) if require_all else False
        failure = None
        for container in containers:
            name = container['Config']['Labels']['com.docker.compose.service']
            state = container['State']
            if _startup_success(container, services[name]):
                continue
            if (state.get('OOMKilled') or container.get('RestartCount', 0)
                or state.get('Health', {}).get('Status') == 'unhealthy' or state.get('Error')
                or state['Status'] in ('dead', 'exited')):
                failure = _SettledFailure(f'unsuccessful container: {name}: {state}')
            else:
                pending = True
        if not pending:
            if failure is not None:
                raise failure
            return containers
        if failure is not None:
            # A terminal failure must not be hidden by another slow/unknown
            # container. This is not a settled failure: retain the admission
            # record so neither retry nor cleanup can duplicate outstanding work.
            raise DeploymentError(f'{failure}; other accepted daemon work remains unresolved') from failure
        sleep(1.0)


def _startup_names(project: str, services: dict, names: Sequence[str]) -> list[str]:
    return [services[name].get('container_name', f'{project}-{name}-1') for name in names]


def _inspect_startup_services(project: str, services: dict, names: Sequence[str],
                              *, allow_missing: bool = False,
                              guard: _StartupMeasurements | None = None) -> list[dict]:
    containers = []
    if names:
        batch = names
        command = ['docker', 'container', 'inspect', '--format', '{{json .}}',
                   *_startup_names(project, services, batch)]
        # One read-only identity inventory.
        result = run(command, capture_output=True, text=True)
        containers.extend(_startup_inspected_objects(result, allow_missing=allow_missing))
    if len(containers) != len(names) and not allow_missing:
        raise DeploymentError('incomplete bounded container inspection')
    seen_services = set()
    for container in containers:
        labels = container['Config']['Labels']
        if labels.get('com.docker.compose.project') != project or labels.get('com.docker.compose.service') not in names:
            raise DeploymentError('startup ownership mismatch')
        service = labels['com.docker.compose.service']
        if service in seen_services:
            raise DeploymentError('duplicate startup service observation')
        seen_services.add(service)
    if not allow_missing and seen_services != set(names):
        raise DeploymentError('startup service coverage differs')
    return containers


def _startup_inspected_objects(result: CompletedProcess, *, allow_missing: bool) -> list[dict]:
    errors = [line for line in result.stderr.splitlines() if line.strip()]
    missing_only = bool(errors) and all('no such' in line.casefold() for line in errors)
    if result.returncode and not (allow_missing and missing_only):
        raise DeploymentError(result.stderr)
    return [loads(line) for line in result.stdout.splitlines() if line.strip()]


_DEPLOYMENT_CONFIG_LABEL = 'org.skyseed.deployment.config-sha256'


def _deployment_service_digest(service: dict, image_id: str) -> str:
    return sha256(dumps({'service': service, 'image_id': image_id},
                        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _startup_success(container: dict, service: dict) -> bool:
    state = container['State']
    if state.get('OOMKilled') or container.get('RestartCount', 0):
        return False
    helper = isinstance(service.get('build'), dict) and str(service['build'].get('dockerfile', '')).startswith('dummies/')
    if helper:
        return state['Status'] == 'exited' and state['ExitCode'] == 0
    health = state.get('Health', {}).get('Status')
    return state['Status'] == 'running' and health in (None, 'healthy')


def _write_startup_group(path: Path, compose: dict, names: Sequence[str],
                         identities: dict[str, dict], digests: dict[str, str]) -> None:
    """Keep intra-group Compose semantics; satisfy earlier dependencies once."""
    services = compose['services']
    selected = {}
    for name in names:
        service = dict(services[name])
        dependencies = service.get('depends_on', {})
        dependencies = ({key: {'condition': 'service_started'} for key in dependencies}
                        if isinstance(dependencies, list) else dependencies)
        retained = {}
        for dependency, options in dependencies.items():
            if dependency in names:
                retained[dependency] = options
                continue
            container = identities.get(dependency)
            if container is None:
                raise DeploymentError('earlier group dependency is missing')
            state = container['State']
            condition = options.get('condition', 'service_started')
            valid = ((state['Status'] == 'running' or _startup_success(container, services[dependency])) if condition == 'service_started' else
                     state.get('Health', {}).get('Status') == 'healthy' if condition == 'service_healthy' else
                     state['Status'] == 'exited' and state['ExitCode'] == 0 if condition == 'service_completed_successfully' else False)
            if not valid:
                raise DeploymentError(f'unsatisfied group dependency: {name} -> {dependency} ({condition})')
        if retained:
            service['depends_on'] = retained
        else:
            service.pop('depends_on', None)
        for field in ('network_mode', 'ipc', 'pid'):
            value = service.get(field, '')
            if isinstance(value, str) and value.startswith('service:'):
                dependency = value.removeprefix('service:')
                if dependency not in names:
                    if dependency not in identities:
                        raise DeploymentError('namespace dependency is not materialized')
                    service[field] = 'container:' + identities[dependency]['Id']
        service['labels'] = {**service.get('labels', {}), _DEPLOYMENT_CONFIG_LABEL: digests[name]}
        selected[name] = service
    path.write_text(safe_dump({**compose, 'services': selected}, sort_keys=False))


def _controlled_startup(output: Path, project: str) -> None:
    """Deploy whole metadata groups; explicit re-entry reconciles confirmed work."""
    path = output / 'deployment/docker-compose.yml'
    _validate_generated_compose(path, project)
    compose = safe_load(path.read_text())
    services = compose['services']
    _reconcile_pending(output, project, services)
    groups = _startup_groups(services)
    parallel = _startup_parallel()
    guard = _StartupMeasurements(output / 'deployment/deployment-measurements.jsonl')
    compose_hash = sha256(path.read_bytes()).hexdigest()
    guard.record('configuration', project=project, mode='whole-group', parallel=parallel,
                 groups=[(g, len(n)) for g, n in groups], compose_sha256=compose_hash)
    refs = tuple(dict.fromkeys(s['image'].replace('${COMPOSE_PROJECT_NAME}', project) for s in services.values()))
    image_result = run(['docker', 'image', 'inspect', *refs], capture_output=True, text=True)
    if image_result.returncode:
        raise DeploymentError(image_result.stderr)
    images = loads(image_result.stdout)
    if len(images) != len(refs):
        raise DeploymentError('incomplete startup image inventory')
    image_ids = {ref: image['Id'] for ref, image in zip(refs, images)}
    digests = {name: _deployment_service_digest(service, image_ids[service['image'].replace('${COMPOSE_PROJECT_NAME}', project)])
               for name, service in services.items()}
    existing = _inspect_startup_services(project, services, tuple(services), allow_missing=True, guard=guard)
    identities = {c['Config']['Labels']['com.docker.compose.service']: c for c in existing}
    for name, container in identities.items():
        if (container['Image'] != image_ids[services[name]['image'].replace('${COMPOSE_PROJECT_NAME}', project)]
            or container['Config']['Labels'].get(_DEPLOYMENT_CONFIG_LABEL) != digests[name]):
            raise DeploymentError(f'existing deployment configuration/image identity differs: {name}')
    completed = []
    active_names = ()
    group = None
    try:
        # Reconcile the whole project before replacing any namespace owner.
        # HostConfig bindings address actual retained container IDs, not merely
        # the unchanged semantic service digest. Unverifiable bindings are stale.
        namespace_fields = {'network_mode': 'NetworkMode', 'ipc': 'IpcMode', 'pid': 'PidMode'}
        namespace_dependencies = {
            name: {field: service[field].removeprefix('service:')
                   for field in namespace_fields
                   if isinstance(service.get(field), str) and service[field].startswith('service:')}
            for name, service in services.items()}
        invalid = set(services) - identities.keys()
        invalid.update(name for name, container in identities.items()
                       if not _startup_success(container, services[name]))
        stale = set()
        for name, container in identities.items():
            for field, owner in namespace_dependencies[name].items():
                if (owner not in identities or container.get('HostConfig', {}).get(namespace_fields[field])
                        != 'container:' + identities[owner]['Id']):
                    stale.add(name)
        invalid.update(stale)
        while True:
            dependents = {name for name, dependencies in namespace_dependencies.items()
                          if any(owner in invalid for owner in dependencies.values())}
            if dependents <= invalid:
                break
            invalid.update(dependents)
        failed = invalid & identities.keys()
        if failed:
            group = 'deployment-reconciliation'
            active_names = tuple(sorted(failed))
            guard.record('retry-unsuccessful-containers', group=group,
                         stale_namespace_bindings=sorted(stale),
                         containers=[identities[name] for name in active_names])
            def retry_command(arguments):
                result = _accepted_mutation(arguments, output)
                if result.returncode:
                    raise DeploymentError(result.stderr)
                return result.stdout
            resolved = _resolved_stop_services(services, path, project, retry_command)
            while failed:
                needed = set().union(*(_compose_dependencies(services[name]) for name in failed))
                wave = sorted(failed - needed)
                if not wave:
                    raise DeploymentError('cyclic unsuccessful startup dependencies')
                for name in wave:
                    if not _disposable_network_service(resolved[name]):
                        identity = identities[name]['Id']
                        if identities[name]['State'].get('Running'):
                            for hook in _pre_stop_commands(resolved[name], identity):
                                retry_command(hook)
                        retry_command(['docker', 'stop', identity])
                retry_command(['docker', 'rm', '--force', *(identities[name]['Id'] for name in wave)])
                for name in wave:
                    del identities[name]
                failed.difference_update(wave)
        for ordinal, (group, names) in enumerate(groups):
            if not names:
                continue
            active_names = names
            begin = monotonic()
            guard.checkpoint()
            pending = tuple(name for name in names if name not in identities or not _startup_success(identities[name], services[name]))
            group_path = path.parent / f'skyseed-startup-group-{ordinal:03}.yml'
            _write_startup_group(group_path, compose, names, identities, digests)
            guard.record('group-start', group=group, count=len(names), pending=list(pending),
                         reused=[name for name in names if name not in pending], parallel=parallel,
                         input_sha256=sha256(group_path.read_bytes()).hexdigest())
            _console(f"Container startup {group}: {len(pending)} to start, {len(names)-len(pending)} confirmed reusable.")
            if pending:
                command = ['docker', 'compose', '--parallel', str(parallel), '-p', project,
                           '--project-directory', str(path.parent), '-f', str(group_path),
                           'up', '-d', '--no-build', '--no-recreate', *pending]
                journal = output / 'deployment/deployment-pending.json'
                journal.write_text(dumps({'services': list(pending), 'command': command}) + '\n')
                primary = None
                try:
                    _startup_command(command, guard)
                except BaseException as error:
                    primary = error
                if primary is not None and not isinstance(primary, KeyboardInterrupt):
                    if isinstance(primary, DeploymentError) and not isinstance(primary, _ClientLost):
                        # A normally drained, failed Compose command is settled.
                        # Missing/created containers are failed-attempt residue,
                        # not a reason to wait for successful service readiness.
                        try:
                            journal.write_text(dumps({'services': list(pending), 'command': command,
                                                      'command_failed': True}) + '\n')
                            journal.unlink()
                        except OSError as error:
                            primary.add_note(f'clearing completed admission failed: {error}')
                    # Lost clients (and unexpected executor errors) retain their
                    # admission record. Read-only observation below cannot turn
                    # an uncertain operation into permission for another mutation.
                    raise primary
                try:
                    containers = _settle_services(project, services, names,
                                                  require_all=True)
                    journal.unlink()
                except BaseException as error:
                    if primary is None:
                        primary = error
                    elif error is not primary:
                        primary.add_note(f'daemon reconciliation also failed: {error}')
                    if isinstance(error, _SettledFailure):
                        journal.unlink(missing_ok=True)
                if primary is not None:
                    raise primary
                identities.update({c['Config']['Labels']['com.docker.compose.service']: c for c in containers})
            completed.append(group)
            guard.record('group-complete', group=group, count=len(names), reused=len(names)-len(pending),
                         elapsed_seconds=monotonic()-begin,
                         containers=[{'id': identities[name]['Id'], 'service': name, 'state': identities[name]['State']}
                                     for name in names])
            guard.checkpoint()
            _console(f"Container startup {group} confirmed: {len(names)} services after {monotonic()-begin:.1f}s.")
        guard.record('deployment-complete', services=len(services), completed_groups=completed)
    except BaseException as error:
        partial = None
        observation_error = None
        background_work_unknown = True
        try:
            # Inventory is read-only; a client exit is not proof of daemon completion.
            partial = _inspect_startup_services(project, services, active_names, allow_missing=True, guard=guard)
            background_work_unknown = any(
                (output / 'deployment' / name).exists() for name in (
                    'deployment-pending.json', 'deployment-mutation.json', 'deployment-build-pending.json'))
        except BaseException as inspection_error:
            observation_error = str(inspection_error)
            error.add_note(f'deployment observation also failed: {inspection_error}')
        guard.record('admission-stopped', error=str(error), failing_group=group,
                     completed_groups=completed, background_work_unknown=background_work_unknown,
                     partial_state=partial, observation_error=observation_error,
                     confirmed_groups_preserved=True)
        raise


def _prepare_controlled_image_compose(output: Path, project: str) -> Path:
    """Derive image-only Compose input; service admission keeps the full source.

    Build/config inspection must not parse thousands of runtime services in one
    Compose invocation. Full service fields are validated by each existing
    bounded startup invocation, with dependencies owned by _startup_groups.
    """
    source = output / "deployment/docker-compose.yml"
    _validate_generated_compose(source, project)
    compose = safe_load(source.read_text())
    services = compose["services"]
    selected = {}
    images = set()
    for name, service in services.items():
        image = service.get("image")
        if not isinstance(image, str) or not image:
            raise DeploymentError(f"generated service lacks image: {name}")
        if "build" in service:
            selected[name] = {key: service[key] for key in
                              ("build", "image", "platform", "pull_policy") if key in service}
            images.add(image)
    for name, service in services.items():
        if service["image"] not in images:
            selected[name] = {"image": service["image"]}
            images.add(service["image"])
    path = source.parent / "skyseed-image-compose.yml"
    path.write_text(safe_dump({**compose, "services": selected}, sort_keys=False))
    return path


def _controlled_teardown(output: Path, project: str) -> None:
    """Remove verified project resources with bounded dependency-ordered requests."""
    path = output / 'deployment/docker-compose.yml'
    _validate_generated_compose(path, project)
    compose = safe_load(path.read_text())
    services = compose.get('services', {})
    _reconcile_pending(output, project, services)
    guard = _StartupMeasurements(output / 'deployment/teardown-measurements.jsonl')

    def command(arguments: Sequence[str]) -> str:
        started = monotonic()
        try:
            result = (_accepted_mutation(arguments, output) if arguments[1] in {"stop", "rm", "exec"} or arguments[1:3] == ["network", "rm"]
                      else run(arguments, capture_output=True, text=True))
            if result.returncode:
                raise DeploymentError(result.stderr or result.stdout)
            return result.stdout
        except BaseException as error:
            guard.record('teardown-failure', command=list(arguments),
                         error_type=type(error).__name__, error=str(error))
            raise
        finally:
            guard.record('teardown-command', command=list(arguments),
                         elapsed_seconds=monotonic() - started)

    def inventory(kind: str) -> list[dict]:
        # Full immutable IDs and exact project labels prevent name reuse from
        # redirecting a later removal. No host-wide prune or name-prefix match.
        fields = '{{json .ID}} {{json (.Label "com.docker.compose.project")}}'
        if kind == 'container':
            fields += ' {{json (.Label "com.docker.compose.service")}}'
            args = ['docker', 'ps', '--all', '--no-trunc']
        else:
            fields += ' {{json (.Label "com.docker.compose.network")}}'
            args = ['docker', 'network', 'ls', '--no-trunc']
        raw = command([*args, '--filter', f'label=com.docker.compose.project={project}',
                       '--format', fields])
        rows = []
        for line in raw.splitlines():
            # Docker label/service identifiers cannot contain whitespace; JSON
            # decoding still rejects malformed output and absent project labels.
            identity, owner, key = (loads(value) for value in line.split())
            if owner != project or not isinstance(identity, str) or not identity:
                raise DeploymentError('teardown ownership mismatch')
            rows.append({'id': identity, 'key': key})
        if len({row['id'] for row in rows}) != len(rows):
            raise DeploymentError('duplicate teardown resource identity')
        return rows

    services = _resolved_stop_services(services, path, project, command)
    dependencies = {name: _compose_dependencies(service) for name, service in services.items()}
    if any(deps - services.keys() for deps in dependencies.values()):
        raise DeploymentError('unknown generated teardown dependency')
    groups = list(reversed(_startup_groups(services)))
    containers = inventory('container')
    guard.record('teardown-start', project=project, mode='whole-group', native_rm_parallel=50,
                 containers=len(containers), compose_sha256=sha256(path.read_bytes()).hexdigest())
    by_service: dict[str, list[str]] = {}
    for row in containers:
        by_service.setdefault(row['key'], []).append(row['id'])
    orphans = sorted(set(by_service) - services.keys())
    if orphans:
        groups.insert(0, ('orphaned-project-services', tuple(orphans)))
    remaining_count = len(containers)
    _console(f"Deployment cleanup inventory: {remaining_count} owned containers; configured graceful stops and hooks are preserved.")
    for group, names in groups:
        started = monotonic()
        pending = set(names) & by_service.keys()
        guard.record('teardown-group-start', group=group, containers=sum(len(by_service[n]) for n in pending))
        while pending:
            # Only real within-group dependencies split a removal, never counts.
            needed = set().union(*(dependencies.get(name, set()) for name in pending))
            wave = sorted(pending - needed)
            if not wave:
                raise DeploymentError('cyclic generated teardown dependencies')
            batch = [(identity, name) for name in wave for identity in by_service[name]]
            graceful = [(identity, name) for identity, name in batch
                        if not _disposable_network_service(services.get(name, {}))]
            stopping = monotonic()
            if graceful:
                _console(f"Deployment cleanup {group}: stopping {len(graceful)} containers gracefully, including configured hooks.")
            for identity, name in graceful:
                hooks = _pre_stop_commands(services.get(name, {}), identity)
                if hooks:
                    state = loads(command(['docker', 'inspect', '--format', '{{json .State}}', identity]))
                    if state.get('Running'):
                        for hook in hooks:
                            command(hook)
            if graceful:
                # Docker retains each container's configured signal and grace period.
                command(['docker', 'stop', *(identity for identity, _ in graceful)])
                _console(f"Deployment cleanup {group}: graceful stop succeeded for {len(graceful)} containers after {monotonic()-stopping:.1f}s.")
            ids = [identity for identity, _ in batch]
            # CLI 29.6.2 parallelOperation supplies the native limit of 50.
            # All graceful services have stopped successfully before force removal.
            removing = monotonic()
            _console(f"Deployment cleanup {group}: removing {len(ids)} containers.")
            command(['docker', 'rm', '--force', *ids])
            _console(f"Deployment cleanup {group}: removal succeeded for {len(ids)} containers after {monotonic()-removing:.1f}s.")
            remaining_count -= len(ids)
            pending.difference_update(wave)
            guard.record('teardown-group-removal', group=group, container_ids=ids,
                         remaining=remaining_count, graceful=len(graceful), native_rm_parallel=50)
        guard.record('teardown-group-complete', group=group, elapsed_seconds=monotonic()-started)
        guard.checkpoint()
    if inventory('container'):
        raise DeploymentError('project containers remain after teardown')
    external = {name for name, network in compose.get('networks', {}).items()
                if network.get('external')}
    networks = [row['id'] for row in inventory('network') if row['key'] not in external]
    removing = monotonic()
    _console(f"Deployment cleanup: removing {len(networks)} owned networks.")
    for offset in range(0, len(networks), 8):
        command(['docker', 'network', 'rm', *networks[offset:offset + 8]])
    if any(row['key'] not in external for row in inventory('network')):
        raise DeploymentError('project networks remain after teardown')
    guard.record('teardown-complete', project=project, containers=len(containers), networks=len(networks))
    _console(f"Deployment cleanup verified: no owned containers or non-external networks remain; network stage {monotonic()-removing:.1f}s.")


def _validate_generated_compose(compose_file: Path, project_name: str) -> None:
    if not compose_file.is_file():
        raise ValueError(
            "output_directory must contain deployment/docker-compose.yml"
        )
    if not isinstance(project_name, str) or fullmatch(
        r"[a-z0-9][a-z0-9_-]*", project_name
    ) is None:
        raise ValueError(
            "project_name must contain only lowercase letters, digits, "
            "hyphens and underscores"
        )


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)




def _reconcile_pending(output, project, services):
    _reconcile_build(output, project)
    _reconcile_mutation(output)
    journal = output / 'deployment/deployment-pending.json'
    if not journal.exists():
        return
    pending = loads(journal.read_text())
    if pending.get('command_failed') is True:
        # The client drained with a known failure, even if removing its local
        # record failed. Residual containers need replacement/removal, not readiness.
        journal.unlink()
        return
    try:
        _settle_services(project, services, tuple(pending['services']), require_all=True)
    except _SettledFailure:
        # All accepted containers reached terminal states; explicit retry may replace failed ones.
        journal.unlink()
        return
    journal.unlink()


def _deployment_operation(method):
    """One local deployment mutator, with no lease or heartbeat."""
    @wraps(method)
    def operation(self, *args, **kwargs):
        path = self.output / 'deployment/deployment-operation.lock'
        with path.open('a+') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise DeploymentError('deployment operation is already in progress') from error
            return method(self, *args, **kwargs)
    return operation


_BUILD_KEY = 'org.skyseed.build.key'


def _inspect_build_images(references):
    references = tuple(dict.fromkeys(references))
    if not references:
        return {}
    result = run(['docker', 'image', 'inspect', *references], capture_output=True, text=True)
    errors = result.stderr.splitlines()
    if result.returncode and (not errors or any('no such' not in e.lower() for e in errors if e.strip())):
        raise DeploymentError(result.stderr)
    # Docker returns only existing objects when some references are missing.
    images = loads(result.stdout or '[]')
    result = {}
    for image in images:
        for ref in (*(image.get('RepoTags') or ()), *(image.get('RepoDigests') or ()), image['Id']):
            result[ref] = image
    # Single-reference callers also support untagged images and canonicalized tags.
    if len(references) == 1 and len(images) == 1:
        result[references[0]] = images[0]
    return result


def _matches_build_image(image, key):
    return image is not None and (image.get('Config', {}).get('Labels') or {}).get(_BUILD_KEY) == key


def _context_digest(context):
    digest = sha256()
    for path in sorted(context.rglob('*'), key=lambda p: p.relative_to(context).as_posix()):
        if path.is_symlink():
            raise DeploymentError('generated build context must not contain symlinks')
        name = path.relative_to(context).as_posix().encode()
        data = path.read_bytes() if path.is_file() else b''
        digest.update(path.stat().st_mode.to_bytes(4, 'big'))
        digest.update(len(name).to_bytes(4, 'big')); digest.update(name)
        digest.update(len(data).to_bytes(8, 'big')); digest.update(data)
    return digest.hexdigest()


def _reconcile_image_batch(path, pending):
    if pending.get('command_failed'):
        path.unlink()
        return
    while True:
        images = _inspect_build_images(row['image'] for row in pending['images'])
        complete = True
        for row in pending['images']:
            image = images.get(row['image']) or images.get(row['image'] + ':latest')
            if image is None:
                complete = False
                continue
            if image['Id'] == row.get('previous_image_id') or (
                    row.get('key') is not None and not _matches_build_image(image, row['key'])):
                raise DeploymentError('build client was lost; the existing image does not establish completion; batch remains unresolved')
        if complete:
            path.unlink()
            return
        sleep(1.0)


def _accepted_build(command, rows, output, guard):
    path = output / 'deployment/deployment-build-pending.json'
    path.write_text(dumps({'version': 2, 'images': rows, 'command': command}) + '\n')
    try:
        _startup_command(command, guard)
    except _ClientLost:
        raise
    except BaseException as error:
        try:
            path.write_text(dumps({'version': 2, 'images': rows, 'command': command,
                                   'command_failed': True}) + '\n')
            path.unlink(missing_ok=True)
        except OSError as cleanup:
            error.add_note(f'build journal cleanup failed: {cleanup}')
        raise
    path.unlink()


def _build_images(owner):
    output, project = owner.output, owner.project_name
    started = monotonic()
    _console(f"Image preparation starting for deployment {project}; checking reusable image identities.")
    runtime = output / 'deployment'
    services = safe_load((runtime / 'docker-compose.yml').read_text())['services']
    _reconcile_pending(output, project, services)
    path = _prepare_controlled_image_compose(output, project)
    configuration = safe_load(path.read_text())
    selected = configuration['services']
    parallel = _startup_parallel()
    guard = _StartupMeasurements(runtime / 'deployment-measurements.jsonl')
    compose = ['docker', 'compose', '--parallel', str(parallel), '-p', project, '-f', str(path)]
    prerequisites, groups = [], []
    for line in (runtime / 'skyseed-build-plan.txt').read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        fields = line.split()
        if len(fields) == 2 and fields[0] == 'prerequisite':
            prerequisites.append(fields[1])
        elif len(fields) == 3 and fields[0] == 'build':
            groups.append(fields[1])
        else:
            raise DeploymentError('invalid generated build plan')
    names = prerequisites + groups
    if len(names) != len(set(names)) or set(names) != {n for n, s in selected.items() if 'build' in s}:
        raise DeploymentError('generated build plan coverage differs')
    references = {n: s['image'].replace('${COMPOSE_PROJECT_NAME}', project) for n, s in selected.items()}
    # Resolve Compose interpolation in build options once, before fingerprinting.
    if names:
        resolved = run([*compose, 'config', '--format', 'json'], capture_output=True, text=True)
        if resolved.returncode:
            raise DeploymentError(resolved.stderr)
        resolved = loads(resolved.stdout)['services']
    else:
        resolved = {}
    recipes, bases = {}, {}
    for name in names:
        service = resolved[name]
        build = deepcopy(service['build'])
        context = Path(build.pop('context')).resolve()
        dockerfile = build.pop('dockerfile', 'Dockerfile')
        source = context / dockerfile
        text = source.read_text()
        mutable_context = False
        if name in prerequisites:
            # SEED's base aliases contain only FROM and need no surrounding node
            # contexts. Stage just that Dockerfile so Map changes cannot invalidate
            # every router's prerequisite or upload the whole generated output.
            instructions = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
            if len(instructions) == 1 and instructions[0].strip().upper().startswith('FROM '):
                context = runtime / 'skyseed-prerequisites' / name
                context.mkdir(parents=True, exist_ok=True)
                (context / 'Dockerfile').write_text(text)
                dockerfile = 'Dockerfile'
            else:
                # The ordinary SEED root context also contains deployment
                # bookkeeping. Do not fingerprint it while builds write there.
                mutable_context = True
        discovered = _external_images(text)
        cacheable = not mutable_context and discovered is not None and not any(build.get(k) for k in ('additional_contexts', 'secrets', 'ssh'))
        try:
            digest = _context_digest(context) if cacheable else None
        except DeploymentError:
            cacheable, digest = False, None
        bases[name] = discovered or ()
        recipes[name] = {'context': str(context), 'dockerfile': str(dockerfile),
                         'options': build, 'digest': digest, 'cacheable': cacheable,
                         'dockerfile_digest': sha256(text.encode()).hexdigest(),
                         'platform': service.get('platform', machine())}
    # FROM prerequisites form the build DAG, independent of runtime depends_on.
    aliases = {name: name for name in prerequisites}
    aliases.update({references[name]: name for name in prerequisites})
    dependencies = {n: {aliases[ref] for ref in bases[n] if ref in aliases} for n in names}
    # Opaque node instructions can still use any generated SEED base alias.
    # Build those first and expose their immutable images to Docker itself.
    for name in names:
        if not recipes[name]['cacheable']:
            bases[name] = tuple(ref for ref in bases[name] if ref in aliases)
            if name in groups:
                bases[name] = tuple(dict.fromkeys((*bases[name], *prerequisites)))
                dependencies[name].update(prerequisites)
    refresh, visited = set(), set()
    pending_refresh = [n for n in names if recipes[n]['options'].get('pull')]
    while pending_refresh:
        name = pending_refresh.pop()
        if name in visited:
            continue
        visited.add(name)
        pending_refresh.extend(dependencies[name])
        refresh.update(ref for ref in bases[name] if ref not in aliases)
    completed, base_images, protected = set(), {}, False

    def mutation_allowed():
        nonlocal protected
        if not protected:
            existing = run(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}'], capture_output=True, text=True)
            if existing.returncode or existing.stdout.strip():
                raise DeploymentError('required images are missing or stale while project containers exist or cannot be inspected')
            protected = True

    while len(completed) < len(names):
        # Finish all prerequisites before independent node/Map groups.
        pending = [n for n in (prerequisites if set(prerequisites) - completed else groups) if n not in completed]
        ready = [n for n in pending if dependencies[n] <= completed]
        if not ready:
            raise DeploymentError('cyclic generated build prerequisites')
        external = {ref for n in ready for ref in bases[n] if ref not in aliases}
        for ref in sorted(external - base_images.keys()):
            images = _inspect_build_images((ref,))
            if ref not in images or ref in refresh:
                mutation_allowed()
                _console(f"Image preparation: pulling {ref}.")
                _accepted_build(['docker', 'pull', ref], [{'image': ref,
                    'previous_image_id': (images.get(ref) or {}).get('Id')}], output, guard)
                images = _inspect_build_images((ref,))
            base_images[ref] = images[ref]
        keys, pinned = {}, {}
        for name in ready:
            inputs = {ref: (base_images[aliases[ref]] if ref in aliases else base_images[ref]) for ref in bases[name]}
            recipe = recipes[name]
            material = {'version': 1, 'context': recipe['digest'], 'dockerfile': recipe['dockerfile'],
                        'dockerfile_content': recipe['dockerfile_digest'],
                        'options': recipe['options'], 'platform': recipe['platform'],
                        'bases': {ref: image['Id'] for ref, image in inputs.items()}}
            keys[name] = (sha256(dumps(material, sort_keys=True).encode()).hexdigest()
                          if recipe['cacheable'] else uuid4().hex)
            pinned[name] = inputs
        cache = {n: 'skyseed-build:' + keys[n] for n in ready}
        inventory = _inspect_build_images([*(references[n] for n in ready), *cache.values()])
        todo = []
        for name in ready:
            current = inventory.get(references[name]) or inventory.get(references[name] + ':latest')
            shared = inventory.get(cache[name])
            force = not recipes[name]['cacheable'] or recipes[name]['options'].get('no_cache', False)
            if not force and _matches_build_image(current, keys[name]):
                state = 'project-image'
            elif not force and _matches_build_image(shared, keys[name]):
                mutation_allowed()
                _startup_command(['docker', 'image', 'tag', shared['Id'], references[name]], guard)
                current, state = shared, 'shared-image'
            else:
                mutation_allowed()
                todo.append(name)
                state = 'build'
            guard.record('image-decision', service=name, key=keys[name], source=state,
                         image=references[name], cacheable=recipes[name]['cacheable'],
                         bases={r: i['Id'] for r, i in pinned[name].items()})
            if state != 'build':
                base_images[name] = current
        _console(f"Image preparation: {len(ready)-len(todo)} reusable groups, {len(todo)} groups to build.")
        for offset in range(0, len(todo), parallel):
            batch = todo[offset:offset + parallel]
            # Named contexts pin FROM/COPY inputs to the identities in the key.
            # Docker tags are content-addressed; projects never share containers.
            for name in batch:
                contexts = {}
                for ref, image in pinned[name].items():
                    immutable = 'skyseed-base:' + image['Id'].removeprefix('sha256:')
                    if immutable not in base_images:
                        _startup_command(['docker', 'image', 'tag', image['Id'], immutable], guard)
                        base_images[immutable] = image
                    contexts[ref] = 'docker-image://' + immutable
                recipe = recipes[name]
                options = deepcopy(recipe['options'])
                # External bases were refreshed above; pinned local aliases must
                # not be pulled from a registry by Compose.
                if recipe['cacheable']:
                    options.pop('pull', None)
                options.update(context=recipe['context'], dockerfile=recipe['dockerfile'])
                options['labels'] = {**options.get('labels', {}), _BUILD_KEY: keys[name]}
                options['tags'] = [*options.get('tags', ()), *([cache[name]] if recipe['cacheable'] else [])]
                if contexts:
                    original = options.get('additional_contexts', {})
                    if isinstance(original, list):
                        original = dict(item.split('=', 1) for item in original)
                    options['additional_contexts'] = {**contexts, **original}
                selected[name]['build'] = options
            path.write_text(safe_dump({**configuration, 'services': {n: selected[n] for n in batch}}, sort_keys=False))
            rows = [{'image': references[n], 'key': keys[n],
                     'previous_image_id': (inventory.get(references[n]) or inventory.get(references[n] + ':latest') or {}).get('Id')} for n in batch]
            _accepted_build([*compose, 'build', *batch], rows, output, guard)
            built = _inspect_build_images(references[n] for n in batch)
            for name in batch:
                image = built.get(references[name]) or built.get(references[name] + ':latest')
                if not _matches_build_image(image, keys[name]):
                    raise DeploymentError('generated build did not produce its declared image key')
                base_images[name] = image
        completed.update(ready)
    # Non-built services (for example a published optional Tool) still need images.
    for ref in set(references.values()) - {references[n] for n in names}:
        if not _inspect_build_images((ref,)):
            raise DeploymentError(f'required deployment image is missing: {ref}')
    guard.record('image-build-complete', groups=len(names), parallel=parallel)
    _console(f"Image preparation succeeded: {len(references)} image groups checked after {monotonic()-started:.1f}s.")


class Deployer:
    """Prepare or operate a generated deployment, independent of computation.

    Layer: deployment. Public because generated controls use this owner; it is
    not a Simulator, Producer or Runner. The experiment calls prepare once with its authored Emulator. Generated
    scripts construct Deployer from an output path for build/start/status/cleanup.
    """

    def __init__(self, output: Path):
        self.output = Path(output).resolve()

    def prepare(self, emulator, *, compiler=None, internet_map_enabled=True, prefix="skyseed_v2",
                python_executable=None, deploy_parallel=None):
        from uuid import uuid4
        from .seed import _InterfaceLessRuntimeDocker
        from .metadata import _write_compose_build_plan
        compiler = compiler if compiler is not None else _InterfaceLessRuntimeDocker(internet_map_enabled=internet_map_enabled)
        project = sub(r'[^a-z0-9_-]+', '_', prefix.lower()) + '-' + uuid4().hex[:16]
        emulator.render()
        emulator.compile(compiler, str(self.output / 'deployment'))
        (self.output / 'deployment/compose-project-name').write_text(project + '\n')
        if internet_map_enabled:
            from .seed import _prepare_internet_map
            _prepare_internet_map(self.output)
        _write_compose_build_plan(self.output / 'deployment')
        _write_deployment_scripts(self.output, python_executable or sys.executable, deploy_parallel)
        return project

    @property
    def project_name(self):
        return (self.output / 'deployment/compose-project-name').read_text().strip()

    def _build(self):
        _build_images(self)

    @_deployment_operation
    def build(self):
        self._build()

    @_deployment_operation
    def start(self):
        started = monotonic()
        _console(f"Deployment starting: {self.output}")
        summary = self.output / 'deployment/deployment-startup.json'
        error = None
        try:
            # An optional failed write must not leave the last operation's
            # successful summary posing as evidence for this attempt.
            try:
                summary.unlink(missing_ok=True)
            except Exception as recording_error:
                _startup_recording_error(recording_error)
            self._build()
            _controlled_startup(self.output, self.project_name)
            _console(f"Deployment startup succeeded: all service groups confirmed after {monotonic()-started:.1f}s. Start runtime with run.sh.")
        except BaseException as failure:
            error = failure
            _console(f"Deployment startup failed: {type(failure).__name__}: {failure}. Inspect retained deployment evidence before retrying.")
            raise
        finally:
            record = {'artifact_type': 'compose-startup-evidence', 'format_version': 1,
                      'status': 'completed' if error is None else 'failed',
                      'duration_seconds': monotonic() - started,
                      'exit_code': 0 if error is None else 130 if isinstance(error, KeyboardInterrupt) else 1}
            if error is not None:
                record.update(error=str(error), error_type=type(error).__name__)
            try:
                summary.write_text(dumps(record) + '\n', encoding='utf-8')
            except Exception as recording_error:
                _startup_recording_error(recording_error)

    def status(self):
        compose = safe_load((self.output / 'deployment/docker-compose.yml').read_text())
        return {'pending_operation': any((self.output / 'deployment' / name).exists() for name in (
                    'deployment-pending.json', 'deployment-mutation.json', 'deployment-build-pending.json')),
                'containers': _inspect_startup_services(self.project_name, compose['services'], tuple(compose['services']), allow_missing=True)}

    @_deployment_operation
    def cleanup(self):
        _controlled_teardown(self.output, self.project_name)


def _startup_recording_error(error):
    try:
        print(f'deployment startup recording unavailable: {error}', file=sys.stderr)
    except Exception:
        pass


def _write_deployment_scripts(output, python_executable, deploy_parallel):
    environment = '' if deploy_parallel is None else f'export SKYSEED_DEPLOY_PARALLEL="${{SKYSEED_DEPLOY_PARALLEL:-{_startup_parallel(deploy_parallel)}}}"\n'
    for script, operation in (('deploy.sh', 'start'), ('cleanup.sh', 'cleanup')):
        _write_executable(output / script, '#!/usr/bin/env bash\nset -euo pipefail\n'
            'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"\n'
            + environment + f'exec {quote(python_executable)} -m skyseed.deployment "$SCRIPT_DIR" {operation} "$@"\n')
