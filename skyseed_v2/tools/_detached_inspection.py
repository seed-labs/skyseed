"""Run heavy repository-local Inspection Tools outside domain processes.

This module is intentionally private and small.  It does not own domain
progression: callers submit already-frozen immutable values, and a forked
process first persists a deterministic detached snapshot before projecting,
rendering, auditing, and atomically publishing one Tool bundle.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
import multiprocessing
import os
from pathlib import Path
import pickle
import resource
import shutil
from time import perf_counter, sleep
from typing import Any


_HANDOFF_FORMAT_VERSION = 1
_SNAPSHOT_ENCODING = "gzip-pickle-v5"
_SUPPORTED_TASKS = frozenset({"planner", "compiler", "observation"})


class _DetachedInspectionExecutor:
    """Schedule a bounded number of family-level Tool worker processes."""

    def __init__(self, evidence_directory: Path, *, max_workers: int = 2) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int):
            raise TypeError("max_workers must be an integer")
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        if "fork" not in multiprocessing.get_all_start_methods():
            raise RuntimeError("detached Inspection workers require fork support")
        self._context = multiprocessing.get_context("fork")
        self._evidence_directory = Path(evidence_directory).resolve()
        self._task_directory = self._evidence_directory / "inspection-tasks"
        self._handoff_directory = self._evidence_directory / "tool-handoffs"
        self._task_directory.mkdir(parents=True, exist_ok=True)
        self._handoff_directory.mkdir(parents=True, exist_ok=True)
        self._max_workers = max_workers
        self._active: list[dict[str, object]] = []
        self._completed: list[dict[str, object]] = []
        self._closed = False

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def submit(
        self,
        task: str,
        source: Mapping[str, object],
        output_directory: Path,
    ) -> dict[str, object]:
        """Schedule one Tool from an immutable fork snapshot."""

        if self._closed:
            raise RuntimeError("detached Inspection executor is already closed")
        if task not in _SUPPORTED_TASKS:
            raise ValueError(f"unsupported detached Inspection task: {task}")
        if any(value["task"] == task for value in (*self._active, *self._completed)):
            raise RuntimeError(f"detached Inspection task already submitted: {task}")
        while len(self._active) >= self._max_workers:
            self._reap(block=True)

        output = Path(output_directory).resolve()
        _clean_stale_staging(output)
        handoff_path = self._handoff_directory / f"{task}.json"
        snapshot_path = self._handoff_directory / f"{task}-source.pickle.gz"
        result_path = self._task_directory / f"{task}.json"
        result_path.unlink(missing_ok=True)
        snapshot_path.unlink(missing_ok=True)
        _write_json_atomic(
            handoff_path,
            {
                "format_version": _HANDOFF_FORMAT_VERSION,
                "artifact_type": "inspection-tool-handoff",
                "tool": task,
                "artifact_directory": os.path.relpath(
                    output, self._handoff_directory
                ),
                "snapshot": {
                    "status": "PENDING",
                    "encoding": _SNAPSHOT_ENCODING,
                    "path": snapshot_path.name,
                },
                "source": _handoff_source_description(task, source),
            },
        )
        scheduled_monotonic = perf_counter()
        scheduled_at = _utc_now()
        process = self._context.Process(
            target=_worker_entry,
            args=(
                task,
                dict(source),
                output,
                handoff_path,
                snapshot_path,
                result_path,
            ),
            name=f"skyseed-inspection-{task}",
        )
        process.start()
        handle: dict[str, object] = {
            "task": task,
            "status": "BUILDING",
            "pid": process.pid,
            "scheduled_at": scheduled_at,
            "scheduled_monotonic": scheduled_monotonic,
            "output_directory": str(output),
            "handoff_path": str(handoff_path),
            "result_path": str(result_path),
            "process": process,
        }
        self._active.append(handle)
        print(
            f"Inspection {task} worker scheduled: PID {process.pid} / BUILDING",
            flush=True,
        )
        return _public_task_record(handle)

    def poll(self) -> tuple[dict[str, object], ...]:
        self._reap(block=False)
        return self.records()

    def join(self) -> tuple[dict[str, object], ...]:
        """Wait only for final artifact completion, not domain progression."""

        while self._active:
            self._reap(block=True)
        records = self.records()
        self._closed = True
        self._discard_control_directory()
        return records

    def terminate(self) -> None:
        """Stop unfinished side work after a primary domain failure."""

        for handle in tuple(self._active):
            process = handle.get("process")
            if isinstance(process, multiprocessing.process.BaseProcess):
                if process.is_alive():
                    process.terminate()
                process.join(timeout=10.0)
            output = Path(str(handle["output_directory"]))
            _clean_stale_staging(output)
            handle.pop("process", None)
            handle.update(
                {
                    "status": "FAILED",
                    "message": "cancelled after primary domain failure",
                    "exit_code": getattr(process, "exitcode", None),
                }
            )
            self._active.remove(handle)
            self._completed.append(handle)
        self._closed = True
        self._discard_control_directory()

    def records(self) -> tuple[dict[str, object], ...]:
        ordered = sorted(
            (*self._completed, *self._active),
            key=lambda value: str(value["task"]),
        )
        return tuple(_public_task_record(value) for value in ordered)

    def result(self, task: str) -> dict[str, object] | None:
        for value in self._completed:
            if value["task"] == task:
                result = value.get("tool_result")
                return dict(result) if isinstance(result, Mapping) else None
        return None

    def _reap(self, *, block: bool) -> None:
        announced_at = perf_counter()
        while True:
            finished = []
            for handle in self._active:
                process = handle["process"]
                assert isinstance(process, multiprocessing.process.BaseProcess)
                if not process.is_alive():
                    process.join()
                    finished.append(handle)
            if finished:
                for handle in finished:
                    self._finish(handle)
                return
            if not block:
                return
            if perf_counter() - announced_at >= 30.0:
                names = ", ".join(str(value["task"]) for value in self._active)
                print(f"Inspection workers still BUILDING: {names}", flush=True)
                announced_at = perf_counter()
            sleep(0.2)

    def _finish(self, handle: dict[str, object]) -> None:
        process = handle.pop("process")
        assert isinstance(process, multiprocessing.process.BaseProcess)
        result_path = Path(str(handle["result_path"]))
        payload: dict[str, object]
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("worker result must be an object")
            payload = raw
        except (OSError, ValueError, json.JSONDecodeError) as error:
            payload = {
                "status": "FAILED",
                "error_type": type(error).__name__,
                "message": str(error),
            }
        if process.exitcode != 0:
            payload["status"] = "FAILED"
            payload.setdefault("message", f"worker exited with code {process.exitcode}")
        try:
            handoff = _read_handoff(Path(str(handle["handoff_path"])))
            payload["input_evidence"] = handoff.get("snapshot")
        except (OSError, ValueError):
            payload["input_evidence"] = {"status": "UNAVAILABLE"}
        handle.update(payload)
        handle["exit_code"] = process.exitcode
        handle["status"] = str(payload.get("status", "FAILED"))
        self._active.remove(handle)
        self._completed.append(handle)
        print(
            f"Inspection {handle['task']} worker finished: "
            f"PID {handle.get('pid')} / {handle['status']}",
            flush=True,
        )

    def _discard_control_directory(self) -> None:
        if self._evidence_directory.exists():
            shutil.rmtree(self._evidence_directory)


def _worker_entry(
    task: str,
    source: dict[str, object],
    output: Path,
    handoff_path: Path,
    snapshot_path: Path,
    result_path: Path,
) -> None:
    started = perf_counter()
    started_at = _utc_now()
    pid = os.getpid()
    staging = _staging_directory(output)
    payload: dict[str, object]
    try:
        staging.mkdir(parents=True)
        if task == "observation":
            observation_path = Path(str(source["observation_path"])).resolve()
            _complete_file_handoff(handoff_path, observation_path)
            result = _build_observation(observation_path, staging)
        else:
            detached = _detach_live_source(task, source)
            snapshot_metadata = _write_snapshot(snapshot_path, detached)
            _complete_snapshot_handoff(handoff_path, snapshot_metadata)
            result = _build_from_snapshot(task, detached, staging)
        _audit_bundle(staging)
        _publish_staged(staging, output)
        ended = perf_counter()
        payload = {
            "task": task,
            "status": "COMPLETE",
            "pid": pid,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "started_monotonic": started,
            "ended_monotonic": ended,
            "wall_seconds": ended - started,
            "peak_rss_bytes": _peak_rss_bytes(),
            "tool_result": _json_safe({**result, "directory": output}),
        }
    except BaseException as error:
        if staging.exists():
            shutil.rmtree(staging)
        ended = perf_counter()
        payload = {
            "task": task,
            "status": "FAILED",
            "pid": pid,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "started_monotonic": started,
            "ended_monotonic": ended,
            "wall_seconds": ended - started,
            "peak_rss_bytes": _peak_rss_bytes(),
            "error_type": type(error).__name__,
            "message": str(error),
        }
    _write_json_atomic(result_path, payload)
    if payload["status"] != "COMPLETE":
        raise SystemExit(1)


def _detach_live_source(task: str, source: Mapping[str, object]) -> dict[str, Any]:
    if task == "planner":
        if "planner_history_path" in source:
            return {
                "planner_history_path": str(source["planner_history_path"]),
                "physical_frames_path": source.get("physical_frames_path"),
                "physical_link_inspection_path": source.get(
                    "physical_link_inspection_path"
                ),
                "model_descriptors": dict(
                    source.get("model_descriptors", {})
                ),
                "streaming_projection": True,
            }
        from skyseed_v2.simulator.planning import (
            published_planning_evidence_to_data,
        )
        from tools._history import read_sealed_physical_frames

        evidence = tuple(source["evidence"])
        return {
            "evidence": tuple(
                published_planning_evidence_to_data(value)
                for value in evidence
            ),
            "physical_frames": (
                ()
                if "physical_frames_path" not in source
                else read_sealed_physical_frames(
                    Path(str(source["physical_frames_path"]))
                )
            ),
            "physical_link_inspection_path": source.get(
                "physical_link_inspection_path"
            ),
            "model_descriptors": dict(source.get("model_descriptors", {})),
        }
    if task == "compiler":
        if "compiler_history_path" in source:
            from tools.bpf_compiler.inspection import (
                _compiler_observation_timing,
            )
            from tools.bpf_compiler.reader import (
                read_compact_bpf_compiler_evidence,
            )

            return {
                "compact_history": read_compact_bpf_compiler_evidence(
                    Path(str(source["compiler_history_path"])),
                    (
                        None
                        if "realization_manifest" not in source
                        else Path(str(source["realization_manifest"]))
                    ),
                ),
                "observation_timing": (
                    None
                    if "observation_path" not in source
                    else _compiler_observation_timing(
                        Path(str(source["observation_path"]))
                    )
                ),
            }
        if "realization_manifest" in source:
            from skyseed_v2.experiment._realization_handoff import (
                _read_persisted_realization_sequence,
            )
            from tools.bpf_compiler.reader import read_bpf_compiler_inspection
            from tools.planner.reader import read_planner_history

            snapshot = read_bpf_compiler_inspection(
                tuple(
                    value.plan
                    for value in read_planner_history(
                        Path(str(source["planner_history_path"]))
                    )
                ),
                _read_persisted_realization_sequence(
                    Path(str(source["realization_manifest"]))
                ),
            ).snapshot
            snapshot["compile_evidence"] = tuple(
                source.get("compile_evidence", ())
            )
            if "observation_path" in source:
                from tools.bpf_compiler.inspection import (
                    _compiler_observation_timing,
                )

                snapshot["observation_timing"] = (
                    _compiler_observation_timing(
                        Path(str(source["observation_path"]))
                    )
                )
            return snapshot
        from tools.bpf_compiler.reader import read_bpf_compiler_inspection

        snapshot = read_bpf_compiler_inspection(
            tuple(source["plans"]), tuple(source["realizations"])
        ).snapshot
        snapshot["compile_evidence"] = tuple(source.get("compile_evidence", ()))
        return snapshot
    raise ValueError(f"task does not accept a live snapshot: {task}")


def _build_from_snapshot(
    task: str, snapshot: dict[str, Any], directory: Path
) -> dict[str, object]:
    if task == "planner":
        if snapshot.get("streaming_projection") is True:
            from tools.planner.inspection import write_planner_history_tool

            return write_planner_history_tool(
                Path(str(snapshot["planner_history_path"])),
                directory,
                physical_frames_path=(
                    None
                    if snapshot.get("physical_frames_path") is None
                    else Path(str(snapshot["physical_frames_path"]))
                ),
                physical_link_inspection_path=(
                    None
                    if snapshot.get("physical_link_inspection_path") is None
                    else Path(
                        str(snapshot["physical_link_inspection_path"])
                    )
                ),
                model_descriptors=dict(
                    snapshot.get("model_descriptors", {})
                ),
            )
        from skyseed_v2.simulator.planning import (
            published_planning_evidence_from_data,
        )
        from tools.planner.artifact_writer import write_planner_artifacts
        from tools.planner.projection import project_planner_data
        from tools.planner.reader import read_planner_inspection
        from tools.planner.renderer import render_planner_page

        evidence = tuple(
            published_planning_evidence_from_data(value)
            for value in snapshot["evidence"]
        )
        data = project_planner_data(
            read_planner_inspection(evidence),
            physical_frames=tuple(snapshot.get("physical_frames", ())),
            physical_link_inspection_path=(
                None
                if snapshot.get("physical_link_inspection_path") is None
                else Path(str(snapshot["physical_link_inspection_path"]))
            ),
            model_descriptors=dict(snapshot.get("model_descriptors", {})),
        )
        return write_planner_artifacts(data, render_planner_page(data), directory)
    if task == "compiler":
        from tools.bpf_compiler.artifact_writer import write_bpf_compiler_artifacts
        from tools.bpf_compiler.projection import (
            project_bpf_compiler_data,
            project_compact_bpf_compiler_data,
        )
        from tools.bpf_compiler.reader import BpfCompilerInspectionRead
        from tools.bpf_compiler.renderer import render_bpf_compiler_page

        observation_timing = snapshot.pop("observation_timing", None)
        if "compact_history" in snapshot:
            data = project_compact_bpf_compiler_data(
                snapshot["compact_history"],
                observation_timing=observation_timing,
            )
        else:
            compile_evidence = tuple(snapshot.pop("compile_evidence", ()))
            data = project_bpf_compiler_data(
                BpfCompilerInspectionRead(snapshot),
                compile_evidence=compile_evidence,
                observation_timing=observation_timing,
            )
        return write_bpf_compiler_artifacts(
            data, render_bpf_compiler_page(data), directory
        )
    raise ValueError(f"unsupported detached snapshot task: {task}")


def _build_observation(path: Path, directory: Path) -> dict[str, object]:
    from tools.observation import write_observation_tool

    return write_observation_tool(path, directory)


def _write_snapshot(path: Path, value: Mapping[str, object]) -> dict[str, object]:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    digest = sha256()
    uncompressed_bytes = 0

    class _HashingWriter:
        def __init__(self, stream: gzip.GzipFile) -> None:
            self._stream = stream

        def write(self, raw: bytes) -> int:
            nonlocal uncompressed_bytes
            digest.update(raw)
            uncompressed_bytes += len(raw)
            self._stream.write(raw)
            return len(raw)

    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0
            ) as compressed:
                pickle.Pickler(
                    _HashingWriter(compressed), protocol=5
                ).dump(dict(value))
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "COMPLETE",
        "encoding": _SNAPSHOT_ENCODING,
        "path": destination.name,
        "size_bytes": destination.stat().st_size,
        "sha256": _sha256_file(destination),
        "uncompressed_bytes": uncompressed_bytes,
        "uncompressed_sha256": digest.hexdigest(),
    }


def _complete_snapshot_handoff(
    handoff_path: Path, snapshot: Mapping[str, object]
) -> None:
    value = _read_handoff(handoff_path)
    value["snapshot"] = dict(snapshot)
    _write_json_atomic(handoff_path, value)


def _complete_file_handoff(handoff_path: Path, source_path: Path) -> None:
    value = _read_handoff(handoff_path)
    relative = os.path.relpath(source_path, handoff_path.parent)
    value["snapshot"] = {
        "status": "COMPLETE",
        "encoding": "source-jsonl",
        "path": relative,
        "size_bytes": source_path.stat().st_size,
        "sha256": _sha256_file(source_path),
    }
    source = value.get("source")
    if isinstance(source, dict):
        source["path"] = relative
    _write_json_atomic(handoff_path, value)


def _handoff_source_description(
    task: str, source: Mapping[str, object]
) -> dict[str, object]:
    if task == "observation":
        return {
            "kind": "completed-execution-observation-jsonl",
            "path": os.path.relpath(
                Path(str(source["observation_path"])).resolve(),
                # Replaced with a handoff-relative path by the worker.
                Path(str(source["observation_path"])).resolve().parent,
            ),
        }
    durable = "planner_history_path" in source or "realization_manifest" in source
    return {
        "kind": (
            "durable-domain-evidence"
            if durable
            else "immutable-domain-fork-snapshot"
        ),
        "detached_before_projection": True,
        "domain_reexecution": False,
        "full_history_retained_by_parent": not durable,
    }


def _audit_bundle(directory: Path) -> None:
    checksum_path = directory / "checksums.sha256"
    if not checksum_path.is_file():
        raise ValueError("Tool bundle has no checksum inventory")
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError("Tool checksum inventory is empty")
    for line in lines:
        digest, separator, relative = line.partition("  ")
        if not separator or not relative or Path(relative).is_absolute():
            raise ValueError("Tool checksum inventory entry is invalid")
        target = (directory / relative).resolve()
        try:
            target.relative_to(directory.resolve())
        except ValueError as error:
            raise ValueError("Tool checksum target escapes its bundle") from error
        if not target.is_file() or _sha256_file(target) != digest:
            raise ValueError(f"Tool checksum audit failed: {relative}")


def _staging_directory(output: Path) -> Path:
    return output.parent / f".{output.name}.staging-{os.getpid()}"


def _clean_stale_staging(output: Path) -> None:
    parent = output.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    prefix = f".{output.name}.staging-"
    for candidate in parent.iterdir():
        if candidate.name.startswith(prefix) and candidate.is_dir():
            shutil.rmtree(candidate)


def _publish_staged(staging: Path, output: Path) -> None:
    staging = staging.resolve()
    output = output.resolve()
    if staging.parent != output.parent or not staging.name.startswith(
        f".{output.name}.staging-"
    ):
        raise ValueError("Tool staging directory is outside its publication scope")
    previous = output.parent / f".{output.name}.previous-{os.getpid()}"
    if previous.exists():
        shutil.rmtree(previous)
    if output.exists():
        os.replace(output, previous)
    try:
        os.replace(staging, output)
    except BaseException:
        if previous.exists() and not output.exists():
            os.replace(previous, output)
        raise
    if previous.exists():
        shutil.rmtree(previous)


def _read_handoff(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Inspection handoff is invalid") from error
    if not isinstance(value, dict):
        raise ValueError("Inspection handoff must be an object")
    if (
        value.get("format_version") != _HANDOFF_FORMAT_VERSION
        or value.get("artifact_type") != "inspection-tool-handoff"
        or value.get("tool") not in _SUPPORTED_TASKS
    ):
        raise ValueError("Inspection handoff format is unsupported")
    return value


def _write_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(
                _json_safe(dict(value)),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _json_safe(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Inspection task result is not JSON-safe: {type(value)!r}")


def _public_task_record(value: Mapping[str, object]) -> dict[str, object]:
    excluded = {"process", "result_path", "tool_result"}
    return {
        key: _json_safe(item)
        for key, item in value.items()
        if key not in excluded
    }


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _peak_rss_bytes() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["_DetachedInspectionExecutor"]
