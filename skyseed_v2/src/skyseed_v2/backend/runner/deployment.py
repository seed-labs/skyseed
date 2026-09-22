"""Bind deployed containers to exact process and network namespace identities."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import sys
from typing import Any, Mapping, Sequence
from skyseed_v2.shared import NodeId
from skyseed_v2.backend.execution_materials.actions import _PhysicalWorldProjection
from .command_channel import (_execute, _require_success, _PROJECT_LABEL,
                              _SERVICE_LABEL, _PRIVILEGED_COMMAND_PATH)

_ASN_LABEL = "org.seedsecuritylabs.seedemu.meta.asn"


_ENTITY_TYPE_LABEL = "org.seedsecuritylabs.seedemu.meta.skyseed.entity_type"


_SATELLITE_ID_LABEL = "org.seedsecuritylabs.seedemu.meta.skyseed.satellite_id"


_GROUND_STATION_ID_LABEL = "org.seedsecuritylabs.seedemu.meta.skyseed.ground_station_id"


@dataclass(frozen=True, slots=True)
class _RunnerRuntimeBinding:
    node_id: NodeId
    service_name: str
    container_id: str
    container_name: str
    image_id: str
    pid: int
    process_start_time_ticks: int
    netns_inode: int
    started_at: str


class _Deployment:
    """Own discovery of one actual deployment's startup identities."""

    def __init__(self, docker: str, compose_project_name: str) -> None:
        self._docker = docker
        self._compose_project_name = compose_project_name

    def discover(
        self, projection: _PhysicalWorldProjection
    ) -> dict[NodeId, _RunnerRuntimeBinding]:
        result = _require_success(
            _execute((
                self._docker, "ps", "--all", "--quiet", "--filter",
                f"label={_PROJECT_LABEL}={self._compose_project_name}",
            )),
            context="Runner deployment discovery",
        )
        container_ids = tuple(
            value.strip() for value in result.stdout.splitlines() if value.strip()
        )
        if not container_ids:
            raise RuntimeError(
                "current Compose project has no containers; start the generated deployment first"
            )
        inspected = self._inspect(container_ids)
        candidates = []
        seen_node_ids: set[NodeId] = set()
        for value in inspected:
            labels = value.get("Config", {}).get("Labels") or {}
            if labels.get(_PROJECT_LABEL) != self._compose_project_name:
                raise RuntimeError("discovered container has the wrong project")
            entity_type = labels.get(_ENTITY_TYPE_LABEL)
            if entity_type not in {"Satellite", "GroundStation"}:
                continue
            service_name = labels.get(_SERVICE_LABEL)
            if not isinstance(service_name, str) or not service_name:
                raise RuntimeError("SkySEED entity container has no Compose service")
            state = value.get("State") or {}
            pid = state.get("Pid")
            if not state.get("Running") or not isinstance(pid, int) or pid <= 0:
                raise RuntimeError("SkySEED entity container is not running")
            try:
                asn = int(labels[_ASN_LABEL])
                node_id = (
                    NodeId(asn, "satellite", labels[_SATELLITE_ID_LABEL])
                    if entity_type == "Satellite"
                    else NodeId(
                        asn, "ground-station",
                        labels[_GROUND_STATION_ID_LABEL],
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError(
                    "SkySEED entity container labels are incomplete"
                ) from error
            if node_id in seen_node_ids:
                raise RuntimeError(f"duplicate live binding for {node_id.value}")
            seen_node_ids.add(node_id)
            candidates.append((
                node_id, service_name, value["Id"],
                value["Name"].removeprefix("/"), value["Image"], pid,
                state.get("StartedAt", ""),
            ))
        expected = set(projection.node_ids)
        if seen_node_ids != expected:
            missing = sorted(value.value for value in expected - seen_node_ids)
            unexpected = sorted(value.value for value in seen_node_ids - expected)
            raise RuntimeError(
                "live NodeId bindings do not match compiled PhysicalWorld: "
                f"missing={missing}, unexpected={unexpected}"
            )
        first_image = min(candidates, key=lambda value: value[0].value)[4]
        incarnations = self._capture_privileged_incarnations(
            image_id=first_image,
            pids=tuple(candidate[5] for candidate in candidates),
        )
        bindings = {
            node_id: _RunnerRuntimeBinding(
                node_id=node_id, service_name=service_name,
                container_id=container_id, container_name=container_name,
                image_id=image_id, pid=pid,
                process_start_time_ticks=start_ticks,
                netns_inode=netns_inode, started_at=started_at,
            )
            for (
                node_id, service_name, container_id, container_name,
                image_id, pid, started_at,
            ), (start_ticks, netns_inode) in zip(candidates, incarnations)
        }
        if set(bindings) != expected:
            missing = sorted(value.value for value in expected - set(bindings))
            unexpected = sorted(value.value for value in set(bindings) - expected)
            raise RuntimeError(
                "live NodeId bindings do not match compiled PhysicalWorld: "
                f"missing={missing}, unexpected={unexpected}"
            )
        return bindings

    def _capture_privileged_incarnations(
        self, *, image_id: str, pids: tuple[int, ...]
    ) -> list[tuple[int, int]]:
        """Use one ephemeral privileged process to capture the complete PID set."""

        if not pids:
            raise RuntimeError("Runner discovered no process incarnations to capture")
        result = _execute(
            (
                self._docker,
                "run",
                "--rm",
                "--interactive",
                "--label",
                f"{_PROJECT_LABEL}=",
                "--label",
                f"{_SERVICE_LABEL}=",
                "--privileged",
                "--pid",
                "host",
                "--network",
                "none",
                "--mount",
                "type=bind,source=/,target=/host,readonly",
                "--entrypoint",
                "chroot",
                image_id,
                "/host",
                sys.executable,
                "-B",
                str(_PRIVILEGED_COMMAND_PATH),
                "capture_incarnations",
            ),
            input_text=json.dumps({"pids": list(pids)}, sort_keys=True),
        )
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "Runner privileged incarnation capture returned invalid evidence; "
                f"stdout={result.stdout!r}; stderr={result.stderr!r}"
            ) from error
        if result.returncode != 0 or response.get("status") != "ok":
            raise RuntimeError(
                "Runner privileged incarnation capture failed: "
                f"{response.get('error', response)!r}; "
                f"traceback={response.get('traceback', '')!r}"
            )
        payload = response.get("result")
        values = payload.get("incarnations") if isinstance(payload, Mapping) else None
        if (
            not isinstance(payload, Mapping)
            or payload.get("capture_mode") != "single-privileged-batch"
            or payload.get("requested_pid_count") != len(pids)
            or payload.get("captured_pid_count") != len(pids)
            or not isinstance(values, list)
            or len(values) != len(pids)
        ):
            raise RuntimeError("Runner privileged incarnation capture is incomplete")
        by_pid: dict[int, tuple[int, int]] = {}
        for value in values:
            if not isinstance(value, Mapping):
                raise RuntimeError("Runner privileged incarnation evidence is malformed")
            pid = value.get("pid")
            start = value.get("process_start_time_ticks")
            netns = value.get("netns_inode")
            if (
                isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid <= 0
                or isinstance(start, bool)
                or not isinstance(start, int)
                or start <= 0
                or isinstance(netns, bool)
                or not isinstance(netns, int)
                or netns <= 0
                or pid in by_pid
            ):
                raise RuntimeError("Runner privileged incarnation evidence is invalid")
            by_pid[pid] = (start, netns)
        if set(by_pid) != set(pids) or len(by_pid) != len(pids):
            raise RuntimeError("Runner privileged incarnation PID set is incomplete")
        if len({value[1] for value in by_pid.values()}) != len(by_pid):
            raise RuntimeError(
                "Runner privileged incarnation netns evidence is duplicated"
            )
        return [by_pid[pid] for pid in pids]

    def foundation_identity(
        self, bindings: Mapping[NodeId, _RunnerRuntimeBinding]
    ) -> str:
        facts = [
            self._compose_project_name,
            *(f"{node_id.value}|{binding.container_id}|{binding.image_id}|"
              f"{binding.pid}|{binding.process_start_time_ticks}|"
              f"{binding.netns_inode}|{binding.started_at}"
              for node_id, binding in sorted(
                  bindings.items(), key=lambda item: item[0].value
              )),
        ]
        return "foundation-" + sha256("\n".join(facts).encode()).hexdigest()

    def _inspect(self, container_ids: Sequence[str]) -> list[dict[str, Any]]:
        result = _require_success(
            _execute((self._docker, "inspect", *container_ids)),
            context="Runner container inspection",
        )
        values = json.loads(result.stdout)
        if not isinstance(values, list) or len(values) != len(container_ids):
            raise RuntimeError("Runner container inspection is incomplete")
        return values
