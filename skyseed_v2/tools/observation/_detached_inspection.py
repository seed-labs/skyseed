"""Run heavy repository-local Inspection Tools outside domain processes.

This module is intentionally private and small.  It does not own domain
progression: callers submit already-frozen immutable values, and a forked
process reads persisted evidence or detaches live values before projecting,
rendering and publishing one complete Tool bundle.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import multiprocessing
import os
from pathlib import Path
import resource
import shutil
import tempfile
from uuid import uuid4
from time import perf_counter, sleep
from typing import Any


_SUPPORTED_TASKS = frozenset({"planner", "compiler", "performance"})


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
        self._evidence_directory.mkdir(parents=True, exist_ok=True)
        self._task_directory = Path(tempfile.mkdtemp(prefix="invocation-", dir=self._evidence_directory))
        self._cancelled = self._context.Event()
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

        if self._closed:
            raise RuntimeError("detached Inspection executor is already closed")
        output = Path(output_directory).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        claim = output.parent / f".{output.name}.publishing"
        try:
            claim.mkdir()
        except FileExistsError as error:
            raise RuntimeError(f"Tool output already has an active publisher: {output}") from error
        staging = output.parent / f".{output.name}.staging-{uuid4().hex}"
        result_path = self._task_directory / f"{task}.json"
        scheduled_monotonic = perf_counter()
        scheduled_at = _utc_now()
        process = self._context.Process(
            target=_worker_entry,
            args=(
                task,
                dict(source),
                output,
                staging,
                result_path,
                self._cancelled,
            ),
            name=f"skyseed-inspection-{task}",
        )
        try:
            process.start()
        except BaseException:
            claim.rmdir()
            raise
        handle: dict[str, object] = {
            "task": task,
            "status": "BUILDING",
            "pid": process.pid,
            "scheduled_at": scheduled_at,
            "scheduled_monotonic": scheduled_monotonic,
            "output_directory": str(output),
            "claim": str(claim),
            "staging": str(staging),
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
        """Close admission and drain accepted workers before removing owned staging."""
        self._closed = True
        self._cancelled.set()
        while self._active:
            try:
                self._reap(block=True)
            except KeyboardInterrupt:
                # Repeated cancellation cannot abandon accepted work.
                continue
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
        handle.update(payload)
        handle["exit_code"] = process.exitcode
        handle["status"] = str(payload.get("status", "FAILED"))
        staging = Path(str(handle["staging"]))
        cleanup_errors = []
        try:
            if staging.exists():
                shutil.rmtree(staging)
        except OSError as error:
            cleanup_errors.append(f"staging cleanup: {error}")
        try:
            Path(str(handle["claim"])).rmdir()
        except OSError as error:
            cleanup_errors.append(f"publication claim cleanup: {error}")
        process.close()
        if cleanup_errors:
            handle["status"] = "FAILED"
            handle["message"] = "; ".join(filter(None, (str(handle.get("message", "")), *cleanup_errors)))
        # This worker has actually exited. A local cleanup failure must not leave
        # a half-reaped handle that prevents draining the other accepted workers.
        self._active.remove(handle)
        self._completed.append(handle)
        print(
            f"Inspection {handle['task']} worker finished: "
            f"PID {handle.get('pid')} / {handle['status']}",
            flush=True,
        )

    def _discard_control_directory(self) -> None:
        if self._task_directory.exists():
            shutil.rmtree(self._task_directory)
        try:
            self._evidence_directory.rmdir()
        except OSError:
            pass  # Other invocations may still own control directories.


def _worker_entry(
    task: str,
    source: dict[str, object],
    output: Path,
    staging: Path,
    result_path: Path,
    cancelled,
) -> None:
    started = perf_counter()
    started_at = _utc_now()
    pid = os.getpid()
    payload: dict[str, object]
    try:
        staging.mkdir(parents=True)
        if task == "performance":
            observation_path = Path(str(source["observation_path"])).resolve()
            result = _build_observation(observation_path, staging)
        else:
            detached = _detach_live_source(task, source)
            result = _build_from_snapshot(task, detached, staging)
        if cancelled.is_set():
            shutil.rmtree(staging)
        else:
            _publish_staged(staging, output)
        ended = perf_counter()
        payload = {
            "task": task,
            "status": "CANCELLED" if cancelled.is_set() else "COMPLETE",
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
    if payload["status"] == "FAILED":
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
        from skyseed_v2.simulator.planner import (
            published_planning_evidence_to_data,
        )
        from tools.observation._history import read_sealed_physical_frames

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
            from tools.observation.bpf_compiler.inspection import (
                _compiler_observation_timing,
            )
            from tools.observation.bpf_compiler.reader import (
                read_compact_bpf_compiler_evidence,
            )

            return {
                **_execution_material_evidence(source.get("execution_definition_path")),
                "compact_history": read_compact_bpf_compiler_evidence(
                    Path(str(source["compiler_history_path"])),
                ),
                "observation_timing": (
                    None
                    if "observation_path" not in source
                    else _compiler_observation_timing(
                        Path(str(source["observation_path"]))
                    )
                ),
            }
        from tools.observation.bpf_compiler.reader import read_bpf_compiler_inspection

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
            from tools.observation.planner.inspection import write_planner_history_tool

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
        from skyseed_v2.simulator.planner import (
            published_planning_evidence_from_data,
        )
        from tools.observation.planner.artifact_writer import write_planner_artifacts
        from tools.observation.planner.projection import project_planner_data
        from tools.observation.planner.reader import read_planner_inspection
        from tools.observation.planner.renderer import render_planner_page

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
        from tools.observation.bpf_compiler.artifact_writer import write_bpf_compiler_artifacts
        from tools.observation.bpf_compiler.projection import (
            project_bpf_compiler_data,
            project_compact_bpf_compiler_data,
        )
        from tools.observation.bpf_compiler.reader import BpfCompilerInspectionRead
        from tools.observation.bpf_compiler.renderer import render_bpf_compiler_page

        observation_timing = snapshot.pop("observation_timing", None)
        delivery_batches = snapshot.pop("delivery_batches", ())
        if "compact_history" in snapshot:
            data = project_compact_bpf_compiler_data(
                snapshot["compact_history"],
                observation_timing=observation_timing,
                forwarding_backend=snapshot.get("forwarding_backend"),
            )
        else:
            compile_evidence = tuple(snapshot.pop("compile_evidence", ()))
            data = project_bpf_compiler_data(
                BpfCompilerInspectionRead(snapshot),
                compile_evidence=compile_evidence,
                observation_timing=observation_timing,
            )
        data["delivery_batches"] = delivery_batches
        return write_bpf_compiler_artifacts(
            data, render_bpf_compiler_page(data), directory
        )
    raise ValueError(f"unsupported detached snapshot task: {task}")


def _execution_material_evidence(definition_path) -> dict:
    """Read immutable packaging metadata; never open runtime binary contents."""
    if definition_path is None:
        return {"forwarding_backend": None, "delivery_batches": ()}
    path = Path(definition_path)
    definition = json.loads(path.read_text())
    output = path.parents[2]
    manifests = [definition["runner_artifact"]["manifest"]]
    if definition["runner"].get("keep_alive"):
        with (output / "results/compiler/materials.jsonl").open() as stream:
            from itertools import islice
            from skyseed_v2.experiment.output.history import _completed_range
            publication = _completed_range.get()
            count = None if publication is None else publication[1]["published_batches"]
            manifests = [json.loads(line)["manifest"] for line in islice(stream, count)]
    else:
        manifests.extend(value["manifest"] for value in definition.get("runner_batches", ()))
    return {"forwarding_backend": definition.get("backend"), "delivery_batches": tuple({"index": index, "manifest": relative,
                  "timeline": (value := json.loads((output / relative).read_text()))["timeline"],
                  "delivery": value.get("delivery"), "binary": value["binary"]}
                 for index, relative in enumerate(manifests))}


def _build_observation(path: Path, directory: Path) -> dict[str, object]:
    from tools.observation.performance import write_performance_tool

    return write_performance_tool(path, directory)


def _publish_staged(staging: Path, output: Path) -> None:
    staging = staging.resolve()
    output = output.resolve()
    if staging.parent != output.parent or not staging.name.startswith(
        f".{output.name}.staging-"
    ):
        raise ValueError("Tool staging directory is outside its publication scope")
    previous = staging.with_name(staging.name + ".previous")
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
    excluded = {"process", "result_path", "tool_result", "claim", "staging"}
    return {
        key: _json_safe(item)
        for key, item in value.items()
        if key not in excluded
    }


def _peak_rss_bytes() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["_DetachedInspectionExecutor"]
