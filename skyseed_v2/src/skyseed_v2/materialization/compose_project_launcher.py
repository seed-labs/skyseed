"""Bounded materialization of one already-generated Compose project."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from json import JSONDecodeError, loads
from math import isfinite
from pathlib import Path
from re import fullmatch
from subprocess import CompletedProcess, run
from time import monotonic, sleep
from typing import Callable, Iterable, Sequence


Command = tuple[str, ...]
CommandRunner = Callable[[Sequence[str]], CompletedProcess[str]]


class ComposeLaunchStrategy(str, Enum):
    """Select one explicit Compose project startup strategy.

    Layer: materialization integration value. Launcher callers create/select it
    and `ComposeProjectLauncher` consumes it. It is not fallback policy,
    compilation-stage input or domain state and is public from the
    materialization module.
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
    scenario or runtime entity model and is public from materialization.
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
    Simulator result or default batching policy and is public only from the
    materialization module.
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
    Emulator entity, deployment entity or Simulator result and is public only from the
    materialization module.
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
    from the materialization module as the stable failure boundary.
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
    Simulator or recovery coordinator. It is public from the materialization
    module because already-compiled project startup needs a bounded adapter.

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
