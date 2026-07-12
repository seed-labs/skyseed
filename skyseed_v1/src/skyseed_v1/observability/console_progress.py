"""Low-cost console projection of the durable runtime progress snapshot."""

from __future__ import annotations

import json
import shlex
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO


def progress_identity(snapshot: Mapping[str, Any]) -> tuple[object, ...]:
    """Return the fields whose change is useful to a human operator."""
    detail = dict(snapshot.get("phase_detail") or {})
    return (
        snapshot.get("current_phase"),
        detail.get("stage_id"),
        detail.get("stage_number"),
        snapshot.get("runtime_epochs_completed"),
        snapshot.get("parameter_epochs_completed"),
        snapshot.get("last_event_type"),
    )


def render_progress_line(snapshot: Mapping[str, Any]) -> str:
    phase = str(snapshot.get("current_phase") or "starting")
    runtime_epochs = int(snapshot.get("runtime_epochs_completed") or 0)
    parameter_epochs = int(snapshot.get("parameter_epochs_completed") or 0)
    context = dict(snapshot.get("current_epoch_context") or {})
    epoch = str(context.get("segment_epoch_key") or "")
    event = str(snapshot.get("last_event_type") or "")
    detail = dict(snapshot.get("phase_detail") or {})
    fields = [f"[skyseed] phase={phase}"]
    stage_id = str(detail.get("stage_id") or "")
    if stage_id:
        fields.append(f"stage={stage_id}")
    stage_number = int(detail.get("stage_number") or 0)
    stage_total = int(detail.get("stage_total") or 0)
    if stage_number and stage_total:
        fields.append(f"batch={stage_number}/{stage_total}")
    stage_type = str(detail.get("stage_type") or "")
    if stage_type:
        fields.append(f"type={stage_type}")
    service_count = int(detail.get("service_count") or 0)
    if service_count:
        fields.append(f"services={service_count}")
    target_names = [str(name) for name in list(detail.get("container_names") or [])]
    if target_names:
        omitted = int(detail.get("omitted_container_count") or 0)
        suffix = f",+{omitted}" if omitted else ""
        fields.append(f"targets={','.join(target_names)}{suffix}")
    if runtime_epochs or parameter_epochs or epoch:
        fields.append(f"epochs=runtime:{runtime_epochs},parameter:{parameter_epochs}")
    if epoch:
        fields.append(f"current={epoch}")
    if event:
        fields.append(f"event={event}")
    wall_seconds = float(detail.get("wall_seconds") or 0.0)
    if wall_seconds:
        fields.append(f"wall={wall_seconds:.3f}s")
    return " ".join(fields)


def render_phase_heartbeat_line(snapshot: Mapping[str, Any]) -> str:
    """Render bounded liveness detail for a phase whose identity is unchanged."""
    phase = str(snapshot.get("current_phase") or "starting")
    elapsed = int(float(snapshot.get("seconds_in_phase") or 0.0))
    fields = [f"[skyseed] phase={phase}", "status=working", f"elapsed={elapsed}s"]
    children = [
        dict(child)
        for child in list(snapshot.get("child_processes") or [])
        if _is_operator_activity(str(dict(child).get("cmd") or ""))
    ]
    if children:
        child_command = str(children[0].get("cmd") or "")
        fields.append(f"activity={_child_activity(child_command)}")
        fields.append(
            "command="
            + json.dumps(
                _child_command_summary(child_command),
                ensure_ascii=False,
            )
        )
    docker_runtime = dict(snapshot.get("docker_runtime") or {})
    if docker_runtime.get("ok") is True:
        total = int(docker_runtime.get("compose_project_container_count") or 0)
        running = int(docker_runtime.get("compose_project_running_count") or 0)
        if phase.startswith("compose") or total or running:
            fields.append(f"containers=running:{running},created:{total}")
            sample_names = [
                str(dict(item).get("name") or "")
                for item in list(docker_runtime.get("sample") or [])[:3]
                if str(dict(item).get("name") or "")
            ]
            if sample_names:
                fields.append(f"sample={','.join(sample_names)}")
    if phase == "compose_build":
        fields.append("detail=building_images_containers_start_at_compose_up")
    return " ".join(fields)


