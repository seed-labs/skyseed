"""Coordinate finite or retained computation and explicitly requested execution."""
from collections.abc import Mapping, Sequence
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
import shutil
from threading import Condition, Event, Thread
from time import perf_counter

from seedemu.core import Emulator
from skyseed.backend.selection import _backend, _emulator_backend, _validate_program
from skyseed.deployment import Deployer
from skyseed.deployment.deployer import _startup_parallel
from skyseed.simulator.planner.routing import validate_route_worker_limit
from skyseed.simulator import Simulator
from .output.generation import (
    _append_phase, _guard_existing_output, _write_orchestration_timing, _prepare_generated_output,
)
from .output.provenance import _capture_inputs, _describe_simulator, _write_inputs, _write_json
from skyseed.shared._console import _console
from .producer import Producer
from .output.observation import _ExecutionObserver
from .output.occupancy import claim_output
from ._control import _ControlUnavailable, _request, _serve_control, _write_control_client
from skyseed.shared._durable_evidence import _append_jsonl

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_THROUGH_STAGES = ("physical-world", "planner", "compiler")


@contextmanager
def _route_worker_override(value):
    # Scope an already-validated explicit setting to generation, then restore it.
    name = "SKYSEED_ROUTE_WORKERS"
    previous = os.environ.get(name)
    if value is not None:
        os.environ[name] = str(value)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def _resolve_delivery_ends(timeline, delivery_ends):
    """Resolve the workflow's existing finite delivery partition before any output work."""
    ends = (timeline.end_time,) if delivery_ends is None else tuple(delivery_ends)
    if (not ends or ends[-1] != timeline.end_time
            or any(end < timeline.start_time for end in ends)
            or any((end - timeline.start_time) % timeline.observation_interval for end in ends[:-1])
            or any(a >= b for a, b in zip(ends, ends[1:]))):
        raise ValueError("delivery endpoints must partition the finite Timeline in increasing cadence order")
    return ends


