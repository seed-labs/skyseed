"""Coordinate finite or retained computation and explicitly requested execution."""
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
import shutil
from threading import Condition, Event, Thread
from time import perf_counter

from seedemu.core import Emulator
from skyseed_v2.deployment import Deployer
from skyseed_v2.deployment.deployer import _startup_parallel
from skyseed_v2.simulator.planner.routing import validate_route_worker_limit
from skyseed_v2.simulator import Simulator
from .output.generation import (
    _append_phase, _guard_existing_output, _write_orchestration_timing, _prepare_generated_output,
)
from .output.provenance import _capture_inputs, _describe_simulator, _write_inputs, _write_json
from .producer import Producer
from .output.observation import _ExecutionObserver
from .output.occupancy import claim_output
from ._control import _ControlUnavailable, _request, _serve_control, _write_control_client
from skyseed_v2.shared._durable_evidence import _append_jsonl

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
        if value is not None:
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
        traffic_enabled: bool = True,
        deploy_parallel: int | None = None,
        runtime_workers: int | None = None,
        route_workers: int | None = None,
    ) -> None:
        if not isinstance(emulator, Emulator) or not isinstance(simulator, Simulator):
            raise TypeError("ExperimentController requires an Emulator and Simulator")
        if through not in _THROUGH_STAGES:
            raise ValueError(f"through must be one of {_THROUGH_STAGES}")
        self._emulator, self._simulator = emulator, simulator
        self._output = Path(output).expanduser().resolve()
        self._entry_path = Path(entry_path).resolve()
        self._input_files = tuple(input_files)
        self._parameters = dict(scenario_parameters or {})
        self._through, self._replace = through, replace
        self._docker_compiler = docker_compiler
        self._internet_map_enabled = internet_map_enabled
        self._delivery_ends = None if delivery_ends is None else tuple(delivery_ends)
        self._lead, self._capacity = batch_lead_fraction, batch_capacity_factor
        self._traffic_options = dict(traffic_options or {})
        self._traffic_enabled = traffic_enabled
        self._deployer = Deployer(self._output)
        self._observer = None
        self._recording_failure = None
        self._completed_materials = []
        self._deploy_parallel, self._runtime_workers = deploy_parallel, runtime_workers
        self._route_workers = route_workers
        self._generated = False
        self._condition = Condition()
        self._stop = Event()
        self._runtime_managed = False
        self._runtime_owned = False
        self._shutdown_requested = False
        self._serve_finished = False
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
        try:
            if run and self._through != "compiler":
                raise ValueError("managed runtime execution requires compiler materials")
            self._validate_duration(delivery_duration)
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
                        if self._runtime_owned or not self._runtime_managed:
                            try:
                                _request(self._output, "runtime", "cancel-runtime")
                            except BaseException as cancellation_error:
                                # Computation-only serve may have no independent runtime.
                                if self._runtime_owned or not isinstance(cancellation_error, _ControlUnavailable):
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

            with _serve_control(self._output, "computation", self._control_request):
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
                            self.cancel_computation()
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
            if self._producer is None:
                try:
                    self._simulator.shutdown()
                except BaseException as cleanup_error:
                    error.add_note(f"Simulator shutdown also failed: {type(cleanup_error).__name__}: {cleanup_error}")
                    for note in getattr(cleanup_error, "__notes__", ()):
                        error.add_note(note)
            raise

    def _validate_duration(self, duration):
        interval = self._simulator.get_timeline().observation_interval
        if not isinstance(duration, timedelta) or duration <= timedelta(0) or duration % interval:
            raise ValueError("duration must be positive and aligned to the original observation cadence")

    def continue_calculation(self, *, additional_duration=None, end_time=None, delivery_duration=None):
        """Request a retained suffix from the computation owner."""
        if self._producer is None:
            raise RuntimeError("continuation requires the original live computation session")
        snapshot = self._producer.request_extension(additional_duration=additional_duration,
            end_time=end_time, delivery_duration=delivery_duration)
        self._event("calculation_requested", requested_end=snapshot["requested_end"])
        return self.status()

    def cancel_computation(self) -> None:
        """Cancel computation and drain accepted work without stopping the network."""
        self._stop.set()
        if self._producer is not None:
            self._producer.cancel_computation()

    def cancel_runtime(self):
        """Stop the independently owned Runner; retained computation remains live."""
        return _request(self._output, "runtime", "cancel-runtime")

    def status(self) -> dict:
        """Aggregate bounded owner snapshots; querying status never starts deployment."""
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
                "recording_failure": self._recording_failure,
                "runtime_managed": self._runtime_managed,
                "shutdown_requested": self._shutdown_requested,
                "shutdown_complete": (self._shutdown_requested and self._serve_finished
                    and self._session_error is None and self._execution_error is None),
                "execution_error": None if self._execution_error is None else str(self._execution_error),
                "error": None if self._session_error is None else str(self._session_error)}
        try:
            result["runtime"] = {"available": True, **_request(self._output, "runtime", "status")}
        except _ControlUnavailable:
            result["runtime"] = {"available": False}
        except RuntimeError as error:
            result["runtime"] = {"available": True, "error": str(error)}
        return result

    def _control_request(self, request):
        command = request["command"]
        if command in {"shutdown", "runtime_claim", "runtime_release"}:
            # The runtime endpoint serves before claiming and until releasing.
            # Serialize these boundaries with shutdown, including cancel delivery,
            # so neither side can relinquish an unhandled request.
            with self._condition:
                if command == "shutdown":
                    self._shutdown_requested = True
                    self.cancel_computation()
                    if self._runtime_owned:
                        _request(self._output, "runtime", "cancel-runtime")
                elif command == "runtime_claim":
                    if self._session_error is not None:
                        raise RuntimeError(f"retained computation failed: {self._session_error}")
                    if self._runtime_managed and not self._shutdown_requested:
                        self._runtime_owned = True
                else:
                    self._runtime_owned = False
                return self.status()
        if command == "continue":
            return self.continue_calculation(
                additional_duration=None if request.get("seconds") is None else timedelta(seconds=request["seconds"]),
                end_time=None if request.get("until") is None else datetime.fromisoformat(request["until"]),
                delivery_duration=None if request.get("batch_seconds") is None else timedelta(seconds=request["batch_seconds"]))
        if command == "cancel-computation":
            self.cancel_computation()
        elif command != "status":
            raise ValueError(f"unsupported computation command: {command}")
        return self.status()

    def _event(self, event, **facts):
        (self._output / ".control").mkdir(exist_ok=True)
        _append_jsonl(self._output / ".control/computation-events.jsonl",
                      {"event": event, "monotonic_seconds": perf_counter(), **facts})

    def _publish_batch(self, index, manifest, metadata, probe_path, snapshot):
        """Publish sealed writer facts after explicit static preparation."""
        from .execution import _write_execution_definition
        self._flush_observations()
        destination = self._output / ("results/compiler/runner-artifact" if index == 0 else f"results/compiler/runner-artifact-{index:03d}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(manifest.parent), destination)
        manifest = destination / "manifest.json"
        reference = {"index": index, "manifest": str(manifest.relative_to(self._output)),
            "end": snapshot["sealed_material_end"], "timeline": metadata["manifest"]["timeline"]}
        if probe_path is not None:
            reference["traffic"] = str(probe_path.relative_to(self._output))
        if index == 0:
            self._first_seal = {"manifest_sha256": metadata["manifest_sha256"],
                "manifest": {key: metadata["manifest"][key] for key in (
                    "format_version", "encoding", "realization_count", "sequence_sha256", "timeline")}}
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
            keep_alive=self._batch_duration is not None, traffic_enabled=self._traffic_enabled)
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
        publication["performance_bytes"] = performance.stat().st_size if performance.exists() else 0
        _write_json(self._output / "results/experiment/completed.json", publication)
        _write_json(self._output / "results/experiment/generation-status.json", publication)
        with self._condition:
            if self._presentation_callback is not None:
                # Keep just the newest pending range if rendering is slower than calculation.
                self._presentation_pending = publication
                self._condition.notify_all()
        self._materials_ready.set()
        print(f"Completed through {self._completed_end}; "
              f"{'waiting for continuation' if snapshot['retained'] else 'finite range ready'}: {self._output}\n"
              f"Controls: {self._output / 'experiment_control.py'}; views: {self._output / 'index.html'}", flush=True)

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
        self._deployer.prepare(self._emulator, compiler=self._docker_compiler,
            internet_map_enabled=self._internet_map_enabled,
            prefix="skyseed_v2_" + self._entry_path.parent.name,
            deploy_parallel=self._deploy_parallel)
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
        parser.add_argument("--no-traffic", action="store_true", help="disable active Traffic probes and their collection")
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
        try:
            with claim_output(self._output, replace=True, computation=True) as allow_execution:
                return self._generate_owned(retain=retain, allow_execution=allow_execution)
        except BaseException as error:
            if self._producer is None:
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
                from skyseed_v2.backend.bpf.runner.lifecycle import _runtime_worker_count, _validate_batch_configuration
                if self._deploy_parallel is not None:
                    _startup_parallel(self._deploy_parallel)
                if self._runtime_workers is not None:
                    _runtime_worker_count(self._runtime_workers)
                if retain or len(delivery_ends) > 1:
                    _validate_batch_configuration(self._lead, self._capacity)
                # Construct the actual consumer once. Its constructor validates
                # probe options and only captures static inputs; append comes later.
                if self._traffic_enabled:
                    try:
                        from tools.traffic.active.probe_plan import TrafficProbePlanBuilder
                    except ImportError as error:
                        raise RuntimeError("Traffic is enabled but its Tool is unavailable; install Tools or use traffic_enabled=False") from error
                    traffic = TrafficProbePlanBuilder(
                        physical_world=simulator.get_physical_world(), **self._traffic_options)
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
            allow_execution()
            _write_inputs(output, captured, self._parameters, descriptors)
            _write_control_client(output)
            (output / "README.md").write_text(
                "# Experiment output\n\n"
                "Open [visualizations](visualizations/index.html). Completed requests refresh these optional views.\n\n"
                "`inputs/`: captured sources and effective settings. `results/`: PhysicalWorld, Planner, Compiler, Traffic inputs and experiment facts.\n"
                "`deployment/`: SEED build/Compose and Deployer records. `runs/`: Runner, Traffic and performance by invocation.\n"
                "`visualizations/`: browser pages and relative assets. `.control/`: internal coordination.\n\n"
                "For compiler output: `./deploy.sh`, `./run.sh`, then `./cleanup.sh`.\n"
                "`python experiment_control.py status` reports live owners; `continue --seconds 300` extends the original computation.\n")
            (output / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>SkySEED output</title>'
                '<h1>SkySEED experiment</h1><p><a href="visualizations/index.html">Open visualizations</a></p>'
                '<p>Views become available after a completed request. See README.md for controls and output ownership.</p>')
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
                simulator.get_planner(), evidence_path=output / "results", retain=retain)
            if self._stop.is_set():
                self._producer.cancel_computation()
            with _route_worker_override(self._route_workers):
                snapshot = self._producer.produce(output, through=self._through,
                    delivery_ends=delivery_ends, traffic=traffic,
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
            print(f"{'Retained session closed' if retain else 'Materials ready'} after {elapsed:.3f}s: {output}", flush=True)
            return output
        except BaseException as error:
            # Preserve the primary failure even if writing its diagnostic also fails.
            if prepared:
                status.update(status="FAILED", stage=stage, error=f"{type(error).__name__}: {error}")
                try:
                    _write_json(output / "results/experiment/generation-status.json", status)
                except Exception as status_error:
                    error.add_note(f"could not write generation status: {status_error}")
            error.add_note(f"ExperimentController stage {stage!r}; output {output}")
            raise


__all__ = ["ExperimentController"]