def _child_activity(command: str) -> str:
    """Collapse an implementation command into a stable operator-facing label."""

    try:
        parts = shlex.split(str(command or ""))
    except ValueError:
        parts = str(command or "").split()
    if not parts:
        return "subprocess"
    shell_payload = _shell_payload(parts)
    semantic_payload = _semantic_shell_payload(shell_payload)
    executable_names = {Path(part).name for part in parts}
    if "nsenter" in executable_names:
        if _command_starts_with(semantic_payload, "ip", "addr"):
            return "namespace_address_update"
        if _command_starts_with(semantic_payload, "ip", "link"):
            return "namespace_link_update"
        if _command_starts_with(semantic_payload, "ip", "route"):
            return "namespace_route_update"
        if _command_starts_with(semantic_payload, "tc"):
            return "namespace_qdisc_update"
        return "namespace_network_update"
    direct_payload = semantic_payload or _semantic_direct_payload(parts)
    if direct_payload:
        if _command_starts_with(direct_payload, "ip", "addr"):
            return "host_address_update"
        if _command_starts_with(direct_payload, "ip", "link"):
            return "host_link_update"
        if _command_starts_with(direct_payload, "ip", "route"):
            return "host_route_update"
        if _command_starts_with(direct_payload, "tc"):
            return "host_qdisc_update"
    if parts[:2] in (["docker-compose", "build"], ["docker", "compose"]):
        return "compose_image_build"
    if "docker-compose" in parts[0] or parts[:2] == ["docker", "compose"]:
        return "compose_materialization"
    if "-m" in parts:
        module_index = parts.index("-m") + 1
        module = parts[module_index] if module_index < len(parts) else ""
        labels = {
            "skyseed_v1.operations.preview": "horizon_preview",
            "skyseed_v1.runtime.maintenance_worker": "horizon_maintenance_worker",
        }
        if module:
            return labels.get(module, f"python_module:{module}")
    executable = Path(parts[0]).name
    if executable in {"clang", "clang-10", "clang-11", "clang-12"}:
        return "bpf_compile"
    return executable or "subprocess"


def _shell_payload(parts: list[str]) -> str:
    for option in ("-lc", "-c"):
        if option in parts:
            index = parts.index(option) + 1
            if index < len(parts):
                return " ".join(str(parts[index]).split())
    return ""


def _semantic_shell_payload(command: str) -> str:
    """Skip shell setup clauses and expose the first network operation."""

    normalized = " ".join(str(command or "").split())
    for marker in ("ip addr ", "ip link ", "ip route ", "tc "):
        index = normalized.find(marker)
        if index >= 0:
            return normalized[index:]
    return normalized


def _semantic_direct_payload(parts: list[str]) -> str:
    """Expose a direct command even when it is wrapped by sudo flags."""

    for index, token in enumerate(parts):
        if Path(token).name in {"ip", "tc"}:
            return " ".join(parts[index:])
    return ""


def _is_operator_activity(command: str) -> bool:
    """Exclude monitor probes and exited children from operator activity."""

    normalized = " ".join(str(command or "").split())
    if not normalized or "<defunct>" in normalized:
        return False
    try:
        parts = shlex.split(normalized)
    except ValueError:
        parts = normalized.split()
    executable_names = {Path(part).name for part in parts}
    if "ps" in executable_names and "--ppid" in parts:
        return False
    if "docker" in executable_names and "ps" in parts and "--filter" in parts:
        return False
    return True


