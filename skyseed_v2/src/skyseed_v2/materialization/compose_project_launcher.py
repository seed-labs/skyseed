"""Bounded materialization of one already-generated Compose project."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from json import JSONDecodeError, loads
from math import isfinite
from pathlib import Path
from subprocess import CompletedProcess, run
from time import monotonic, sleep
from typing import Callable, Iterable, Sequence


Command = tuple[str, ...]
CommandRunner = Callable[[Sequence[str]], CompletedProcess[str]]


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
    """Explicit state classes for every service in one Compose project."""

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
    """One successfully materialized and verified sequential batch."""

    batch_index: int
    services: tuple[str, ...]
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class ComposeMaterializationResult:
    """Evidence that the complete expected Compose project reached its states."""

    compose_file: Path
    project_name: str
    batch_size: int
    compose_sha256: str
    declared_services: tuple[str, ...]
    long_running_services: tuple[str, ...]
    successful_exit_helpers: tuple[str, ...]
    batches: tuple[ComposeBatchResult, ...]
    total_elapsed_seconds: float


class ComposeMaterializationError(RuntimeError):
    """Fail-closed materialization error with optional batch context."""

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
    """Materialize all services from one Compose file in bounded batches.

    This tool neither compiles nor edits Compose. The caller must explicitly
    classify every declared service as long-running or a successful-exit
    helper.
    """

    def __init__(
        self,
        *,
        compose_file: Path,
        project_name: str,
        expectations: ComposeServiceExpectations,
        batch_size: int,
        command_runner: CommandRunner | None = None,
        compose_executable: str = "docker-compose",
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
        self._batch_size = batch_size
        self._runner = command_runner or _default_command_runner
        self._compose_executable = _non_empty_text(
            compose_executable, "compose_executable"
        )
        self._docker_executable = _non_empty_text(
            docker_executable, "docker_executable"
        )
        self._verification_attempts = verification_attempts
        self._verification_interval_seconds = interval
        self._clock = monotonic_clock
        self._sleep = sleeper

    def materialize(self) -> ComposeMaterializationResult:
        """Start every declared service once and verify the complete project."""

        compose_digest = self._compose_digest()
        declared_services = self._declared_services()
        self._validate_expectations(declared_services)

        materialization_started = self._clock()
        completed: set[str] = set()
        batch_results: list[ComposeBatchResult] = []
        for offset in range(0, len(declared_services), self._batch_size):
            services = declared_services[offset : offset + self._batch_size]
            batch_index = offset // self._batch_size + 1
            batch_started = self._clock()
            self._assert_compose_digest(compose_digest, batch_index, services)
            self._execute(
                self._compose_command("up", "-d", *services),
                context=f"batch {batch_index} up",
                batch_index=batch_index,
                services=services,
            )
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
                    elapsed_seconds=self._clock() - batch_started,
                )
            )

        self._wait_for_expected_states(
            declared_services,
            declared_services=declared_services,
            batch_index=None,
            batch_services=(),
            final=True,
        )
        self._assert_compose_digest(compose_digest, None, ())
        return ComposeMaterializationResult(
            compose_file=self._compose_file,
            project_name=self._project_name,
            batch_size=self._batch_size,
            compose_sha256=compose_digest,
            declared_services=declared_services,
            long_running_services=self._expectations.long_running,
            successful_exit_helpers=(
                self._expectations.successful_exit_helpers
            ),
            batches=tuple(batch_results),
            total_elapsed_seconds=self._clock() - materialization_started,
        )

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
            self._compose_executable,
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
