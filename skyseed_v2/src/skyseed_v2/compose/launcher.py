"""Launch and verify one already-generated Compose project."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from json import JSONDecodeError, dumps, loads
from math import isfinite
from pathlib import Path
from re import fullmatch, sub
from shlex import quote, split
import shutil
import os
import signal
import sys
from subprocess import CompletedProcess, Popen, TimeoutExpired, run
from time import monotonic, sleep, time
from typing import Callable, Iterable, Sequence

from seedemu.compiler import Docker
from yaml import safe_dump, safe_load


Command = tuple[str, ...]
CommandRunner = Callable[[Sequence[str]], CompletedProcess[str]]


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
            'pressure': pressure, 'pswpin': int(vm['pswpin']),
            'pswpout': int(vm['pswpout']), 'service_cgroups': scopes}


class _StartupMeasurements:
    """Lightweight deployment checkpoints, with no admission or abort policy."""

    def __init__(self, path: Path):
        self.path = path

    def record(self, kind: str, **fields) -> None:
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(dumps({'kind': kind, 'monotonic': monotonic(), **fields}) + '\n')

    def checkpoint(self):
        try:
            sample = _host_startup_sample()
            self.record('host', sample=sample)
            return sample['memory']['MemAvailable']
        except OSError as error:
            self.record('measurement-unavailable', error=str(error))
            return None


def _deployment_bounds(phase: str, resident: int) -> tuple[int, int]:
    """Bound services per invocation separately from concurrent daemon requests.

    Startup is count-independent; the cleanup threshold is an engineering
    default, not pressure admission or an optimum. Explicit values are ceilings.
    """
    if phase not in {'startup', 'cleanup'}:
        raise ValueError('unknown deployment policy phase')
    prefix = 'SKYSEED_DEPLOY' if phase == 'startup' else 'SKYSEED_CLEANUP'
    batch = int(os.environ.get(prefix + '_BATCH_SIZE', '64' if phase == 'startup' else '16'))
    parallel = int(os.environ.get(prefix + '_PARALLEL', '8')) if phase == 'startup' else batch
    threshold = int(os.environ.get('SKYSEED_DEPLOY_RESIDENT_STEP', '2000')) if phase == 'cleanup' else 1
    if not 1 <= parallel <= batch <= 64 or threshold < 1:
        raise ValueError('deployment policy requires 1 <= parallel <= batch size <= 64 and positive resident step')
    if phase == 'cleanup' and resident >= threshold:
        batch, parallel = max(1, batch // 2), max(1, parallel // 2)
    return batch, parallel


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
        raise ComposeMaterializationError('resolved hook project identity differs')
    result = dict(services)
    for name in hooked:
        value = resolved.get('services', {}).get(name)
        if not value or not value.get('pre_stop'):
            raise ComposeMaterializationError('resolved service stop hook missing')
        result[name] = {**services[name], 'pre_stop': value['pre_stop']}
    return result


def _pre_stop_commands(service: dict, identity: str) -> list[list[str]]:
    """Preserve Compose hook options while addressing the saved container ID."""
    result = []
    for hook in service.get('pre_stop', ()):
        if set(hook) - {'command', 'user', 'privileged', 'working_dir', 'environment'}:
            raise ComposeMaterializationError('unsupported generated pre_stop option')
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
            raise ComposeMaterializationError('empty generated pre_stop command')
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
            raise ComposeMaterializationError(f'missing generated Shell identity: {name}')
        membership[name] = group
        by_group.setdefault(group, []).append(name)
    ordered = ['shared', *sorted(set(by_group) - {'shared'})]
    rank = {group: i for i, group in enumerate(ordered)}
    result = []
    completed: set[str] = set()
    for group in ordered:
        pending = set(by_group[group])
        output = []
        while pending:
            ready = []
            for name in sorted(pending):
                dependencies = _compose_dependencies(services[name])
                for dependency in dependencies:
                    if dependency not in membership or rank[membership[dependency]] > rank[group]:
                        raise ComposeMaterializationError(f'dependency crosses a later Shell: {name} -> {dependency}')
                if set(dependencies) <= completed:
                    ready.append(name)
            if not ready:
                raise ComposeMaterializationError('cyclic generated Compose dependencies')
            output.extend(ready)
            completed.update(ready)
            pending.difference_update(ready)
        result.append((group, tuple(output)))
    return result


def _bounded_startup_command(command: Sequence[str], guard: _StartupMeasurements,
                             ) -> None:
    started = monotonic()
    process = Popen(command, start_new_session=True)
    try:
        process.wait()
        if process.returncode:
            raise ComposeMaterializationError(f'startup command failed: {process.returncode}')
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        guard.record('command', command=list(command), exit_code=process.returncode,
                     elapsed_seconds=monotonic() - started)
    # Caller owns and observes all admitted services, including daemon work after client exit.


def _startup_names(project: str, services: dict, names: Sequence[str]) -> list[str]:
    return [services[name].get('container_name', f'{project}-{name}-1') for name in names]


def _write_startup_batch(path: Path, compose: dict, names: Sequence[str],
                         identities: dict[str, str] | None = None) -> None:
    """Derive only admitted services; preserve original project/network identity."""
    selected = {name: {key: value for key, value in compose['services'][name].items()
                       if key != 'depends_on'} for name in names}
    for service in selected.values():
        for field in ('network_mode', 'ipc', 'pid'):
            value = service.get(field, '')
            if isinstance(value, str) and value.startswith('service:'):
                target = (identities or {}).get(value.removeprefix('service:'))
                if not target:
                    raise ComposeMaterializationError('namespace dependency is not materialized')
                service[field] = 'container:' + target
    path.write_text(safe_dump({**compose, 'services': selected}, sort_keys=False))


def _inspect_startup_services(project: str, services: dict, names: Sequence[str],
                              *, allow_missing: bool = False,
                              guard: _StartupMeasurements | None = None) -> list[dict]:
    containers = []
    for offset in range(0, len(names), 8):
        batch = names[offset:offset + 8]
        command = ['docker', 'container', 'inspect', '--format', '{{json .}}',
                   *_startup_names(project, services, batch)]
        # A query failure pauses admission; it is not evidence of host capacity.
        for attempt in range(3):
            try:
                result = run(command, capture_output=True, text=True, timeout=10)
                break
            except TimeoutExpired:
                if guard is None or attempt == 2:
                    raise
                guard.record('docker-observation-retry', services=list(batch), attempt=attempt + 1)
                sleep(2)
        containers.extend(_startup_inspected_objects(result, allow_missing=allow_missing))
    if len(containers) != len(names) and not allow_missing:
        raise ComposeMaterializationError('incomplete bounded container inspection')
    seen_services = set()
    for container in containers:
        labels = container['Config']['Labels']
        if labels.get('com.docker.compose.project') != project or labels.get('com.docker.compose.service') not in names:
            raise ComposeMaterializationError('startup ownership mismatch')
        service = labels['com.docker.compose.service']
        if service in seen_services:
            raise ComposeMaterializationError('duplicate startup service observation')
        seen_services.add(service)
    if not allow_missing and seen_services != set(names):
        raise ComposeMaterializationError('startup service coverage differs')
    return containers


def _startup_inspected_objects(result: CompletedProcess, *, allow_missing: bool) -> list[dict]:
    errors = [line for line in result.stderr.splitlines() if line.strip()]
    missing_only = bool(errors) and all('no such' in line.casefold() for line in errors)
    if result.returncode and not (allow_missing and missing_only):
        raise ComposeMaterializationError(result.stderr)
    return [loads(line) for line in result.stdout.splitlines() if line.strip()]


def _controlled_startup_cleanup(project: str, services: dict, admitted: Sequence[str],
                                guard: _StartupMeasurements, *, compose_path: Path | None = None) -> None:
    """Stop daemon-owned bounded work, then remove only verified project containers."""
    def command(arguments):
        result = run(arguments, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise ComposeMaterializationError(result.stderr)
        return result.stdout
    if compose_path is not None:
        services = _resolved_stop_services(services, compose_path, project, command)
    pending = set(admitted)
    waves = []
    while pending:
        needed = set().union(*(_compose_dependencies(services[name]) for name in pending))
        ready = sorted(pending - needed)
        if not ready:
            raise ComposeMaterializationError('cyclic startup rollback dependencies')
        waves.append(ready)
        pending.difference_update(ready)
    for wave in waves:
        for offset in range(0, len(wave), 8):
            names = wave[offset:offset + 8]
            try:
                containers = _inspect_startup_services(project, services, names, allow_missing=True)
                ids = [c['Id'] for c in containers]
                if not ids:
                    continue
                for container in containers:
                    name = container['Config']['Labels']['com.docker.compose.service']
                    service = services[name]
                    if any(key in service for key in ('pre_stop', 'stop_signal', 'stop_grace_period')):
                        if container['State'].get('Running'):
                            for hook in _pre_stop_commands(service, container['Id']):
                                command(hook)
                        command(['docker', 'stop', container['Id']])
                # At most eight daemon removals, including in-flight starts.
                command(['docker', 'rm', '--force', *ids])
                result = run(['docker', 'container', 'inspect', '--format', '{{json .}}', *ids],
                             capture_output=True, text=True, timeout=10)
                if _startup_inspected_objects(result, allow_missing=True):
                    raise ComposeMaterializationError('container removal not verified')
                guard.record('cleanup-containers', container_ids=ids, verified_absent=True)
            except Exception as error:
                guard.record('cleanup-failure', names=list(names), error=str(error))
                # Do not remove a dependency after its dependent failed cleanup.
                raise ComposeMaterializationError('scoped cleanup incomplete; remaining ownership recorded') from error


def _controlled_startup(output: Path, project: str) -> None:
    """Generated startup entry under the existing Compose deployment owner."""
    path = output / 'runtime/docker-compose.yml'
    _validate_generated_compose(path, project)
    compose = safe_load(path.read_text())
    services = compose['services']
    groups = _startup_groups(services)
    batch_size, parallel = _deployment_bounds('startup', 0)
    guard = _StartupMeasurements(output / 'runtime/deployment-measurements.jsonl')
    guard.record('configuration', project=project, batch_size=batch_size, parallel=parallel,
                 groups=[(g, len(n)) for g, n in groups],
                 compose_sha256=sha256(path.read_bytes()).hexdigest())
    existing = _inspect_startup_services(project, services, tuple(services), allow_missing=True, guard=guard)
    identities = {c['Config']['Labels']['com.docker.compose.service']: c['Id'] for c in existing}
    resident = {c['Id'] for c in existing if c['State']['Running']}
    admitted: list[str] = []
    complete: set[str] = set()
    helpers = {name for name, s in services.items()
               if isinstance(s.get('build'), dict) and str(s['build'].get('dockerfile', '')).startswith('dummies/')}
    try:
        for group, names in groups:
            begin = monotonic()
            before = guard.checkpoint()
            remaining = dict.fromkeys(names)
            while remaining:
                batch_size, parallel = _deployment_bounds('startup', len(resident))
                # Select from unhandled services, not an offset into a filtered wave.
                batch = tuple(name for name in remaining
                              if _compose_dependencies(services[name]) <= complete)[:batch_size]
                if not batch:
                    raise ComposeMaterializationError('no dependency-ready startup batch')
                batch_before = guard.checkpoint()
                started = monotonic()
                admitted.extend(batch)
                existing = _inspect_startup_services(project, services, batch, allow_missing=True, guard=guard)
                reusable = set()
                for c in existing:
                    name = c['Config']['Labels']['com.docker.compose.service']
                    expected_image = services[name]['image'].replace('${COMPOSE_PROJECT_NAME}', project)
                    if c['Config']['Image'] != expected_image:
                        raise ComposeMaterializationError(f'existing startup image differs: {name}')
                    if c['State']['Running'] or (name in helpers and c['State']['Status'] == 'exited' and c['State']['ExitCode'] == 0):
                        reusable.add(name)
                pending_batch = tuple(name for name in batch if name not in reusable)
                batch_path = path.parent / 'skyseed-startup-batch.yml'
                _write_startup_batch(batch_path, compose, pending_batch, identities)
                command = ['docker', 'compose', '--parallel', str(parallel), '-p', project,
                           '--project-directory', str(path.parent), '-f', str(batch_path),
                           'up', '-d', '--no-build', '--no-deps', *pending_batch]
                if pending_batch:
                    _bounded_startup_command(command, guard)
                guard.record('batch-input', services=list(pending_batch), reused=list(sorted(reusable)),
                             sha256=sha256(batch_path.read_bytes()).hexdigest())
                deadline = monotonic() + 30
                while True:
                    containers = _inspect_startup_services(project, services, batch, guard=guard)
                    pending = []
                    for c in containers:
                        name = c['Config']['Labels']['com.docker.compose.service']
                        state = c['State']
                        valid = state['Status'] == 'running' if name not in helpers else (
                            state['Status'] == 'exited' and state['ExitCode'] == 0)
                        if state.get('OOMKilled') or c.get('RestartCount', 0):
                            raise ComposeMaterializationError(f'container OOM/restart: {name}')
                        if not valid:
                            pending.append(name)
                    if not pending:
                        break
                    if monotonic() >= deadline:
                        raise ComposeMaterializationError(f'container startup states unresolved: {pending}')
                    sleep(1)
                settled = guard.checkpoint()
                identities.update({c['Config']['Labels']['com.docker.compose.service']: c['Id'] for c in containers})
                resident.update(c['Id'] for c in containers if c['State']['Running'])
                complete.update(batch)
                for name in batch:
                    del remaining[name]
                guard.record('batch-complete', group=group, services=list(batch),
                             started=len(complete), resident=len(resident), batch_size=batch_size, parallel=parallel, completed_helpers=len(complete & helpers), failed=0,
                             elapsed_seconds=monotonic() - started, before_available= batch_before,
                             settled_available=settled,
                             containers=[{'id': c['Id'], 'service': c['Config']['Labels']['com.docker.compose.service'],
                                          'state': c['State']} for c in containers])
                settled = guard.checkpoint()
            guard.record('group-complete', group=group, count=len(names), cumulative_count=len(complete),
                         elapsed_seconds=monotonic() - begin, before_available=before,
                         settled_available=settled, host_increment_bytes=None if before is None or settled is None else before - settled)
        if complete != set(services):
            raise ComposeMaterializationError('deployment did not cover every expected service')
        guard.record('deployment-complete', services=len(complete))
    except BaseException as error:
        guard.record('admission-stopped', error=str(error), admitted=admitted, completed=len(complete))
        _controlled_startup_cleanup(project, services, admitted, guard, compose_path=path)
        raise


def _prepare_generated_output(output_directory: Path, *, replace: bool) -> Path:
    """Create one clean generated output root and return its runtime path."""

    output = Path(output_directory)
    if output.exists() or output.is_symlink():
        if not replace:
            raise FileExistsError(f"example output already exists: {output}")
        if output.is_symlink() or output.is_file():
            output.unlink()
        else:
            shutil.rmtree(output)
    output.mkdir(parents=True)
    return output / "runtime"


def _write_compose_project_scripts(
    output_directory: Path,
    project_name: str,
    *,
    build_plan_path: str | None = None,
    startup_evidence_path: str | None = None,
    python_executable: str | None = None,
    controlled_startup: bool = False,
) -> None:
    """Write thin generated Compose-only start and cleanup controls."""

    output = Path(output_directory)
    compose_file = output / "runtime/docker-compose.yml"
    _validate_generated_compose(compose_file, project_name)
    shell_project_name = quote(project_name)
    if (build_plan_path is None) is not (startup_evidence_path is None):
        raise ValueError(
            "build_plan_path and startup_evidence_path must be supplied together"
        )
    if build_plan_path is None:
        startup = '"${COMPOSE[@]}" up -d --build\n'
    else:
        if not isinstance(python_executable, str) or not python_executable:
            raise ValueError("planned Compose startup requires python_executable")
        build_plan = _generated_relative_path(build_plan_path, "build plan")
        startup_evidence = _generated_relative_path(
            startup_evidence_path,
            "startup evidence",
        )
        if not (output / build_plan).is_file():
            raise ValueError("generated Compose build plan is unavailable")
        startup = f'''BUILD_PLAN="$SCRIPT_DIR/{build_plan}"
STARTUP_EVIDENCE="$SCRIPT_DIR/{startup_evidence}"
PYTHON={quote(python_executable)}
if [[ ! -f "$BUILD_PLAN" ]]; then
    echo "generated Compose build plan is missing: $BUILD_PLAN" >&2
    exit 1
fi
startup_begin="$($PYTHON -c 'import time; print(time.monotonic_ns())')"
startup_status=0
declare -A generated_image_refs=()

validate_build_plan() {{
    local action source target extra
    while read -r action source target extra; do
        [[ -z "$action" || "$action" == \\#* ]] && continue
        if [[ -n "${{extra:-}}" ]]; then
            return 1
        fi
        if [[ "$action" == "prerequisite" && -n "$source" && -z "${{target:-}}" ]]; then
            generated_image_refs["$source"]=1
        elif [[ "$action" == "build" && -n "$source" && -n "${{target:-}}" ]]; then
            generated_image_refs["$PROJECT_NAME-$target"]=1
        else
            return 1
        fi
    done < "$BUILD_PLAN"
    (( ${{#generated_image_refs[@]}} > 0 ))
}}

required_images_match() {{
    local -a metadata=()
    local raw_metadata raw_compose_images
    local index image_id image_project extra target
    declare -A compose_refs=()
    raw_compose_images="$("${{COMPOSE[@]}}" config --images)" || return 1
    while read -r target; do
        [[ -n "$target" ]] && compose_refs["$target"]=1
    done <<< "$raw_compose_images"
    (( ${{#compose_refs[@]}} > 0 )) || return 1
    for target in "${{!generated_image_refs[@]}}"; do
        [[ -n "${{compose_refs[$target]:-}}" ]] || return 1
    done
    local -a refs=("${{!compose_refs[@]}}")
    raw_metadata="$(
        docker image inspect \
            --format '{{{{.Id}}}} {{{{with index .Config "Labels"}}}}{{{{index . "com.docker.compose.project"}}}}{{{{end}}}}' \
            "${{refs[@]}}" 2>/dev/null
    )" || return 1
    mapfile -t metadata <<< "$raw_metadata"
    (( ${{#metadata[@]}} == ${{#refs[@]}} )) || return 1
    for index in "${{!refs[@]}}"; do
        read -r image_id image_project extra <<< "${{metadata[$index]}}"
        [[ -n "$image_id" ]] || return 1
        if [[ -n "${{generated_image_refs[${{refs[$index]}}]:-}}" ]]; then
            [[ "$image_project" == "$PROJECT_NAME" && -z "${{extra:-}}" ]] || return 1
        fi
    done
}}


build_required_images() {{
    local action source target extra
    while read -r action source target extra; do
        [[ -z "$action" || "$action" == \\#* ]] && continue
        "${{COMPOSE[@]}}" build "$source" || return $?
    done < "$BUILD_PLAN"
}}

if ! validate_build_plan; then
    echo "invalid generated build-plan entry" >&2
    startup_status=1
elif required_images_match; then
    echo "required generated images already match; skipping build plan"
else
    existing_containers="$("${{COMPOSE[@]}}" ps -aq)" || startup_status=$?
    if (( startup_status == 0 )); then
        if [[ -n "$existing_containers" ]]; then
            echo "required generated images are missing or stale while project containers exist" >&2
            startup_status=1
        else
            build_required_images || startup_status=$?
        fi
    fi
    if (( startup_status == 0 )) && ! required_images_match; then
        echo "generated image build did not produce the exact required image set" >&2
        startup_status=1
    fi
fi
if (( startup_status == 0 )); then
    "${{COMPOSE[@]}}" up -d --no-build || startup_status=$?
fi
startup_end="$($PYTHON -c 'import time; print(time.monotonic_ns())')"
$PYTHON - "$STARTUP_EVIDENCE" "$startup_begin" "$startup_end" "$startup_status" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
begin = int(sys.argv[2])
end = int(sys.argv[3])
exit_code = int(sys.argv[4])
path.write_text(
    json.dumps(
        {{
            "artifact_type": "compose-startup-evidence",
            "format_version": 1,
            "status": "completed" if exit_code == 0 else "failed",
            "duration_seconds": max(0, end - begin) / 1_000_000_000,
            "exit_code": exit_code,
        }},
        indent=2,
        sort_keys=True,
    )
    + "\\n",
    encoding="utf-8",
)
PY
exit "$startup_status"
'''
        if controlled_startup:
            startup = startup.replace(
                '"${COMPOSE[@]}" up -d --no-build || startup_status=$?',
                '$PYTHON -c \'from pathlib import Path; import sys; '
                'from skyseed_v2.compose.launcher import _controlled_startup; '
                '_controlled_startup(Path(sys.argv[1]), sys.argv[2])\' '
                '"$SCRIPT_DIR" "$PROJECT_NAME" || startup_status=$?',
            )
    _write_executable(
        output / "start.sh",
        f"""#!/usr/bin/env bash