class ExperimentController:
    """Configure finite generation or retained computation from constructed owners.

    Layer: experiment orchestration. This public entry coordinates requested
    calculation ranges; it is not a runtime lifecycle authority.
    The caller owns the constructed Emulator and Simulator. This experiment-layer
    object owns their one incremental Producer during ``generate`` or ``serve``; compilation,
    artifacts and runtime controls use the existing backend/execution components.
    ``serve(run=True)`` uses the existing generated deployment/runtime operation.
    Optional Tools run on completed requests through ``serve(on_completed=...)``
    or afterward at the finite caller.
    Source files and ordinary YAML references are captured alongside actual Model
    inputs. ``entry_path`` anchors source identity for programmatic construction.
    """

    def __init__(
        self, emulator: Emulator, simulator: Simulator, output: Path, *,
        entry_path: Path,
        input_files: Sequence[Path] = (),
        scenario_parameters: Mapping[str, object] | None = None,
        through: str = "compiler",
        replace: bool = False,
        docker_compiler=None,
        internet_map_enabled: bool = True,
        delivery_ends: Sequence[datetime] | None = None,
        batch_lead_fraction: float = 0.75,
        batch_capacity_factor: int = 2,
        traffic_options: Mapping[str, object] | None = None,
        traffic_enabled: bool | None = None,
        traffic_telemetry_enabled: bool | None = None,
        deploy_parallel: int | None = None,
        runtime_workers: int | None = None,
        route_workers: int | None = None,
        dataplane_program=None,
        forwarding_backend=None,
        initial_readiness_command: Sequence[str] | None = None,
        preparation=None,
        preparation_inputs: Mapping[str, object] | None = None,
        interaction_endpoint: str | None = None,
        interaction_listen_host: str = "127.0.0.1",
        interaction_container_endpoint: str | None = None,
    ) -> None:
        if not isinstance(emulator, Emulator) or not isinstance(simulator, Simulator):
            raise TypeError("ExperimentController requires an Emulator and Simulator")
        if through not in _THROUGH_STAGES:
            raise ValueError(f"through must be one of {_THROUGH_STAGES}")
        if forwarding_backend is not None:
            selected = _backend(forwarding_backend)
            if hasattr(emulator, "_skyseed_forwarding_backend") and _emulator_backend(emulator) != selected:
                raise ValueError("Emulator and experiment select incompatible forwarding backends")
            emulator._skyseed_forwarding_backend = selected
        self._forwarding_backend = _emulator_backend(emulator)
        emulator._skyseed_forwarding_backend = self._forwarding_backend
        _validate_program(self._forwarding_backend, dataplane_program)
        self._interaction_options = dict(endpoint=interaction_endpoint, listen_host=interaction_listen_host,
            container_endpoint=interaction_container_endpoint)
        self._interaction_ready = Event()
        self._interaction = None
        self._emulator, self._simulator = emulator, simulator
        self._output = Path(output).expanduser().resolve()
        self._entry_path = Path(entry_path).resolve()
        self._input_files = tuple(input_files)
        from .preparation import _function_source
        import json
        if preparation is not None and initial_readiness_command is not None:
            raise ValueError("choose preparation or initial_readiness_command, not both")
        self._preparation = None if preparation is None else _function_source(preparation)
        # Detach and validate inputs before modifying generated output.
        self._preparation_inputs = json.loads(json.dumps(dict(preparation_inputs or {}), allow_nan=False))
        if self._preparation is not None:
            self._input_files += (self._preparation[0],)
        from .execution import _validate_readiness_command
        self._initial_readiness_command = _validate_readiness_command(initial_readiness_command)
        self._captured_readiness_command = None
        self._parameters = dict(scenario_parameters or {})
        self._through, self._replace = through, replace
        self._docker_compiler = docker_compiler
        self._internet_map_enabled = internet_map_enabled
        self._delivery_ends = None if delivery_ends is None else tuple(delivery_ends)
        self._lead, self._capacity = batch_lead_fraction, batch_capacity_factor
        self._traffic_options = dict(traffic_options or {})
        if traffic_enabled is None:
            traffic_enabled = self._forwarding_backend == "bpf"
        self._traffic_enabled = traffic_enabled
        if traffic_telemetry_enabled is None:
            traffic_telemetry_enabled = self._forwarding_backend == "bpf"
        if self._forwarding_backend == "linux" and traffic_telemetry_enabled:
            raise ValueError("BPF traffic telemetry is unavailable for Linux forwarding")
        self._traffic_telemetry_enabled = traffic_telemetry_enabled
        self._deployer = Deployer(self._output)
        self._observer = None
        self._recording_failure = None
        self._completed_materials = []
        self._deploy_parallel, self._runtime_workers = deploy_parallel, runtime_workers
        self._route_workers = route_workers
        self._dataplane_program = dataplane_program
        self._generated = False
        self._condition = Condition()
        self._stop = Event()
        self._runtime_managed = False
        self._shutdown_requested = False
        self._serve_finished = False
        self._generation_finished = False
        self._retaining = False
        self._execution_error = None
        self._materials_ready = Event()
        self._producer = None
        self._requested_end = simulator.get_timeline().end_time
        self._batch_duration = None
        self._computation_state = "NEW"
        self._published_count = 0
        self._published_end = None
        self._session_error = None
        self._first_materials_seconds = None
        self._completed_end = None
        self._presentation_pending = None
        self._presentation_closed = False
        self._presentation_status = "DISABLED"
        self._presentation_callback = None

    def serve(self, *, delivery_duration: timedelta = timedelta(seconds=300),
              run: bool = False, cleanup: bool = True, on_completed=None) -> Path:
        """Retain this calculation until cancellation; optionally execute generated controls.

        Initial and continued ranges are finite. Only this explicit operation
        keeps Producer alive. ``run=True`` uses the existing deployment and runtime
        execution operation, with normal teardown after runtime cancellation.
        """
        session_error = None
        try:
            if run and self._through != "compiler":
                raise ValueError("managed runtime execution requires compiler materials")
            self._validate_duration(delivery_duration)
            _console("Retained calculation session (serve): this terminal stays open after range readiness. "
                     "Use continue or cancel-computation from experiment_control.py. "
                     "run.sh has a separate runtime owner and retains the final network for continuation; "
                     "--operation generate selects finite calculation and execution materials.")
            self._runtime_managed = run
            self._presentation_callback = on_completed
            self._batch_duration = delivery_duration
            timeline = self._simulator.get_timeline()
            if self._delivery_ends is None:
                ends, boundary = [], timeline.start_time + delivery_duration
                while boundary < timeline.end_time:
                    ends.append(boundary - timeline.observation_interval)
                    boundary += delivery_duration
                self._delivery_ends = (*ends, timeline.end_time)
            from .execution import run_generated_experiment
            computation_done, presentation_done = Event(), Event()

            def compute():
                try:
                    self._generate(retain=True)
                except BaseException as error:
                    with self._condition:
                        self._session_error = error
                        self._computation_state = "FAILED"
                        if self._interaction is not None:
                            try:
                                _request(self._output, "runtime", "cancel-runtime")
                            except BaseException as cancellation_error:
                                # Computation-only serve may have no independent runtime.
                                if not isinstance(cancellation_error, _ControlUnavailable):
                                    error.add_note(f"runtime cancellation also failed: {cancellation_error}")
                        # Otherwise runtime_claim rejects this failure before Runner
                        # construction; no unavailable endpoint loses the signal.
                finally:
                    self._materials_ready.set()
                    computation_done.set()

            def present():
                try:
                    self._present_completed()
                finally:
                    presentation_done.set()

            with _serve_control(self._output, "computation", self._control_request, initialized=self._interaction_ready, observe_runtime=self._observe_runtime):
                thread = Thread(target=compute, name="skyseed-retained-computation")
                presentation = None
                if on_completed is not None:
                    presentation = Thread(target=present, name="skyseed-presentation")
                    presentation.start()
                thread.start()
                try:
                    self._materials_ready.wait()
                    if self._session_error is not None:
                        raise self._session_error
                    if run and self._published_count and not self._shutdown_requested:
                        run_generated_experiment(self._output, cleanup=cleanup)
                    computation_done.wait()
                except BaseException as error:
                    self._execution_error = error
                    raise
                finally:
                    # SIGINT can invalidate Thread.join/is_alive bookkeeping on
                    # CPython 3.12. Only the owners' finally signals prove drainage.
                    while True:
                        try:
                            self._cancel_computation()
                            computation_done.wait()
                            with self._condition:
                                self._presentation_closed = True
                                self._condition.notify_all()
                            if presentation is not None:
                                presentation_done.wait()
                            with self._condition:
                                self._serve_finished = True
                            break
                        except KeyboardInterrupt:
                            continue  # Repeated interruption retains ownership.
            if self._session_error is not None:
                raise self._session_error
            return self._output
        except BaseException as error:
            session_error = error
            if self._producer is None and not self._generation_finished:
                try:
                    self._simulator.shutdown()
                except BaseException as cleanup_error:
                    error.add_note(f"Simulator shutdown also failed: {type(cleanup_error).__name__}: {cleanup_error}")
                    for note in getattr(cleanup_error, "__notes__", ()):
                        error.add_note(note)
            raise
        finally:
            if self._serve_finished:
                failure = self._session_error if self._session_error is not None else session_error
                if failure is not None and not isinstance(failure, KeyboardInterrupt):
                    _console(f"Calculation session closed with errors: {type(failure).__name__}: {failure}")
                else:
                    _console(f"Calculation session closed: owned work and presentation drained. {self._output}")

    def _validate_duration(self, duration):
        interval = self._simulator.get_timeline().observation_interval
        if not isinstance(duration, timedelta) or duration <= timedelta(0) or duration % interval:
            raise ValueError("duration must be positive and aligned to the original observation cadence")

    @property
    def interaction(self):
        """Independent-program client; available after explicit generation starts."""
        from .interaction import InteractionClient
        return InteractionClient.from_output(self._output)

    def _submit(self, command, *, owner='computation', **arguments):
        client = self.interaction
        try:
            return client.submit(owner, command, **arguments)
        finally:
            client.close()

    def continue_calculation(self, *, additional_duration=None, end_time=None, delivery_duration=None):
        return self._submit('continue',
            seconds=None if additional_duration is None else additional_duration.total_seconds(),
            until=None if end_time is None else end_time.isoformat(),
            batch_seconds=None if delivery_duration is None else delivery_duration.total_seconds())

    def cancel_computation(self):
        return self._submit('cancel-computation')

    def revise_future(self, *, at=None, request_id=None):
        return self._submit('revise', at=at, request_id=request_id)

    def _observe_runtime(self, state):
        if self._producer is not None:
            self._producer._observe_runtime(state)

    def status(self):
        """Local diagnostics, available before startup and without store access.

        Use interaction.state('computation') for shared publications carrying
        session, version and freshness; this local snapshot has none of those.
        """
        return self._local_status()

    def _continue_calculation(self, *, additional_duration=None, end_time=None, delivery_duration=None):
        """Request a retained suffix from the computation owner."""
        if self._producer is None:
            raise RuntimeError("continuation requires the original live computation session")
        snapshot = self._producer.request_extension(additional_duration=additional_duration,
            end_time=end_time, delivery_duration=delivery_duration)
        self._event("calculation_requested", requested_end=snapshot["requested_end"])
        _console(f"Calculation continuation starting: requested through {snapshot['requested_end']}.")
        return self._local_status()

    def _cancel_computation(self) -> None:
        """Cancel computation and drain accepted work without stopping the network."""
        if not self._stop.is_set():
            _console("Calculation stop requested: draining owned calculation and presentation work before session closure.")
        self._stop.set()
        if self._producer is not None:
            self._producer.cancel_computation()

    def cancel_runtime(self):
        """Stop the independently owned Runner; retained computation remains live."""
        return self._submit("cancel-runtime", owner="runtime")

    def _local_status(self) -> dict:
        """Aggregate bounded owner snapshots; querying status never starts deployment."""
        from .execution import _error_fact
        with self._condition:
            producer = None if self._producer is None else self._producer.snapshot()
            result = {"state": self._computation_state if producer is None else producer["production_state"],
                "initial_start": self._simulator.get_timeline().start_time.isoformat(),
                "initial_end": self._simulator.get_timeline().end_time.isoformat(),
                "requested_end": self._requested_end.isoformat() if producer is None else producer["requested_end"],
                "published_batches": self._published_count, "published_end": self._published_end,
                "completed_end": self._completed_end,
                "presentation": self._presentation_status,
                "producer": producer,
                "producer_ready": self._producer is not None,
                "revision_requests": 0 if self._producer is None else len(self._producer._revisions),
                "supported_parameters": {"delivery_duration_seconds": "next-delivery", "route_workers": "next-created-resources",
                    "deploy_parallel": "next-created-resources", "runtime_workers": "next-created-resources", "batch_capacity_factor": "next-created-resources"},
                "recording_failure": self._recording_failure,
                "runtime_managed": self._runtime_managed,
                "shutdown_requested": self._shutdown_requested,
                "shutdown_complete": (self._shutdown_requested and self._serve_finished
                    and self._session_error is None and self._execution_error is None),
                "outcome": None if not (self._serve_finished or self._generation_finished and not self._retaining) else {
                    "status": "failed" if self._session_error or self._execution_error else "completed",
                    "primary_error": _error_fact(self._session_error),
                    "execution_error": _error_fact(self._execution_error),
                    "presentation": self._presentation_status},
                "execution_error": None if self._execution_error is None else str(self._execution_error),
                "error": None if self._session_error is None else str(self._session_error)}
        return result

    def _control_request(self, request):
        command = request["command"]
        if command == 'shutdown':
            self._interaction._put('coordination/shutdown', {'requested': True})
            with self._condition:
                self._shutdown_requested = True
            self._cancel_computation()
            return self._local_status()
        if command == "parameters":
            values = request['values']
            if not isinstance(values, dict):
                raise ValueError('parameter values must be an object')
            allowed = {'delivery_duration_seconds', 'route_workers', 'deploy_parallel', 'runtime_workers', 'batch_capacity_factor'}
            if set(values) - allowed:
                raise ValueError(f'unsupported computation parameters: {set(values) - allowed}')
            if self._producer is None and {'delivery_duration_seconds', 'route_workers'}.intersection(values):
                raise RuntimeError('Producer is not ready')
            if 'delivery_duration_seconds' in values:
                duration = timedelta(seconds=values['delivery_duration_seconds'])
                self._validate_duration(duration)
                if self._producer is None:
                    raise RuntimeError('Producer is not ready')
            for name, validator in [('route_workers', validate_route_worker_limit),
                                    ('deploy_parallel', _startup_parallel)]:
                if name in values:
                    validator(values[name])
            from skyseed.backend.runner.lifecycle import _runtime_worker_count
            if 'runtime_workers' in values:
                _runtime_worker_count(values['runtime_workers'])
            if 'batch_capacity_factor' in values and (type(values['batch_capacity_factor']) is not int or values['batch_capacity_factor'] < 1):
                raise ValueError('batch_capacity_factor must be a positive int')
            record = self._interaction._get('parameters/next-resources')
            existing = {} if record is None else record['value']
            self._interaction._put('parameters/next-resources', {**existing, **values})
            if 'delivery_duration_seconds' in values:
                self._producer.set_delivery_duration(duration)
            if 'route_workers' in values:
                self._producer.set_route_workers(values['route_workers'])
            return {'values': values, 'effective': {name: 'next-created-resources' if name != 'delivery_duration_seconds' else 'next-delivery' for name in values}}
        if command == 'revision-status':
            return self._producer.revision_status(request['revision_id'])
        if command == 'revision-result-stored':
            self._producer.release_revision_result(request['revision_id'])
            return {}
        if command == 'revise':
            if self._producer is None:
                raise RuntimeError('Producer is not ready')
            return self._producer.request_revision(at=request.get('at'))
        if command == "continue":
            return self._continue_calculation(
                additional_duration=None if request.get("seconds") is None else timedelta(seconds=request["seconds"]),
                end_time=None if request.get("until") is None else datetime.fromisoformat(request["until"]),
                delivery_duration=None if request.get("batch_seconds") is None else timedelta(seconds=request["batch_seconds"]))
        if command == "cancel-computation":
            self._cancel_computation()
        elif command != "status":
            raise ValueError(f"unsupported computation command: {command}")
        return self._local_status()

    def _event(self, event, **facts):
        (self._output / ".control").mkdir(exist_ok=True)
        _append_jsonl(self._output / ".control/computation-events.jsonl",
                      {"event": event, "monotonic_seconds": perf_counter(), **facts})

    def _publish_batch(self, index, manifest, metadata, snapshot):
        """Publish sealed writer facts after explicit static preparation."""
        from .execution import _write_execution_definition
        self._flush_observations()
        destination = self._output / ("results/compiler/runner-artifact" if index == 0 else f"results/compiler/runner-artifact-{index:03d}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(manifest.parent), destination)
        manifest = destination / "manifest.json"
        reference = {"index": index, "version": "original", "manifest": str(manifest.relative_to(self._output)),
            "end": snapshot["sealed_material_end"], "timeline": metadata["manifest"]["timeline"]}
        if index == 0:
            self._first_seal = {"manifest_sha256": metadata["manifest_sha256"],
                "manifest": {key: metadata["manifest"][key] for key in (
                    "backend", "format_version", "encoding", "realization_count", "sequence_sha256", "timeline")}}
            self._first_seal["manifest"]["physical_world"] = {"fingerprint": metadata["manifest"]["physical_world"]["fingerprint"]}
        if self._batch_duration is None or index == 0:
            self._completed_materials.append(reference)
        timeline = self._simulator.get_timeline()
        if index == 0 or self._batch_duration is None:
            _write_execution_definition(self._output,
            generated_output_identity=self._deployer.project_name,
            timeline_start=timeline.start_time, timeline_end=timeline.end_time,
            observation_interval=timeline.observation_interval,
            runner_artifact=self._completed_materials[0]["manifest"],
            realization_producer="skyseed-v2-producer",
            sealed_metadata=self._first_seal,
            runner_batches=tuple(self._completed_materials[1:]),
            batch_lead_fraction=self._lead, batch_capacity_factor=self._capacity,
            keep_alive=self._batch_duration is not None, traffic_enabled=self._traffic_enabled,
            traffic_telemetry_enabled=self._traffic_telemetry_enabled,
            initial_readiness_command=self._captured_readiness_command)
        _append_jsonl(self._output / "results/compiler/materials.jsonl", reference)
        self._published_count = index + 1
        self._published_end = reference["end"]
        if index == 0:
            self._first_materials_seconds = perf_counter() - self._generation_origin
        self._event("batch_published", index=index, manifest=reference["manifest"], end=reference["end"])
        self._materials_ready.set()

    def _publish_completed(self, history, physical_links, snapshot):
        """Publish one completed request; optional presentation never holds up admission."""
        self._flush_observations()
        self._completed_end = history["timeline"]["end_timestamp"]
        publication = {
            **self._generation_status, "status": "RANGE_READY",
            "session_terminated": False, "completed_end": self._completed_end,
            "requested_end": snapshot["requested_end"], "producer": snapshot,
            "history": history, "physical_links": physical_links,
            "published_batches": self._published_count,
        }
        performance = self._output / "results/experiment/generation-performance.jsonl"
        if self._through == 'compiler':
            from .materials import _MaterialCatalog
            publication['selected_materials'] = _MaterialCatalog(self._output).selected()
        publication["performance_bytes"] = performance.stat().st_size if performance.exists() else 0
        _write_json(self._output / "results/experiment/completed.json", publication)
        _write_json(self._output / "results/experiment/generation-status.json", publication)
        with self._condition:
            if self._presentation_callback is not None:
                # Keep just the newest pending range if rendering is slower than calculation.
                self._presentation_pending = publication
                self._condition.notify_all()
        self._materials_ready.set()
        _console(f"Calculation range ready through {self._completed_end}; "
                 f"{'waiting for continuation (calculation IDLE; runtime is independent)' if snapshot['retained'] else 'finite range ready'}: {self._output}\n"
                 f"Controls: {self._output / 'experiment_control.py'}; views: {self._output / 'index.html'}")

    def _present_completed(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._presentation_pending is not None or self._presentation_closed)
                if self._presentation_pending is None:
                    return
                publication = self._presentation_pending
                self._presentation_pending = None
                self._presentation_status = "BUILDING"
            error_message = None
            try:
                result = self._presentation_callback(self._output, publication)
                failed = isinstance(result, dict) and any(
                    isinstance(value, dict) and value.get("status") not in {"PASS", "COMPLETE", "COMPLETED", "WARN"}
                    for value in result.values())
                self._presentation_status = "DEGRADED" if failed else "COMPLETE"
            except Exception as error:
                self._presentation_status = "DEGRADED"
                error_message = str(error)
                print(f"Presentation failed; completed results remain available: {error}", file=sys.stderr)
            try:
                _write_json(self._output / "results/experiment/presentation-status.json", {
                    "status": self._presentation_status, "completed_end": publication["completed_end"],
                    "error": error_message})
            except Exception as error:
                print(f"Presentation status recording failed: {error}", file=sys.stderr)

    def _prepare_static(self):
        from .execution import _write_generated_run_script
        from skyseed.emulator.construction import _prepare_forwarding
        if _emulator_backend(self._emulator) != self._forwarding_backend:
            raise ValueError("experiment forwarding backend changed after assembly")
        _prepare_forwarding(self._emulator)
        self._deployer.prepare(self._emulator, compiler=self._docker_compiler,
            internet_map_enabled=self._internet_map_enabled,
            prefix="skyseed_v2_" + self._entry_path.parent.name,
            deploy_parallel=self._deploy_parallel)
        from .container_interaction import _provision_container_interaction
        _provision_container_interaction(self._output)
        _write_generated_run_script(self._output, python_executable=sys.executable,
            tool_package_parent=_PROJECT_ROOT,
            runtime_workers=self._runtime_workers)

    def _flush_observations(self):
        try:
            self._observer.write_jsonl(self._output / "results/experiment/generation-performance.jsonl", release=True)
        except Exception as error:
            self._recording_failure = str(error)
            try:
                print(f"generation performance recording degraded: {error}", file=sys.stderr)
            except Exception:
                pass

    @staticmethod
    def add_arguments(parser, *, default_operation="serve", batch_duration=timedelta(seconds=300)):
        """Add shared generation/resource flags to a scenario's ArgumentParser.

        Scene selection, duration, cadence and intentional Model settings belong
        in the entry. Pass the resulting values explicitly to this workflow.
        """
        parser.add_argument("--operation", choices=("serve", "generate", "run"), default=default_operation,
                            help="serve retains computation (default); generate exits; run explicitly deploys and executes")
        parser.add_argument("--batch-seconds", type=float, default=batch_duration.total_seconds())
        parser.add_argument("--output", type=Path)
        parser.add_argument("--through", choices=_THROUGH_STAGES, default="compiler")
        parser.add_argument("--override", action="store_true", help="replace inactive existing output")
        parser.add_argument("--no-tools", action="store_true", help="skip optional offline presentation; active Traffic is controlled separately")
        parser.add_argument("--no-traffic", action="store_true", help="disable scheduled GS/Zone traffic generation")
        parser.add_argument("--no-traffic-telemetry", action="store_true", help="disable BPF packet/path collection independently of sending")
        parser.add_argument("--no-internet-map", action="store_true")
        parser.add_argument("--deploy-parallel", type=int, help="generated Compose startup limit (default 8)")
        parser.add_argument("--runtime-workers", type=int, help="generated BPF preparation workers (existing runtime default)")
        parser.add_argument("--route-workers", type=int, help="bound offline route workers (1–8); actual use remains demand-dependent")

    def generate(self) -> Path:
        """Generate, seal and package materials; return the output directory.

        Errors retain their cause and stage, close owned workers, and leave FAILED
        status for output created by this invocation. Reusing a consumed workflow
        fails; create new owners for a new simulation.
        """
        return self._generate(retain=False)

    def _generate(self, *, retain):
        self._retaining = retain
        try:
            with claim_output(self._output, replace=True, computation=True) as allow_execution, (nullcontext() if retain else _serve_control(
                    self._output, "computation", self._control_request, initialized=self._interaction_ready, observe_runtime=self._observe_runtime)):
                primary = None
                try:
                    return self._generate_owned(retain=retain, allow_execution=allow_execution)
                except BaseException as error:
                    primary = self._session_error = error
                    self._computation_state = "FAILED"
                    raise
                finally:
                    try:
                        if self._producer is None:
                            self._simulator.shutdown()
                    except BaseException as error:
                        if primary is None:
                            self._session_error = error
                            raise
                        primary.add_note(f"Simulator shutdown also failed: {type(error).__name__}: {error}")
                        for note in getattr(error, "__notes__", ()):
                            primary.add_note(note)
                    finally:
                        # The finite control context closes after this outcome;
                        # retained owners also wait for presentation in serve().
                        self._generation_finished = True
        except BaseException as error:
            if self._producer is None and not self._generation_finished:
                try:
                    self._simulator.shutdown()
                except BaseException as cleanup_error:
                    error.add_note(f"Simulator shutdown also failed: {type(cleanup_error).__name__}: {cleanup_error}")
                    for note in getattr(cleanup_error, "__notes__", ()):
                        error.add_note(note)
            raise

    def _generate_owned(self, *, retain, allow_execution):
        if self._generated:
            raise RuntimeError("ExperimentController.generate has already been invoked")
        self._generated = True
        output, simulator = self._output, self._simulator
        origin, phases = perf_counter(), []
        self._generation_origin = origin
        self._computation_state = "COMPUTING"
        _console(f"Calculation starting: {simulator.get_timeline().start_time.isoformat()} through "
                 f"{simulator.get_timeline().end_time.isoformat()} ({'retained' if retain else 'finite'}).")
        stage, prepared = "configuration", False
        status = self._generation_status = {"format_version": 2, "artifact_type": "experiment-generation-status",
                  "through": self._through, "status": "GENERATING", "producer": None,
                  "environment": {"CONDA_DEFAULT_ENV": os.getenv("CONDA_DEFAULT_ENV"),
                      "CONDA_PREFIX": os.getenv("CONDA_PREFIX"), "python_executable": sys.executable,
                      "python_version": sys.version}}
        try:
            # Resolve known settings before touching output or entering the Producer.
            # Owners keep their validation rules; runtime environment choices remain
            # deferred unless the caller explicitly supplies a generated default.
            timeline = simulator.get_timeline()
            delivery_ends = _resolve_delivery_ends(timeline, self._delivery_ends)
            validate_route_worker_limit(self._route_workers)
            traffic = None
            if self._through == "compiler":
                from skyseed.backend.runner.lifecycle import _runtime_worker_count, _validate_batch_configuration
                if self._deploy_parallel is not None:
                    _startup_parallel(self._deploy_parallel)
                if self._runtime_workers is not None:
                    _runtime_worker_count(self._runtime_workers)
                if retain or len(delivery_ends) > 1:
                    _validate_batch_configuration(self._lead, self._capacity)
                # Select the fixed workload once from static identities, without route calculation.
                if self._traffic_enabled:
                    try:
                        from tools.generation.traffic.workload import select_workload, write_workload
                    except ImportError as error:
                        raise RuntimeError("Traffic is enabled but its Tool is unavailable; install Tools or use traffic_enabled=False") from error
                    traffic = select_workload(simulator.get_physical_world(),
                        **{"duration_seconds": (timeline.end_time-timeline.start_time).total_seconds() + timeline.observation_interval.total_seconds(), **self._traffic_options})
            stage = "input-capture"
            captured = _capture_inputs((self._entry_path, *self._input_files))
            descriptors = _describe_simulator(simulator)
            stage = "output-preparation"
            if output.exists() and not self._replace:
                raise FileExistsError(f"experiment output already exists: {output}")
            # Protect authored sources before any recursive output replacement.
            if any(path.is_relative_to(output) for path in captured):
                raise ValueError("output must not contain the authored entry or input source files")
            _guard_existing_output(output)
            _prepare_generated_output(output, replace=self._replace)
            prepared = True
            from .interaction_service import start_interaction
            self._interaction = start_interaction(output, **self._interaction_options)
            self._interaction_ready.set()
            allow_execution()
            from .preparation import _package_root
            source_root = None if self._preparation is None else _package_root(
                self._preparation[0], self._preparation[2])
            sources = _write_inputs(output, captured, self._parameters, descriptors, source_root=source_root)
            if self._preparation is not None:
                source, name, package = self._preparation
                snapshots = {row['source']: 'inputs/' + row['snapshot'] for row in sources}
                _write_json(output / 'inputs/preparation.json', {'inputs': self._preparation_inputs})
                self._captured_readiness_command = (sys.executable, '-m',
                    'skyseed.experiment.preparation', '--function-file', snapshots[str(source)],
                    '--function-name', name, '--function-package', package)
            elif self._initial_readiness_command is not None:
                snapshots = {row['source']: str(Path('inputs') / row['snapshot']) for row in sources}
                # Replace only exact captured source arguments, never substrings,
                # command text or arbitrary references inside user options.
                self._captured_readiness_command = tuple(
                    snapshots.get(argument, argument) for argument in self._initial_readiness_command)
            if traffic is not None:
                write_workload(traffic, output / "results/traffic/workload.json")
            _write_control_client(output)
            (output / "README.md").write_text(
                '''# Experiment output

Use the generation Python environment, with this output as your working directory.
Open `index.html` and `visualizations/index.html` after optional Tools finish.
Views reflect completed publications and may lag current owner observations.

## Controls and results

`python experiment_control.py --help` lists controls. `status` reads owner state,
freshness and retained outcomes. A finite generation has no live computation owner;
`continue --seconds 300` and `revise` require retained calculation.
For Compiler output, `./deploy.sh` starts Compose and `./run.sh` owns Runner/Traffic
in a separate foreground process. From another terminal:

```bash
python experiment_control.py pause > pause-receipt.json
python experiment_control.py result "$(cat pause-receipt.json)"
# After the requested hold, release it explicitly:
python experiment_control.py resume > resume-receipt.json
python experiment_control.py result "$(cat resume-receipt.json)"
```

Receipts acknowledge storage only. Repeat result lookup until terminal and inspect
the status: CLI exit zero alone does not mean the effect succeeded. `shutdown`
returns a map of owner receipts; query each separately. It requests both owners.
Wait for calculation and runtime drainage, inspect their final outcomes, then use
`./cleanup.sh` for independently deployed containers. A closed owner can report
failed cleanup. Missing/stale state or an unknown outcome is not success.

Revision recomputes complete eligible future windows. Preparation freezes a window;
Runner keeps progressing by default, and late candidates defer or reject. Inspect
revision attempts/adopted selection and subsequent Runner activation evidence.

## Host and container programs

Host programs use the installed public client; Etcd must still be running:

```python
from skyseed.experiment import InteractionClient

client = InteractionClient.from_output(".")
try:
    print(client.state("computation"))
    print(client.user_put("research/value", {"weight": 2}))
finally:
    client.close()
```

User writes do not automatically interpret faults, run Models or revise materials.
Models choose their own reads. Explicit `submit` returns a receipt; `result`/`wait`
report its effect. A live runtime also answers focused `query("materials")`,
`query("bindings")`, `query("communication", ...)` and file-backed `query("inspect")`.
Queries return result envelopes, not bare observations; inspect their status.

For Compiler output with a container endpoint, `inputs/interaction-client/README.md`
describes the standalone client mounted at `/opt/skyseed-interaction` and generated
Compose endpoint variables. Supply a Python 3.10+ image with its listed dependencies,
your program/import path and startup command. The default same-host Unix mount
needs socket access and adds no network route. External endpoints require explicit
reachability; client credentials/certificates are not exposed by this helper.

## Storage and service lifetime

`inputs/` holds captured authored sources/provenance and generated settings/client;
it is not a generated research algorithm. `results/` holds computation, fixed Traffic
inputs and completed publications. `results/compiler/materials.jsonl` and
`selection.json` locate selected file-backed versions; candidate materials/evidence
remain in `results/revisions/`. Selection is not proof of execution.
`deployment/` holds Compose/build inputs and Deployer records. `runs/` retains actual
Runner/Traffic/performance evidence per invocation; `visualizations/` derives from it.
`.control/interaction.json` discovers Etcd. Its owned database is `.control/etcd-data`;
state/request/result/user values are records there, separate from bulk material files.
`.control/` also holds locks, events, revision proposals and requested inspections.

Etcd survives generation and owner closure. After both owners close and you have
checked resource cleanup, explicitly stop it on the host:

```bash
python -m skyseed.experiment.interaction_service stop .
# Optional later online result inspection, then stop again:
python -m skyseed.experiment.interaction_service start .
python experiment_control.py status
python -m skyseed.experiment.interaction_service stop .
```

Stop preserves data/evidence and refuses unclosed owners, even when their state is
stale. A recorded failed closure permits service stop but does not repair cleanup.
Restart restores records only, never Models or running resources. External services
are never stopped. During outages retain receipts: lost replies and wait timeouts
can leave effects indeterminate, while local cleanup can still finish.
''')
            map_link = ('<p><a href="http://localhost:8080">Open live Internet Map after deployment</a> (capture is opt-in).</p>'
                        if self._internet_map_enabled and self._through == 'compiler' else '')
            preview_link = ('<p><a href="visualizations/traffic-generation/index.html">Traffic generation preview</a> · planned, before deployment</p>'
                            if self._traffic_enabled and self._through == 'compiler' else '')
            (output / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>SkySEED output</title>'
                '<h1>SkySEED experiment</h1><p><a href="visualizations/index.html">Open visualizations</a></p>'
                '<p>Views become available after a completed request. See README.md for controls and output ownership.</p>' + preview_link + map_link)
            status["configuration"] = descriptors
            status["resources"] = {
                "route_computation": {"requested_workers": self._route_workers,
                    "environment_override": os.getenv("SKYSEED_ROUTE_WORKERS"),
                    "effective_values": "results/experiment/generation-performance.jsonl; route-planning resources (per scope)"},
                "deployment": {"requested_parallel": self._deploy_parallel,
                    "effective_values": "deployment/deployment-measurements.jsonl after explicit deploy.sh"},
                "runtime_preparation": {"requested_workers": self._runtime_workers,
                    "effective_values": "runs/<invocation>/runner evidence after explicit run.sh"},
            }
            self._observer = _ExecutionObserver(metadata={"scenario_identity": self._entry_path.parent.name,
                "timeline_start": timeline.start_time.isoformat(), "timeline_end": timeline.end_time.isoformat(),
                "observation_interval_seconds": timeline.observation_interval.total_seconds(),
                "through": self._through, "progression": "physical-world-planner-plan-compiler"})
            simulator._set_execution_observer(self._observer)
            if self._through == "compiler":
                stage = "static-preparation"
                started = perf_counter()
                self._prepare_static()
                _append_phase(phases, stage, "other", perf_counter() - started, additive=True)
            stage = self._through + "-production"
            self._producer = Producer(timeline, simulator.get_physical_world(), simulator.get_physical_link_model(),
                simulator.get_planner(), evidence_path=output / "results", retain=retain,
                dataplane_program=self._dataplane_program, forwarding_backend=self._forwarding_backend)
            if self._stop.is_set():
                self._producer.cancel_computation()
            with _route_worker_override(self._route_workers):
                snapshot = self._producer.produce(output, through=self._through,
                    delivery_ends=delivery_ends,
                    delivery_duration=self._batch_duration, publish=self._publish_batch,
                    completed=self._publish_completed)
            status["producer"] = snapshot
            self._flush_observations()
            status["recording_failure"] = self._recording_failure
            elapsed = perf_counter() - origin
            cancelled_incomplete = snapshot["cancellation_requested"] and (
                not snapshot["domain_history_sealed"] or self._through == "compiler"
                and snapshot["sealed_material_end"] != snapshot["requested_end"])
            ready_seconds = self._first_materials_seconds if retain else elapsed
            status.update(status="CANCELLED" if cancelled_incomplete else "MATERIALS_READY",
                          session_terminated=True, completed_end=self._completed_end,
                          requested_end=snapshot["requested_end"], materials_ready_seconds=ready_seconds,
                          total_elapsed_seconds=elapsed, tools=None,
                          timing_scope=("retained session including idle waits; first material readiness reported separately" if retain else
                            "ExperimentController.generate through material seal and controls; caller assembly excluded"),
                          idle_seconds=snapshot["idle_seconds"])
            try:
                _write_orchestration_timing(output / "results/experiment", phases, total_wall_seconds=elapsed,
                                            run_origin_monotonic=origin)
            except Exception as error:
                status["orchestration_recording_failure"] = str(error)
            _write_json(output / "results/experiment/generation-status.json", status)
            self._computation_state = "CLOSED" if retain else "MATERIALS_READY"
            if not retain:
                _console((f"Finite calculation cancelled before range readiness after {elapsed:.3f}s: {output}."
                          if cancelled_incomplete else f"Materials ready after {elapsed:.3f}s: {output}. Finite calculation closed;")
                         + " this terminal returns after any requested Tool generation.")
            return output
        except BaseException as error:
            # Preserve the primary failure even if writing its diagnostic also fails.
            if prepared:
                status.update(status="FAILED", stage=stage, error=f"{type(error).__name__}: {error}")
                self._session_error = error
                if self._interaction is not None:
                    try:
                        self._interaction._put('coordination/computation-failure', str(error))
                    except _ControlUnavailable:
                        pass
                try:
                    _write_json(output / "results/experiment/generation-status.json", status)
                except Exception as status_error:
                    error.add_note(f"could not write generation status: {status_error}")
            error.add_note(f"ExperimentController stage {stage!r}; output {output}")
            raise


__all__ = ["ExperimentController"]