def _command_starts_with(command: str, *prefix: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return parts[: len(prefix)] == list(prefix)


def _bounded_command_text(value: str, *, max_chars: int = 180) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= max_chars:
        return normalized
    candidate = normalized[: max_chars - 2].rsplit(" ", 1)[0].rstrip(";,")
    return (candidate or normalized[: max_chars - 2]).rstrip() + " …"


def _child_command_summary(command: str) -> str:
    """Return a bounded, operator-facing command without noisy arguments."""

    try:
        parts = shlex.split(str(command or ""))
    except ValueError:
        parts = str(command or "").split()
    if not parts:
        return "subprocess"

    if "-m" in parts:
        module_index = parts.index("-m") + 1
        if module_index < len(parts):
            executable = Path(parts[0]).name or "python"
            return f"{executable} -m {parts[module_index]}"

    compose_offset = 0
    compose_name = ""
    if "docker-compose" in Path(parts[0]).name:
        compose_name = "docker-compose"
    elif parts[:2] == ["docker", "compose"]:
        compose_name = "docker compose"
        compose_offset = 1
    if compose_name:
        actions = {"build", "config", "down", "logs", "ps", "pull", "up"}
        action_index = next(
            (
                index
                for index, token in enumerate(parts[compose_offset + 1 :], compose_offset + 1)
                if token in actions
            ),
            None,
        )
        if action_index is not None:
            tail = parts[action_index : action_index + 5]
            return _bounded_command_text(" ".join([compose_name, *tail]))
        return compose_name

    payload = _shell_payload(parts)
    if payload:
        payload_option = "-lc" if "-lc" in parts else "-c"
        payload_index = parts.index(payload_option)
        prefix = [Path(parts[0]).name, *parts[1 : payload_index + 1]]
        return _bounded_command_text(" ".join([*prefix, payload]))

    displayed = [Path(parts[0]).name, *parts[1:12]]
    return _bounded_command_text(" ".join(displayed))


def render_launch_lines(
    *,
    profile_name: str,
    run_id: str,
    run_dir: Path,
    profile: Mapping[str, Any],
) -> tuple[str, ...]:
    """Render the immediate acknowledgement before the child writes progress."""
    args = dict(profile.get("runner_args") or {})
    selectors = dict(profile.get("validation_selector") or {})

    def selected_count(kind: str) -> int:
        return len(list(dict(selectors.get(kind) or {}).get("ids") or []))

    return (
        f"[skyseed] launch=accepted profile={profile_name} run_id={run_id}",
        f"[skyseed] output={Path(run_dir)}",
        "[skyseed] shape="
        f"shell:{args.get('planned_shell_id') or '?'} "
        f"satellites:{int(args.get('max_satellites') or 0)} "
        f"uts:{selected_count('ut')} "
        f"gateways:{selected_count('gateway')} "
        f"zones:{selected_count('zone')} "
        f"horizons:{int(args.get('loop_count') or 1)}x"
        f"{float(args.get('horizon_seconds') or 0.0):g}s "
        f"sample:{float(args.get('sample_interval') or 0.0):g}s",
        "[skyseed] phase=initial_preview status=starting "
        "detail=topology_planning_and_seed_compile_no_network_yet",
        "[skyseed] control=Ctrl-C_requests_interruption_and_cleanup",
    )


class ConsoleProgressMonitor:
    """Poll and coalesce progress in a daemon thread, never on the run path.

    A blocked terminal may stall this disposable projection thread and lose
    display freshness. It cannot backpressure the separately running network
    subprocess.
    """

    def __init__(
        self,
        path: Path,
        *,
        event_path: Path | None = None,
        stream: TextIO | None = None,
        interval_seconds: float = 1.0,
        waiting_phase: str | None = None,
        waiting_heartbeat_seconds: float = 15.0,
        initial_lines: tuple[str, ...] = (),
        emit: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.event_path = Path(event_path) if event_path else self.path.with_name(
            "events.raw.jsonl"
        )
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.waiting_phase = str(waiting_phase or "").strip()
        self.waiting_heartbeat_seconds = max(
            self.interval_seconds,
            float(waiting_heartbeat_seconds),
        )
        self.initial_lines = tuple(str(line) for line in initial_lines)
        output = stream if stream is not None else sys.stderr
        self._emit = emit or (lambda line: print(line, file=output, flush=True))
        self._stop = threading.Event()
        self._event_offset = (
            self.event_path.stat().st_size if self.event_path.is_file() else 0
        )
        self._runtime_epoch_count = 0
        self._thread = threading.Thread(
            target=self._run,
            name="skyseed-console-progress",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds * 2.0))

    def _run(self) -> None:
        for line in self.initial_lines:
            self._emit(line)
        last_identity: tuple[object, ...] | None = None
        started = time.monotonic()
        last_waiting_emit = started
        last_phase_emit = started
        last_heartbeat_line = ""
        snapshot_seen = False
        while not self._stop.wait(self.interval_seconds):
            self._emit_completed_epochs()
            try:
                snapshot = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                now = time.monotonic()
                if (
                    self.waiting_phase
                    and not snapshot_seen
                    and now - last_waiting_emit >= self.waiting_heartbeat_seconds
                ):
                    last_waiting_emit = now
                    self._emit(
                        f"[skyseed] phase={self.waiting_phase} status=working "
                        f"elapsed={int(now - started)}s progress_snapshot=pending"
                    )
                continue
            snapshot_seen = True
            now = time.monotonic()
            identity = progress_identity(snapshot)
            if identity == last_identity:
                if now - last_phase_emit >= self.waiting_heartbeat_seconds:
                    last_phase_emit = now
                    heartbeat_line = render_phase_heartbeat_line(snapshot)
                    if heartbeat_line != last_heartbeat_line:
                        self._emit(heartbeat_line)
                        last_heartbeat_line = heartbeat_line
                continue
            last_identity = identity
            last_phase_emit = now
            last_heartbeat_line = ""
            self._emit(render_progress_line(snapshot))
        # Drain epoch completions written between the final poll and process
        # exit. This still runs on the daemon thread, never on the caller.
        self._emit_completed_epochs()

    def _emit_completed_epochs(self) -> None:
        try:
            size = self.event_path.stat().st_size
            if size < self._event_offset:
                self._event_offset = 0
                self._runtime_epoch_count = 0
            with self.event_path.open("r", encoding="utf-8") as handle:
                handle.seek(self._event_offset)
                for line in handle:
                    if "runtime_epoch_result" not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("event_type") != "runtime_epoch_result":
                        continue
                    self._runtime_epoch_count += 1
                    self._emit(
                        "[skyseed] epoch_completed="
                        f"{self._runtime_epoch_count} "
                        f"epoch={event.get('segment_epoch_key') or '?'} "
                        f"success={event.get('connectivity_gate_passed')} "
                        f"wall={float(event.get('transition_wall_seconds') or 0.0):.3f}s"
                    )
                self._event_offset = handle.tell()
        except (FileNotFoundError, OSError):
            return
