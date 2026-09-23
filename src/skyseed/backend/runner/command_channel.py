"""Parent command transport and conservative helper ownership."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
from threading import Condition, RLock
from time import monotonic, monotonic_ns
from typing import Any, Mapping, Sequence


_PROJECT_LABEL = "com.docker.compose.project"


_SERVICE_LABEL = "com.docker.compose.service"


_RUNNER_HELPER_LABEL = "org.skyseed.runner.foundation"


_RUNNER_PRIVATE_ROOT = Path(__file__).parent


_PRIVILEGED_COMMAND_PATH = _RUNNER_PRIVATE_ROOT / "runtime_command.py"


_TRAFFIC_TELEMETRY_COLLECTOR_PATH = (
    _RUNNER_PRIVATE_ROOT.parent / "bpf" / "runner" / "traffic_telemetry_collector.py"
)


def _execute(
    command: Sequence[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        tuple(command), input=input_text, text=True, capture_output=True, check=False
    )


def _require_success(
    result: subprocess.CompletedProcess[str], *, context: str
) -> subprocess.CompletedProcess[str]:
    if result.returncode != 0:
        raise RuntimeError(
            f"{context} failed ({result.returncode}); "
            f"stdout={result.stdout!r}; stderr={result.stderr!r}"
        )
    return result


class _PrivilegedRuntimeCommandError(RuntimeError):
    """Preserve a private child failure classification across docker exec."""

    def __init__(
        self,
        message: str,
        *,
        remote_error_type: object,
        remote_evidence: object = None,
    ) -> None:
        super().__init__(message)
        self.remote_error_type = remote_error_type
        self.remote_evidence = remote_evidence


class _PrivilegedHelper:
    """Own one privileged container and one persistent command stream."""

    def __init__(
        self,
        *,
        docker_executable: str,
        name: str,
        foundation_token: str,
        image_id: str,
        forwarding_backend="bpf",
    ) -> None:
        self._backend = forwarding_backend
        self._docker = docker_executable
        self._name = name
        self._foundation_token = foundation_token
        self._image_id = image_id
        self._started = False
        self._process: subprocess.Popen[str] | None = None
        self._command_lock = RLock()
        self._channel_condition = Condition(RLock())
        self._channel_busy = False
        self._main_waiters = 0
        self._main_deadline = None

    def _set_main_deadline(self, deadline):
        with self._channel_condition:
            self._main_deadline = deadline
            self._channel_condition.notify_all()

    @contextmanager
    def _channel(self, action):
        main = action == "activate_boundary"
        background = action in {"prewrite_generation", "reclaim_generations"}
        with self._channel_condition:
            self._main_waiters += int(main)
            try:
                while self._channel_busy or (background and (
                    self._main_waiters or (self._main_deadline is not None
                    and monotonic() >= self._main_deadline - 0.02)
                )):
                    self._channel_condition.wait(0.01)
                self._channel_busy = True
            finally:
                self._main_waiters -= int(main)
        try:
            yield
        finally:
            with self._channel_condition:
                self._channel_busy = False
                self._channel_condition.notify_all()

    def start(self) -> None:
        if self._started:
            raise RuntimeError("Runner privileged helper is already started")
        if _execute((self._docker, "inspect", self._name)).returncode == 0:
            raise RuntimeError(f"Runner-owned helper name already exists: {self._name}")
        result = _execute(
            (
                self._docker,
                "run",
                "--detach",
                "--name",
                self._name,
                "--label",
                f"{_RUNNER_HELPER_LABEL}={self._foundation_token}",
                "--label",
                f"{_PROJECT_LABEL}=",
                "--label",
                f"{_SERVICE_LABEL}=",
                "--privileged",
                "--pid",
                "host",
                "--network",
                "host",
                "--mount",
                "type=bind,source=/,target=/host,readonly",
                *(("--mount", "type=bind,source=/sys/fs/bpf,target=/host/sys/fs/bpf") if self._backend == "bpf" else ()),
                "--entrypoint",
                "/bin/sh",
                self._image_id,
                "-c",
                "while :; do sleep 3600; done",
            )
        )
        if result.returncode != 0:
            partial = _execute((self._docker, "inspect", self._name))
            if partial.returncode == 0:
                values = json.loads(partial.stdout)
                labels = values[0].get("Config", {}).get("Labels") or {}
                if labels.get(_RUNNER_HELPER_LABEL) == self._foundation_token:
                    _execute((self._docker, "rm", "--force", self._name))
        _require_success(result, context="Runner privileged helper startup")
        self._started = True
        try:
            self._process = subprocess.Popen(
                (self._docker, "exec", "--interactive", self._name,
                 "chroot", "/host", sys.executable, "-B", str(_PRIVILEGED_COMMAND_PATH), "serve-" + self._backend),
                text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=1,
            )
        except BaseException:
            self.stop()
            raise

    def assert_current(self) -> None:
        if not self._started:
            raise RuntimeError("Runner privileged helper is not started")
        result = _require_success(
            _execute((self._docker, "inspect", self._name)),
            context="Runner privileged helper inspection",
        )
        values = json.loads(result.stdout)
        if len(values) != 1:
            raise RuntimeError("Runner privileged helper inspection is ambiguous")
        labels = values[0].get("Config", {}).get("Labels") or {}
        if (
            labels.get(_RUNNER_HELPER_LABEL) != self._foundation_token
            or not values[0].get("State", {}).get("Running")
        ):
            raise RuntimeError("Runner privileged helper evidence is stale")

    def execute(self, action: str, request: Mapping[str, Any] | str) -> dict[str, Any]:
        stamps = {"command_start_monotonic_ns": monotonic_ns()}
        response = None
        request_bytes = response_bytes = None

        def observation():
            stamps["command_end_monotonic_ns"] = monotonic_ns()
            values = {**stamps, "request_bytes": request_bytes, "response_bytes": response_bytes,
                      "helper": response.get("helper_timing") if isinstance(response, Mapping) else None}
            for label, first, last in (
                ("command_seconds", "command_start", "command_end"),
                ("json_encode_seconds", "encode_start", "encode_end"),
                ("channel_write_seconds", "write_start", "write_end"),
                ("channel_wait_seconds", "write_end", "read_end"),
                ("response_decode_seconds", "decode_start", "decode_end"),
            ):
                start = stamps.get(first + "_monotonic_ns")
                end = stamps.get(last + "_monotonic_ns")
                values[label] = None if start is None or end is None else (end - start) / 1e9
            return values

        try:
            with self._channel(action), self._command_lock:
                process = self._process
                if process is None or process.poll() is not None:
                    raise RuntimeError("Runner privileged execution channel is unavailable")
                assert process.stdin is not None and process.stdout is not None
                stamps["encode_start_monotonic_ns"] = monotonic_ns()
                if isinstance(request, str):
                    if action != "activate_boundary":
                        raise ValueError("only immutable activation requests are preencoded")
                    payload = request
                    stamps["encode_end_monotonic_ns"] = stamps["encode_start_monotonic_ns"]
                else:
                    payload = json.dumps(
                        {"action": action, "request": request},
                        default=lambda value: dict(value) if isinstance(value, Mapping) else value,
                        separators=(",", ":"),
                    ) + "\n"
                    stamps["encode_end_monotonic_ns"] = monotonic_ns()
                request_bytes = len(payload)  # JSON's default ASCII encoding.
                stamps["write_start_monotonic_ns"] = monotonic_ns()
                process.stdin.write(payload)
                process.stdin.flush()
                stamps["write_end_monotonic_ns"] = monotonic_ns()
                line = process.stdout.readline()
                stamps["read_end_monotonic_ns"] = monotonic_ns()
                if not line:
                    raise RuntimeError("Runner privileged execution channel closed before result")
                response_bytes = len(line)
                stamps["decode_start_monotonic_ns"] = monotonic_ns()
                response = json.loads(line)
                stamps["decode_end_monotonic_ns"] = monotonic_ns()
                if response.get("status") != "ok":
                    raise _PrivilegedRuntimeCommandError(
                        str(response.get("error")), remote_error_type=response.get("error_type"),
                        remote_evidence=response.get("evidence"),
                    )
                result = response.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("Runner kernel executor omitted its mutation result")
        except BaseException as error:
            error.command_timing = observation()
            raise
        result["command_timing"] = observation()
        return result

    def start_telemetry_collector(
        self, request: Mapping[str, Any]
    ) -> subprocess.Popen[str]:
        """Start one supervised long-lived command in the owned helper."""

        self.assert_current()
        process = subprocess.Popen(
            (
                self._docker,
                "exec",
                "--interactive",
                self._name,
                "chroot",
                "/host",
                sys.executable,
                "-B",
                str(_TRAFFIC_TELEMETRY_COLLECTOR_PATH),
            ),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        assert process.stdin is not None
        try:
            process.stdin.write(json.dumps(request, sort_keys=True) + "\n")
            process.stdin.flush()
        except BaseException:
            process.terminate()
            process.wait()
            raise
        return process

    def stop(self) -> None:
        if not self._started:
            return
        process = self._process
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            process.wait()
            if process.stdout is not None:
                process.stdout.close()
            self._process = None
        result = _require_success(
            _execute((self._docker, "inspect", self._name)),
            context="Runner privileged helper cleanup inspection",
        )
        values = json.loads(result.stdout)
        if len(values) != 1:
            raise RuntimeError("Runner helper cleanup ownership is ambiguous")
        labels = values[0].get("Config", {}).get("Labels") or {}
        if labels.get(_RUNNER_HELPER_LABEL) != self._foundation_token:
            raise RuntimeError("Runner helper cleanup ownership is stale")
        _require_success(
            _execute((self._docker, "rm", "--force", self._name)),
            context="Runner privileged helper cleanup",
        )
        self._started = False
