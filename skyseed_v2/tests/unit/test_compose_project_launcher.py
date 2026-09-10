from __future__ import annotations

from json import dumps
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from skyseed_v2.compose import (
    ComposeLaunchStrategy,
    ComposeMaterializationError,
    ComposeProjectLauncher,
    ComposeServiceExpectations,
)


class FakeComposeRuntime:
    def __init__(
        self,
        *,
        declared_services: tuple[str, ...],
        expectations: ComposeServiceExpectations,
        fail_up_batch: int | None = None,
        abnormal_long_running: str | None = None,
        omit_from_final: str | None = None,
        extra_final_service: str | None = None,
        mutate_compose_after_up_batch: int | None = None,
        compose_version: str = "2.40.2",
        version_returncode: int = 0,
        expected_parallel_limit: int = 16,
    ) -> None:
        self.declared_services = declared_services
        self.expectations = expectations
        self.fail_up_batch = fail_up_batch
        self.abnormal_long_running = abnormal_long_running
        self.omit_from_final = omit_from_final
        self.extra_final_service = extra_final_service
        self.mutate_compose_after_up_batch = mutate_compose_after_up_batch
        self.compose_version = compose_version
        self.version_returncode = version_returncode
        self.expected_parallel_limit = expected_parallel_limit
        self.commands: list[tuple[str, ...]] = []
        self.up_batches: list[tuple[str, ...]] = []
        self.states: dict[str, tuple[str, int]] = {}

    def __call__(self, command) -> CompletedProcess[str]:
        command = tuple(command)
        self.commands.append(command)
        if command[:4] == ("docker", "compose", "version", "--short"):
            return self._completed(
                command,
                returncode=self.version_returncode,
                stdout=f"{self.compose_version}\n",
                stderr="compose plugin unavailable" if self.version_returncode else "",
            )
        if command[:2] == ("docker", "compose"):
            assert command[2:4] == (
                "--parallel",
                str(self.expected_parallel_limit),
            )
            action = command[8]
            if action == "config":
                return self._completed(command, stdout="\n".join(self.declared_services))
            if action == "up":
                requested_services = command[10:]
                self.up_batches.append(requested_services)
                if self.fail_up_batch == len(self.up_batches):
                    return self._completed(command, returncode=17, stderr="up failed")
                services = requested_services or self.declared_services
                for service in services:
                    if service in self.expectations.long_running:
                        state = (
                            ("exited", 1)
                            if service == self.abnormal_long_running
                            else ("running", 0)
                        )
                    else:
                        state = ("exited", 0)
                    self.states[service] = state
                if self.mutate_compose_after_up_batch == len(self.up_batches):
                    Path(command[7]).write_text("changed: true\n")
                return self._completed(command)
            if action == "ps":
                requested = command[11:]
                services = tuple(self.states) if not requested else requested
                if not requested and self.omit_from_final is not None:
                    services = tuple(
                        service
                        for service in services
                        if service != self.omit_from_final
                    )
                if not requested and self.extra_final_service is not None:
                    self.states[self.extra_final_service] = ("running", 0)
                    services = (*services, self.extra_final_service)
                ids = [f"id-{service}" for service in services if service in self.states]
                return self._completed(command, stdout="\n".join(ids))
        if command[:2] == ("docker", "inspect"):
            containers = []
            for container_id in command[2:]:
                service = container_id.removeprefix("id-")
                status, exit_code = self.states[service]
                containers.append(
                    {
                        "Id": container_id,
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": "test_project",
                                "com.docker.compose.service": service,
                            }
                        },
                        "State": {
                            "Status": status,
                            "ExitCode": exit_code,
                        },
                    }
                )
            return self._completed(command, stdout=dumps(containers))
        raise AssertionError(f"unexpected command: {command}")

    @staticmethod
    def _completed(
        command: tuple[str, ...],
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> CompletedProcess[str]:
        return CompletedProcess(command, returncode, stdout, stderr)


def compose_file(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "docker-compose.yml"
    path.write_text("version: '3.4'\nservices: {}\n", encoding="utf-8")
    return path


def expectations(
    long_running: tuple[str, ...] = ("node-a", "node-b", "node-c"),
    helpers: tuple[str, ...] = ("helper",),
) -> ComposeServiceExpectations:
    return ComposeServiceExpectations(
        long_running=long_running,
        successful_exit_helpers=helpers,
    )


def launcher(
    tmp_path: Path,
    runtime: FakeComposeRuntime,
    *,
    batch_size: int,
    expected: ComposeServiceExpectations,
    strategy: ComposeLaunchStrategy = ComposeLaunchStrategy.BATCHED,
    parallel_limit: int = 16,
    verification_attempts: int = 1,
) -> ComposeProjectLauncher:
    return ComposeProjectLauncher(
        compose_file=compose_file(tmp_path),
        project_name="test_project",
        expectations=expected,
        strategy=strategy,
        parallel_limit=parallel_limit,
        batch_size=batch_size,
        command_runner=runtime,
        verification_attempts=verification_attempts,
        verification_interval_seconds=0,
    )


def test_default_strategy_is_one_compose_v2_full_project_invocation(
    tmp_path: Path,
) -> None:
    expected = expectations()
    runtime = FakeComposeRuntime(
        declared_services=("node-c", "helper", "node-a", "node-b"),
        expectations=expected,
    )

    result = ComposeProjectLauncher(
        compose_file=compose_file(tmp_path),
        project_name="test_project",
        expectations=expected,
        command_runner=runtime,
        verification_attempts=1,
        verification_interval_seconds=0,
    ).materialize()

    assert runtime.up_batches == [()]
    assert result.strategy is ComposeLaunchStrategy.SINGLE
    assert result.parallel_limit == 16
    assert result.batch_size is None
    assert result.batches == ()
    assert result.compose_version == "2.40.2"
    assert result.declared_services == expected.all_services


def test_parallel_limit_is_forwarded_to_compose_v2(tmp_path: Path) -> None:
    expected = expectations(("node-a",), ())
    runtime = FakeComposeRuntime(
        declared_services=expected.all_services,
        expectations=expected,
        expected_parallel_limit=7,
    )

    result = launcher(
        tmp_path,
        runtime,
        batch_size=3,
        expected=expected,
        strategy=ComposeLaunchStrategy.SINGLE,
        parallel_limit=7,
    ).materialize()

    assert result.parallel_limit == 7
    assert any(command[2:4] == ("--parallel", "7") for command in runtime.commands)


def test_missing_or_unsupported_compose_v2_fails_without_v1_fallback(
    tmp_path: Path,
) -> None:
    expected = expectations(("node-a",), ())
    missing = FakeComposeRuntime(
        declared_services=expected.all_services,
        expectations=expected,
        version_returncode=1,
    )
    with pytest.raises(ComposeMaterializationError, match="version check failed"):
        launcher(
            tmp_path / "missing",
            missing,
            batch_size=1,
            expected=expected,
        ).materialize()
    assert missing.commands == [("docker", "compose", "version", "--short")]

    unsupported = FakeComposeRuntime(
        declared_services=expected.all_services,
        expectations=expected,
        compose_version="1.29.2",
    )
    with pytest.raises(ComposeMaterializationError, match="unsupported version"):
        launcher(
            tmp_path / "unsupported",
            unsupported,
            batch_size=1,
            expected=expected,
        ).materialize()
    assert not any(command[0] == "docker-compose" for command in unsupported.commands)


def test_single_invocation_failure_is_propagated_without_batched_retry(
    tmp_path: Path,
) -> None:
    expected = expectations()
    runtime = FakeComposeRuntime(
        declared_services=expected.all_services,
        expectations=expected,
        fail_up_batch=1,
    )
    with pytest.raises(ComposeMaterializationError, match="single full-project up"):
        launcher(
            tmp_path,
            runtime,
            batch_size=2,
            expected=expected,
            strategy=ComposeLaunchStrategy.SINGLE,
        ).materialize()
    assert runtime.up_batches == [()]


def test_service_order_is_deterministic_and_tail_batch_is_preserved(
    tmp_path: Path,
) -> None:
    expected = expectations()
    first_runtime = FakeComposeRuntime(
        declared_services=("node-c", "helper", "node-a", "node-b"),
        expectations=expected,
    )
    second_runtime = FakeComposeRuntime(
        declared_services=("node-b", "node-a", "node-c", "helper"),
        expectations=expected,
    )

    first = launcher(
        tmp_path / "first",
        first_runtime,
        batch_size=3,
        expected=expected,
    ).materialize()
    second = launcher(
        tmp_path / "second",
        second_runtime,
        batch_size=3,
        expected=expected,
    ).materialize()

    assert first.declared_services == second.declared_services == (
        "helper",
        "node-a",
        "node-b",
        "node-c",
    )
    assert first_runtime.up_batches == second_runtime.up_batches == [
        ("helper", "node-a", "node-b"),
        ("node-c",),
    ]
    assert tuple(len(batch.services) for batch in first.batches) == (3, 1)


@pytest.mark.parametrize(
    ("batch_size", "expected_sizes"),
    ((1, (1, 1, 1, 1)), (2, (2, 2)), (5, (4,))),
)
def test_batch_size_is_configurable(
    tmp_path: Path,
    batch_size: int,
    expected_sizes: tuple[int, ...],
) -> None:
    expected = expectations()
    runtime = FakeComposeRuntime(
        declared_services=expected.all_services,
        expectations=expected,
    )

    result = launcher(
        tmp_path,
        runtime,
        batch_size=batch_size,
        expected=expected,
    ).materialize()

    assert tuple(len(batch.services) for batch in result.batches) == expected_sizes
    assert result.batch_size == batch_size


def test_empty_or_duplicate_discovered_service_list_fails_closed(
    tmp_path: Path,
) -> None:
    expected = expectations(("node-a",), ())
    empty = FakeComposeRuntime(declared_services=(), expectations=expected)
    with pytest.raises(ComposeMaterializationError, match="empty service list"):
        launcher(
            tmp_path / "empty",
            empty,
            batch_size=1,
            expected=expected,
        ).materialize()

    duplicate = FakeComposeRuntime(
        declared_services=("node-a", "node-a"),
        expectations=expected,
    )
    with pytest.raises(ComposeMaterializationError, match="duplicate service"):
        launcher(
            tmp_path / "duplicate",
            duplicate,
            batch_size=1,
            expected=expected,
        ).materialize()


def test_duplicate_or_unknown_expected_service_fails_before_up(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="duplicate service"):
        ComposeServiceExpectations(("node-a", "node-a"))

    expected = expectations(("node-a", "unknown"), ())
    runtime = FakeComposeRuntime(
        declared_services=("node-a",),
        expectations=expected,
    )
    with pytest.raises(ComposeMaterializationError, match="unknown expected"):
        launcher(
            tmp_path,
            runtime,
            batch_size=1,
            expected=expected,
        ).materialize()
    assert runtime.up_batches == []


def test_middle_batch_failure_stops_without_later_batches(tmp_path: Path) -> None:
    expected = expectations()
    runtime = FakeComposeRuntime(
        declared_services=expected.all_services,
        expectations=expected,
        fail_up_batch=2,
    )
    materializer = launcher(
        tmp_path,
        runtime,
        batch_size=2,
        expected=expected,
    )

    with pytest.raises(ComposeMaterializationError, match="batch 2 up") as raised:
        materializer.materialize()

    assert raised.value.batch_index == 2
    assert raised.value.services == ("node-b", "node-c")
    assert runtime.up_batches == [
        ("helper", "node-a"),
        ("node-b", "node-c"),
    ]


def test_all_batches_use_one_project_and_one_compose_file(tmp_path: Path) -> None:
    expected = expectations()
    runtime = FakeComposeRuntime(
        declared_services=expected.all_services,
        expectations=expected,
    )
    materializer = launcher(
        tmp_path,
        runtime,
        batch_size=2,
        expected=expected,
    )
    result = materializer.materialize()

    compose_prefixes = {
        command[:9]
        for command in runtime.commands
        if command[:2] == ("docker", "compose") and len(command) > 8
    }
    assert compose_prefixes == {
        (
            "docker",
            "compose",
            "--parallel",
            "16",
            "-p",
            "test_project",
            "-f",
            str(result.compose_file),
            "config",
        ),
        (
            "docker",
            "compose",
            "--parallel",
            "16",
            "-p",
            "test_project",
            "-f",
            str(result.compose_file),
            "up",
        ),
        (
            "docker",
            "compose",
            "--parallel",
            "16",
            "-p",
            "test_project",
            "-f",
            str(result.compose_file),
            "ps",
        ),
    }
    assert list(tmp_path.iterdir()) == [result.compose_file]


def test_helper_exit_zero_is_accepted_and_long_running_exit_is_rejected(
    tmp_path: Path,
) -> None:
    expected = expectations(("node-a",), ("helper",))
    successful = FakeComposeRuntime(
        declared_services=expected.all_services,
        expectations=expected,
    )
    result = launcher(
        tmp_path / "success",
        successful,
        batch_size=2,
        expected=expected,
    ).materialize()
    assert result.successful_exit_helpers == ("helper",)

    abnormal = FakeComposeRuntime(
        declared_services=expected.all_services,
        expectations=expected,
        abnormal_long_running="node-a",
    )
    with pytest.raises(ComposeMaterializationError, match="long-running service"):
        launcher(
            tmp_path / "abnormal",
            abnormal,
            batch_size=2,
            expected=expected,
        ).materialize()


def test_final_missing_service_is_rejected(tmp_path: Path) -> None:
    expected = expectations(("node-a", "node-b"), ())
    runtime = FakeComposeRuntime(
        declared_services=expected.all_services,
        expectations=expected,
        omit_from_final="node-b",
    )
    with pytest.raises(ComposeMaterializationError, match="timed out"):
        launcher(
            tmp_path,
            runtime,
            batch_size=2,
            expected=expected,
        ).materialize()


def test_final_extra_project_service_is_rejected(tmp_path: Path) -> None:
    expected = expectations(("node-a",), ())
    runtime = FakeComposeRuntime(
        declared_services=expected.all_services,
        expectations=expected,
        extra_final_service="orphan",
    )
    with pytest.raises(ComposeMaterializationError, match="extra services"):
        launcher(
            tmp_path,
            runtime,
            batch_size=1,
            expected=expected,
        ).materialize()


def test_compose_file_drift_stops_before_a_later_batch(tmp_path: Path) -> None:
    expected = expectations(("node-a", "node-b"), ())
    runtime = FakeComposeRuntime(
        declared_services=expected.all_services,
        expectations=expected,
        mutate_compose_after_up_batch=1,
    )
    with pytest.raises(ComposeMaterializationError, match="Compose file changed"):
        launcher(
            tmp_path,
            runtime,
            batch_size=1,
            expected=expected,
        ).materialize()
    assert runtime.up_batches == [("node-a",)]


def test_launcher_does_not_import_or_call_skyseed_compiler() -> None:
    from inspect import getsourcefile

    from skyseed_v2.compose import launcher

    source = Path(getsourcefile(launcher)).read_text()
    assert "SkyseedDockerCompiler" not in source
    assert ".compile(" not in source
    # Generated packaging may edit Compose YAML; the public launcher only
    # deploys an already compiled project.
    from inspect import getsource
    assert "safe_load" not in getsource(launcher.ComposeProjectLauncher)
    assert "safe_dump" not in getsource(launcher.ComposeProjectLauncher)
    assert '"compose"' in source
    assert "compose_executable" not in source
@pytest.mark.parametrize("failure", [None, "build", "ps", "owned-stale"])
def test_generated_shared_image_startup_checks_unique_images_and_failures(tmp_path, monkeypatch, failure):
    import json
    import os
    import subprocess
    import sys
    from yaml import safe_dump
    from skyseed_v2.compose.launcher import _write_compose_build_plan, _write_compose_project_scripts

    runtime = tmp_path / "runtime"
    (runtime / "dummies").mkdir(parents=True)
    (runtime / "dummies/base").write_text("FROM scratch\n")
    services = {"base": {"build": {"context": ".", "dockerfile": "dummies/base"}, "image": "base"},
                "map": {"image": "external/map:v1"}}
    for name in ("a", "b", "c"):
        (runtime / name).mkdir()
        (runtime / name / "Dockerfile").write_text("FROM base\n" + ("ENV C=1\n" if name == "c" else ""))
        services[name] = {"build": f"./{name}", "container_name": name}
    (runtime / "docker-compose.yml").write_text(safe_dump({"services": services}))
    _write_compose_build_plan(runtime)
    _write_compose_project_scripts(tmp_path, "test-project", build_plan_path="runtime/skyseed-build-plan.txt",
                                   startup_evidence_path="runtime/startup.json", python_executable=sys.executable)
    fake = tmp_path / "bin"
    fake.mkdir()
    docker = fake / "docker"
    docker.write_text(f"#!{sys.executable}\n" + r'''
import json, os, sys
from pathlib import Path
from yaml import safe_load
root = Path(os.environ['TEST_DEPLOYMENT_ROOT'])
args = sys.argv[1:]
with (root/'commands.jsonl').open('a') as stream:
    stream.write(json.dumps(args)+'\n')
state_path = root/'images.json'
images = json.loads(state_path.read_text()) if state_path.exists() else {"external/map:v1": "sha256:external"}
failure = os.environ.get('TEST_DEPLOYMENT_FAILURE')
if args[:2] == ['image', 'inspect']:
    refs = args[4:]
    assert len(refs) == len(set(refs)) == 4
    if any(ref not in images for ref in refs):
        sys.exit(1)
    for ref in refs:
        print(images[ref], '<no value>' if ref=='external/map:v1' else 'test-project')
elif args[0] == 'compose':
    services = safe_load((root/'runtime/docker-compose.yml').read_text())['services']
    args = args[5:]
    def image(service):
        return services[service]['image'].replace('${COMPOSE_PROJECT_NAME}', 'test-project')
    if args == ['config', '--images']:
        print('\n'.join(image(name) for name in services))
    elif args == ['config', '--quiet']:
        pass
    elif args == ['ps', '-aq']:
        if failure == 'ps':
            sys.exit(7)
        if failure == 'owned-stale':
            print('owned-container')
    elif args[0] == 'build':
        if failure == 'build':
            sys.exit(9)
        images[image(args[1])] = 'sha256:' + args[1]
        state_path.write_text(json.dumps(images))
    elif args == ['up', '-d', '--no-build']:
        assert all(image(name) in images for name in services)
    else:
        raise AssertionError(args)
else:
    raise AssertionError(args)
''')
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    monkeypatch.setenv("TEST_DEPLOYMENT_ROOT", str(tmp_path))
    if failure:
        monkeypatch.setenv("TEST_DEPLOYMENT_FAILURE", failure)
    result = subprocess.run(["bash", str(tmp_path / "start.sh")], capture_output=True, text=True)
    evidence = json.loads((runtime / "startup.json").read_text())
    assert (result.returncode == 0) == (failure is None), result.stderr
    assert evidence["exit_code"] == result.returncode
    commands = [json.loads(line) for line in (tmp_path / "commands.jsonl").read_text().splitlines()]
    assert not any(command[:2] == ["image", "tag"] for command in commands)
    assert sum(command[-3:] == ["up", "-d", "--no-build"] for command in commands) == (failure is None)
    if failure is None:
        assert sum("build" in command for command in commands) == 3
        (tmp_path / "commands.jsonl").unlink()
        repeated = subprocess.run(["bash", str(tmp_path / "start.sh")], capture_output=True, text=True)
        assert repeated.returncode == 0, repeated.stderr
        commands = [json.loads(line) for line in (tmp_path / "commands.jsonl").read_text().splitlines()]
        assert not any("build" in command for command in commands)
        assert sum(command[:2] == ["image", "inspect"] for command in commands) == 1