set -euo pipefail

if (( $# != 0 )); then
    echo "start.sh does not accept arguments" >&2
    exit 2
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/runtime/docker-compose.yml"
PROJECT_NAME={shell_project_name}
COMPOSE=(
    docker compose
    -p "$PROJECT_NAME"
    -f "$COMPOSE_FILE"
)

"${{COMPOSE[@]}}" config --quiet
{startup}""",
    )
    _write_compose_cleanup_script(output, project_name, python_executable)


def _generated_relative_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise ValueError(f"{label} must be a generated relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or value == ".":
        raise ValueError(f"{label} must stay within generated output")
    return path.as_posix()


def _write_compose_cleanup_script(
    output_directory: Path,
    project_name: str,
    python_executable: str | None = None,
) -> None:
    """Write the generated Compose teardown control."""

    output = Path(output_directory)
    compose_file = output / "runtime/docker-compose.yml"
    _validate_generated_compose(compose_file, project_name)
    shell_project_name = quote(project_name)
    _write_executable(
        output / "cleanup.sh",
        f"""#!/usr/bin/env bash
set -euo pipefail

if (( $# != 0 )); then
    echo "cleanup.sh does not accept arguments" >&2
    exit 2
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/runtime/docker-compose.yml"
PROJECT_NAME={shell_project_name}
# Compose down does not consistently apply COMPOSE_PARALLEL_LIMIT to stops.
# Explicit resource batches enforce the bound independently of Compose version.
exec {quote(python_executable or sys.executable)} -c 'from pathlib import Path; import sys; from skyseed_v2.compose.launcher import _controlled_teardown; _controlled_teardown(Path(sys.argv[1]), sys.argv[2])' "$SCRIPT_DIR" "$PROJECT_NAME"
""",
    )


def _controlled_teardown(output: Path, project: str) -> None:
    """Remove verified project resources with bounded dependency-ordered requests."""
    path = output / 'runtime/docker-compose.yml'
    _validate_generated_compose(path, project)
    compose = safe_load(path.read_text())
    services = compose.get('services', {})
    guard = _StartupMeasurements(output / 'runtime/teardown-measurements.jsonl')

    def command(arguments: Sequence[str]) -> str:
        started = monotonic()
        try:
            result = run(arguments, capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise ComposeMaterializationError(result.stderr or result.stdout)
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
                raise ComposeMaterializationError('teardown ownership mismatch')
            rows.append({'id': identity, 'key': key})
        if len({row['id'] for row in rows}) != len(rows):
            raise ComposeMaterializationError('duplicate teardown resource identity')
        return rows

    services = _resolved_stop_services(services, path, project, command)
    dependencies = {name: _compose_dependencies(service) for name, service in services.items()}
    if any(deps - services.keys() for deps in dependencies.values()):
        raise ComposeMaterializationError('unknown generated teardown dependency')
    waves = []
    pending = set(services)
    while pending:
        needed = set().union(*(dependencies[name] for name in pending))
        ready = sorted(pending - needed)
        if not ready:
            raise ComposeMaterializationError('cyclic generated Compose dependencies')
        waves.append(ready)
        pending.difference_update(ready)

    containers = inventory('container')
    batch_size, parallel = _deployment_bounds('cleanup', len(containers))
    guard.record('teardown-start', project=project, batch_size=batch_size, parallel=parallel,
                 containers=len(containers), compose_sha256=sha256(path.read_bytes()).hexdigest())
    by_service: dict[str, list[str]] = {}
    for row in containers:
        by_service.setdefault(row['key'], []).append(row['id'])
    # Orphans are still project owned, as with compose down --remove-orphans.
    waves.append(sorted(set(by_service) - services.keys()))
    remaining_count = len(containers)
    for wave in waves:
        # Same-wave services have no mutual dependencies. Fast and graceful
        # services are independent within it; no next wave starts on failure.
        for fast in (False, True):
            pending = [(identity, name) for name in wave
                       if _disposable_network_service(services.get(name, {})) == fast
                       for identity in by_service.get(name, ())]
            while pending:
                batch_size, parallel = _deployment_bounds('cleanup', remaining_count)
                # Docker stop/rm issue a request for each ID; unlike Compose up,
                # they have no separate concurrency flag. Bound actual IDs too.
                batch, pending = pending[:min(batch_size, parallel)], pending[min(batch_size, parallel):]
                ids = [identity for identity, _ in batch]
                if not fast:
                    for identity, name in batch:
                        hooks = _pre_stop_commands(services.get(name, {}), identity)
                        if hooks:
                            state = loads(command(['docker', 'inspect', '--format', '{{json .State}}', identity]))
                            if state.get('Running'):
                                for hook in hooks:
                                    command(hook)
                    command(['docker', 'stop', *ids])
                command(['docker', 'rm', *(['--force'] if fast else []), *ids])
                remaining_count -= len(ids)
                guard.record('teardown-batch', container_ids=ids, fast_disposable=fast,
                             remaining=remaining_count, batch_size=batch_size, parallel=parallel)
    if inventory('container'):
        raise ComposeMaterializationError('project containers remain after teardown')
    external = {name for name, network in compose.get('networks', {}).items()
                if network.get('external')}
    networks = [row['id'] for row in inventory('network') if row['key'] not in external]
    for offset in range(0, len(networks), 8):
        command(['docker', 'network', 'rm', *networks[offset:offset + 8]])
    if any(row['key'] not in external for row in inventory('network')):
        raise ComposeMaterializationError('project networks remain after teardown')
    guard.record('teardown-complete', project=project, containers=len(containers), networks=len(networks))


def _validate_generated_compose(compose_file: Path, project_name: str) -> None:
    if not compose_file.is_file():
        raise ValueError(
            "output_directory must contain runtime/docker-compose.yml"
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


class _InterfaceLessRuntimeDocker(Docker):
    """Compile runtime-only nodes whose links are established by BpfRunner."""

    def __init__(self) -> None:
        super().__init__(
            namingScheme="as{asn}{role}-{name}",
            internetMapEnabled=True,
        )

    def _getComposeNodeName(self, node) -> str:
        if node.getInterfaces():
            return super()._getComposeNodeName(node)
        name = (
            f"as{node.getAsn()}"
            f"{self._nodeRoleToString(node.getRole())}-{node.getName()}"
        )
        return sub(r"[^a-zA-Z0-9_.-]", "_", name)

    def _compileNode(self, node) -> str:
        service = super()._compileNode(node)
        if node.getInterfaces() or "        network_mode: none\n" in service:
            return service
        empty_networks = "        networks:\n"
        if empty_networks not in service:
            raise RuntimeError(
                "upstream Docker service shape changed for an "
                "interface-less runtime node"
            )
        return service.replace(
            empty_networks,
            "        network_mode: none\n",
            1,
        )


def _write_compose_build_plan(runtime_directory: Path) -> Path:
    """Share one project image per identical context; retain per-node services."""

    runtime = Path(runtime_directory)
    dummy_directory = runtime / "dummies"
    prerequisites = tuple(
        sorted(path.name for path in dummy_directory.iterdir() if path.is_file())
    )
    if len(prerequisites) != 1:
        raise RuntimeError("generated runtime must have one shared base-image build")

    compose_path = runtime / "docker-compose.yml"
    compose = safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose['services']
    for service in services.values():
        labels = service.get('labels', {})
        if (service.get('network_mode') == 'none' and
                labels.get('org.seedsecuritylabs.seedemu.meta.skyseed.entity_type') in {'Satellite', 'GroundStation'}):
            service['x-skyseed-disposable-network'] = True
    groups: dict[str, list[str]] = {}
    for directory in sorted(runtime.iterdir(), key=lambda path: path.name):
        if not directory.is_dir() or not (directory / "Dockerfile").is_file():
            continue
        service = services.get(directory.name)
        if service is None or service.get("build") != f"./{directory.name}":
            raise RuntimeError("generated node build context does not match its service")
        digest = sha256()
        for path in sorted(
            directory.rglob("*"),
            key=lambda path: path.relative_to(directory).as_posix(),
        ):
            relative = path.relative_to(directory).as_posix().encode("utf-8")
            if path.is_symlink():
                raise RuntimeError("generated build context must not contain symlinks")
            digest.update(path.stat().st_mode.to_bytes(4, "big"))
            content = path.read_bytes() if path.is_file() else b""
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        groups.setdefault(digest.hexdigest(), []).append(directory.name)
    if not groups:
        raise RuntimeError("generated runtime has no node build contexts")

    lines = [
        "# skyseed-v2-compose-build-plan/v2",
        f"# contexts={sum(len(group) for group in groups.values())}",
        f"# identical-content-groups={len(groups)}",
        *(f"prerequisite {service}" for service in prerequisites),
    ]
    for fingerprint, group in sorted(groups.items(), key=lambda item: item[1][0]):
        representative = group[0]
        image_suffix = f"skyseed-{fingerprint}"
        lines.append(f"build {representative} {image_suffix}")
        for name in group:
            services[name]["image"] = f"${{COMPOSE_PROJECT_NAME}}-{image_suffix}"
            if name != representative:
                del services[name]["build"]
    compose_path.write_text(safe_dump(compose, sort_keys=False), encoding="utf-8")
    plan = runtime / "skyseed-build-plan.txt"
    plan.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return plan


class ComposeLaunchStrategy(str, Enum):
    """Select one explicit Compose project startup strategy.

    Layer: materialization integration value. Launcher callers create/select it
    and `ComposeProjectLauncher` consumes it. It is not fallback policy,
    compilation-stage input or domain state and is public from
    ``skyseed_v2.compose``.
    """

    SINGLE = "single"
    BATCHED = "batched"


def _default_command_runner(command: Sequence[str]) -> CompletedProcess[str]:
    return run(
        tuple(command),
        check=False,
        capture_output=True,
        text=True,
    )


def _non_empty_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    normalized = value.strip()
    if "\n" in normalized or "\r" in normalized:
        raise ValueError(f"{field_name} must not contain a newline")
    return normalized


def _service_tuple(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    try:
        snapshot = tuple(values)
    except TypeError as error:
        raise TypeError(f"{field_name} must be an iterable of strings") from error
    normalized = tuple(_non_empty_text(value, field_name) for value in snapshot)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} contains a duplicate service")
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class ComposeServiceExpectations:
    """Classify expected final states for every Compose service.

    Layer: materialization integration value. Launcher callers create it and
    `ComposeProjectLauncher` verifies it. It is not an inventory service,
    scenario or runtime entity model and is public from ``skyseed_v2.compose``.
    """

    long_running: tuple[str, ...]
    successful_exit_helpers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        long_running = _service_tuple(self.long_running, "long_running")
        helpers = _service_tuple(
            self.successful_exit_helpers,
            "successful_exit_helpers",
        )
        overlap = set(long_running) & set(helpers)
        if overlap:
            raise ValueError(
                "service cannot be both long-running and a successful-exit helper: "
                f"{min(overlap)}"
            )
        object.__setattr__(self, "long_running", long_running)
        object.__setattr__(self, "successful_exit_helpers", helpers)

    @property
    def all_services(self) -> tuple[str, ...]:
        return tuple(sorted(self.long_running + self.successful_exit_helpers))


@dataclass(frozen=True, slots=True)
class ComposeBatchResult:
    """Record evidence for one explicitly requested sequential batch.

    Layer: materialization operation result. `ComposeProjectLauncher` creates
    it and tooling/evidence callers read it. It is not a domain entity,
    Simulator result or default batching policy and is public only from
    ``skyseed_v2.compose``.
    """

    batch_index: int
    services: tuple[str, ...]
    compose_cli_elapsed_seconds: float
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class ComposeMaterializationResult:
    """Record evidence that one complete Compose project reached expected states.

    Layer: materialization operation result. `ComposeProjectLauncher` creates
    it and integration/evidence callers read it. It is not an inventory,
    Emulator entity, deployment entity or Simulator result and is public only
    from ``skyseed_v2.compose``.
    """

    compose_file: Path
    project_name: str
    compose_version: str
    strategy: ComposeLaunchStrategy
    parallel_limit: int
    batch_size: int | None
    compose_sha256: str
    declared_services: tuple[str, ...]
    long_running_services: tuple[str, ...]
    successful_exit_helpers: tuple[str, ...]
    batches: tuple[ComposeBatchResult, ...]
    compose_cli_elapsed_seconds: float
    total_elapsed_seconds: float


class ComposeMaterializationError(RuntimeError):
    """Report fail-closed Compose materialization failure with batch context.

    Layer: materialization integration error. Launcher operations create it and
    integration callers catch it. It is not domain/runtime state and is public
    from ``skyseed_v2.compose`` as the stable failure boundary.
    """

    def __init__(
        self,
        message: str,
        *,
        batch_index: int | None = None,
        services: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.batch_index = batch_index
        self.services = services


@dataclass(frozen=True, slots=True)
class _ServiceState:
    service: str
    container_id: str
    status: str
    exit_code: int


class ComposeProjectLauncher:
    """Materialize one complete Compose v2 project and verify its final state.

    Layer: materialization integration. Tooling callers create it and consume
    its operation result. It is not a domain entity, compilation stage, Runner,
    Simulator or recovery coordinator. It is public from
    ``skyseed_v2.compose`` because already-compiled project startup needs a
    bounded adapter.

    The default is one full-project Compose invocation with controlled
    internal parallelism. Batching is available only through an explicit
    strategy. This tool neither compiles nor edits Compose, and never falls
    back to the legacy ``docker-compose`` executable.
    """

    def __init__(
        self,
        *,
        compose_file: Path,
        project_name: str,
        expectations: ComposeServiceExpectations,
        strategy: ComposeLaunchStrategy | str = ComposeLaunchStrategy.SINGLE,
        parallel_limit: int = 16,
        batch_size: int = 16,
        command_runner: CommandRunner | None = None,
        docker_executable: str = "docker",
        verification_attempts: int = 40,
        verification_interval_seconds: float = 0.25,
        monotonic_clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        path = Path(compose_file)
        if not path.is_file():
            raise ValueError("compose_file must identify an existing file")
        if not isinstance(expectations, ComposeServiceExpectations):
            raise TypeError("expectations must be ComposeServiceExpectations")
        try:
            normalized_strategy = ComposeLaunchStrategy(strategy)
        except (TypeError, ValueError) as error:
            raise ValueError("strategy must be 'single' or 'batched'") from error
        if (
            not isinstance(parallel_limit, int)
            or isinstance(parallel_limit, bool)
            or parallel_limit <= 0
        ):
            raise ValueError("parallel_limit must be a positive integer")
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or batch_size <= 0
        ):
            raise ValueError("batch_size must be a positive integer")
        if (
            not isinstance(verification_attempts, int)
            or isinstance(verification_attempts, bool)
            or verification_attempts <= 0
        ):
            raise ValueError("verification_attempts must be a positive integer")
        interval = float(verification_interval_seconds)
        if not isfinite(interval) or interval < 0.0:
            raise ValueError(
                "verification_interval_seconds must be a non-negative finite number"
            )
        if not callable(command_runner) and command_runner is not None:
            raise TypeError("command_runner must be callable")
        if not callable(monotonic_clock) or not callable(sleeper):
            raise TypeError("monotonic_clock and sleeper must be callable")

        self._compose_file = path.resolve()
        self._project_name = _non_empty_text(project_name, "project_name")
        self._expectations = expectations
        self._strategy = normalized_strategy
        self._parallel_limit = parallel_limit
        self._batch_size = batch_size
        self._runner = command_runner or _default_command_runner
        self._docker_executable = _non_empty_text(
            docker_executable, "docker_executable"
        )
        self._verification_attempts = verification_attempts
        self._verification_interval_seconds = interval
        self._clock = monotonic_clock
        self._sleep = sleeper

    def materialize(self) -> ComposeMaterializationResult:
        """Start every declared service once and verify the complete project."""

        compose_version = self._compose_version()
        compose_digest = self._compose_digest()
        declared_services = self._declared_services()
        self._validate_expectations(declared_services)

        materialization_started = self._clock()
        if self._strategy is ComposeLaunchStrategy.SINGLE:
            cli_started = self._clock()
            self._assert_compose_digest(compose_digest, None, ())
            self._execute(
                self._compose_command("up", "-d"),
                context="single full-project up",
                services=declared_services,
            )
            compose_cli_elapsed = self._clock() - cli_started
            self._wait_for_expected_states(
                declared_services,
                declared_services=declared_services,
                batch_index=None,
                batch_services=(),
                final=True,
            )
            self._assert_compose_digest(compose_digest, None, ())
            batch_results: list[ComposeBatchResult] = []
        else:
            batch_results, compose_cli_elapsed = self._materialize_batches(
                declared_services,
                compose_digest,
            )
            self._wait_for_expected_states(
                declared_services,
                declared_services=declared_services,
                batch_index=None,
                batch_services=(),
                final=True,
            )
            self._assert_compose_digest(compose_digest, None, ())

        total_elapsed = self._clock() - materialization_started
        return ComposeMaterializationResult(
            compose_file=self._compose_file,
            project_name=self._project_name,
            compose_version=compose_version,
            strategy=self._strategy,
            parallel_limit=self._parallel_limit,
            batch_size=(
                self._batch_size
                if self._strategy is ComposeLaunchStrategy.BATCHED
                else None
            ),
            compose_sha256=compose_digest,
            declared_services=declared_services,
            long_running_services=self._expectations.long_running,
            successful_exit_helpers=(
                self._expectations.successful_exit_helpers
            ),
            batches=tuple(batch_results),
            compose_cli_elapsed_seconds=compose_cli_elapsed,
            total_elapsed_seconds=total_elapsed,
        )

    def _materialize_batches(
        self,
        declared_services: tuple[str, ...],
        compose_digest: str,
    ) -> tuple[list[ComposeBatchResult], float]:
        completed: set[str] = set()
        batch_results: list[ComposeBatchResult] = []
        compose_cli_elapsed = 0.0
        for offset in range(0, len(declared_services), self._batch_size):
            services = declared_services[offset : offset + self._batch_size]
            batch_index = offset // self._batch_size + 1
            batch_started = self._clock()
            self._assert_compose_digest(compose_digest, batch_index, services)
            cli_started = self._clock()
            self._execute(
                self._compose_command("up", "-d", *services),
                context=f"batch {batch_index} up",
                batch_index=batch_index,
                services=services,
            )
            batch_cli_elapsed = self._clock() - cli_started
            compose_cli_elapsed += batch_cli_elapsed
            completed.update(services)
            self._wait_for_expected_states(
                tuple(sorted(completed)),
                declared_services=declared_services,
                batch_index=batch_index,
                batch_services=services,
                final=False,
            )
            self._assert_compose_digest(compose_digest, batch_index, services)
            batch_results.append(
                ComposeBatchResult(
                    batch_index=batch_index,
                    services=services,
                    compose_cli_elapsed_seconds=batch_cli_elapsed,
                    elapsed_seconds=self._clock() - batch_started,
                )
            )
        return batch_results, compose_cli_elapsed

    def _compose_version(self) -> str:
        result = self._execute(
            (self._docker_executable, "compose", "version", "--short"),
            context="Docker Compose v2 version check",
        )
        version = result.stdout.strip().removeprefix("v")
        match = fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version)
        if match is None or int(match.group(1)) < 2:
            raise ComposeMaterializationError(
                "Docker Compose v2 version check returned an unsupported version: "
                f"{result.stdout.strip()!r}"
            )
        return version

    def _compose_digest(self) -> str:
        try:
            content = self._compose_file.read_bytes()
        except OSError as error:
            raise ComposeMaterializationError(
                f"cannot read Compose file: {self._compose_file}"
            ) from error
        return sha256(content).hexdigest()

    def _assert_compose_digest(
        self,
        expected_digest: str,
        batch_index: int | None,
        services: tuple[str, ...],
    ) -> None:
        if self._compose_digest() != expected_digest:
            raise ComposeMaterializationError(
                "Compose file changed during materialization",
                batch_index=batch_index,
                services=services,
            )

    def _declared_services(self) -> tuple[str, ...]:
        result = self._execute(
            self._compose_command("config", "--services"),
            context="Compose service discovery",
        )
        services = tuple(
            line.strip() for line in result.stdout.splitlines() if line.strip()
        )
        if not services:
            raise ComposeMaterializationError(
                "Compose service discovery returned an empty service list"
            )
        if len(set(services)) != len(services):
            raise ComposeMaterializationError(
                "Compose service discovery returned a duplicate service"
            )
        return tuple(sorted(services))

    def _validate_expectations(
        self, declared_services: tuple[str, ...]
    ) -> None:
        declared = set(declared_services)
        expected = set(self._expectations.all_services)
        unknown = expected - declared
        unclassified = declared - expected
        if unknown or unclassified:
            details = []
            if unknown:
                details.append(f"unknown expected services={sorted(unknown)}")
            if unclassified:
                details.append(f"unclassified services={sorted(unclassified)}")
            raise ComposeMaterializationError(
                "Compose service expectations do not match declared services: "
                + "; ".join(details)
            )

    def _wait_for_expected_states(
        self,
        required_services: tuple[str, ...],
        *,
        declared_services: tuple[str, ...],
        batch_index: int | None,
        batch_services: tuple[str, ...],
        final: bool,
    ) -> None:
        last_pending: tuple[str, ...] = ()
        for attempt in range(self._verification_attempts):
            states = self._read_project_states(() if final else required_services)
            pending = self._validate_states(
                states,
                required_services=required_services,
                declared_services=declared_services,
                final=final,
                batch_index=batch_index,
                batch_services=batch_services,
            )
            if not pending:
                return
            last_pending = pending
            if attempt + 1 < self._verification_attempts:
                self._sleep(self._verification_interval_seconds)
        location = "final project" if final else f"batch {batch_index}"
        raise ComposeMaterializationError(
            f"{location} state verification timed out; pending={list(last_pending)}",
            batch_index=batch_index,
            services=batch_services,
        )

    def _validate_states(
        self,
        states: dict[str, _ServiceState],
        *,
        required_services: tuple[str, ...],
        declared_services: tuple[str, ...],
        final: bool,
        batch_index: int | None,
        batch_services: tuple[str, ...],
    ) -> tuple[str, ...]:
        declared = set(declared_services)
        extra = set(states) - declared
        if extra:
            raise ComposeMaterializationError(
                f"project contains extra services: {sorted(extra)}",
                batch_index=batch_index,
                services=batch_services,
            )
        pending: list[str] = []
        for service in required_services:
            state = states.get(service)
            if state is None:
                pending.append(f"{service}:missing")
                continue
            if service in self._expectations.long_running:
                if state.status == "running":
                    continue
                if state.status == "created":
                    pending.append(f"{service}:created")
                    continue
                raise ComposeMaterializationError(
                    f"long-running service {service!r} is {state.status} "
                    f"with exit code {state.exit_code}",
                    batch_index=batch_index,
                    services=batch_services,
                )
            if state.status == "exited" and state.exit_code == 0:
                continue
            if state.status in {"created", "running"}:
                pending.append(f"{service}:{state.status}")
                continue
            raise ComposeMaterializationError(
                f"successful-exit helper {service!r} is {state.status} "
                f"with exit code {state.exit_code}",
                batch_index=batch_index,
                services=batch_services,
            )
        if final and set(states) != set(required_services):
            missing = set(required_services) - set(states)
            pending.extend(f"{service}:missing" for service in sorted(missing))
        return tuple(sorted(set(pending)))

    def _read_project_states(
        self, services: tuple[str, ...]
    ) -> dict[str, _ServiceState]:
        ids_result = self._execute(
            self._compose_command("ps", "--all", "--quiet", *services),
            context="Compose project state discovery",
        )
        container_ids = tuple(
            line.strip()
            for line in ids_result.stdout.splitlines()
            if line.strip()
        )
        if len(set(container_ids)) != len(container_ids):
            raise ComposeMaterializationError(
                "Compose project state discovery returned a duplicate container ID"
            )
        if not container_ids:
            return {}
        inspect_result = self._execute(
            (self._docker_executable, "inspect", *container_ids),
            context="Compose project container inspection",
        )
        try:
            inspected = loads(inspect_result.stdout)
        except JSONDecodeError as error:
            raise ComposeMaterializationError(
                "Docker inspect returned invalid JSON"
            ) from error
        if not isinstance(inspected, list) or len(inspected) != len(container_ids):
            raise ComposeMaterializationError(
                "Docker inspect result does not match requested project containers"
            )

        states: dict[str, _ServiceState] = {}
        for container in inspected:
            if not isinstance(container, dict):
                raise ComposeMaterializationError(
                    "Docker inspect returned a non-object container entry"
                )
            config = container.get("Config")
            state = container.get("State")
            if not isinstance(config, dict) or not isinstance(state, dict):
                raise ComposeMaterializationError(
                    "Docker inspect container entry is missing Config or State"
                )
            labels = config.get("Labels")
            if not isinstance(labels, dict):
                raise ComposeMaterializationError(
                    "project container is missing Compose labels"
                )
            project = labels.get("com.docker.compose.project")
            service = labels.get("com.docker.compose.service")
            if project != self._project_name:
                raise ComposeMaterializationError(
                    "Compose project state returned a container from another project"
                )
            if not isinstance(service, str) or not service:
                raise ComposeMaterializationError(
                    "project container is missing its Compose service label"
                )
            if service in states:
                raise ComposeMaterializationError(
                    f"project contains duplicate containers for service {service!r}"
                )
            container_id = container.get("Id")
            status = state.get("Status")
            exit_code = state.get("ExitCode")
            if (
                not isinstance(container_id, str)
                or not isinstance(status, str)
                or not isinstance(exit_code, int)
            ):
                raise ComposeMaterializationError(
                    "Docker inspect container entry has invalid state fields"
                )
            states[service] = _ServiceState(
                service=service,
                container_id=container_id,
                status=status.casefold(),
                exit_code=exit_code,
            )
        return states

    def _compose_command(self, *arguments: str) -> Command:
        return (
            self._docker_executable,
            "compose",
            "--parallel",
            str(self._parallel_limit),
            "-p",
            self._project_name,
            "-f",
            str(self._compose_file),
            *arguments,
        )

    def _execute(
        self,
        command: Command,
        *,
        context: str,
        batch_index: int | None = None,
        services: tuple[str, ...] = (),
    ) -> CompletedProcess[str]:
        try:
            result = self._runner(command)
        except OSError as error:
            raise ComposeMaterializationError(
                f"{context} could not execute {command[0]!r}: {error}",
                batch_index=batch_index,
                services=services,
            ) from error
        if not isinstance(result, CompletedProcess):
            raise TypeError("command_runner must return subprocess.CompletedProcess")
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            detail = f": {stderr}" if stderr else ""
            service_detail = f"; services={list(services)}" if services else ""
            raise ComposeMaterializationError(
                f"{context} failed with exit code {result.returncode}{detail}"
                f"{service_detail}",
                batch_index=batch_index,
                services=services,
            )
        return result
