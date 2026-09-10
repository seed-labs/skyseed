"""Generate two sealed deliveries and complete every generation Tool."""
from datetime import timedelta
import argparse
from pathlib import Path
import sys
from time import perf_counter
from yaml import safe_load

EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples.starlink_multi_shell_satellite_internet.emu import write_inputs, build_emulator
from examples.starlink_multi_shell_satellite_internet.simu import create_simulator, starlink_gen1_model_descriptors
from examples.starlink_gen1_satellite_internet.main import (
    _guard_existing_output, _prepare_generated_output, _append_phase,
    _run_compiler_stage, _write_runtime_package, _write_optional_observations,
    _write_generation_status, _run_static_tools, _run_dynamic_tools,
    _write_tool_records, _write_orchestration_timing,
)
from tools._html import write_aggregate_index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=EXAMPLE_ROOT / "output")
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    origin = perf_counter()
    phases = []
    settings = safe_load((EXAMPLE_ROOT / "settings.yaml").read_text())
    if settings["delivered_coverage_seconds"] != 300:
        raise ValueError("this qualification example uses two 300-second deliveries")
    _guard_existing_output(output)
    _prepare_generated_output(output, replace=True)
    scenario = write_inputs(output, settings)
    emulator = build_emulator(scenario)
    simulator = create_simulator(emulator, zones=output / "inputs/zones.yaml", settings=settings)
    descriptors = starlink_gen1_model_descriptors()
    descriptors["delivered_coverage"] = {**settings,
        "seam_convention": "first [0,300), second [300,600]; one B0 and 120 Main boundaries"}
    _append_phase(phases, "example-and-planner-assembly", "other", perf_counter() - origin, additive=True)
    inspection = output / "inspection"
    timeline = simulator.get_timeline()
    simulator._enable_execution_observation(inspection / "observation/execution-observations.jsonl", metadata={
        "scenario_identity": "starlink_multi_shell_satellite_internet",
        "timeline_start": timeline.start_time.isoformat(), "timeline_end": timeline.end_time.isoformat(),
        "observation_interval_seconds": 5, "through": "compiler",
        "progression": "physical-world-planner-plan-compiler"})
    # Prediction covers the whole continuous horizon and policy tails. The
    # delivery seam flushes a publication prefix without ending Planner memory.
    manifest, snapshot, frame, observation = _run_compiler_stage(simulator, output,
        phase_records=phases, delivery_ends=(timeline.start_time + timedelta(seconds=295), timeline.end_time))
    batches = tuple(Path(value["manifest"]) for value in snapshot["delivered_batches"])
    _write_runtime_package(output, emulator=emulator, simulator=simulator,
        staged_manifest=manifest, staged_batch_manifests=batches[1:], phase_records=phases,
        additional_example_roots=(EXAMPLE_ROOT,),
        batch_lead_fraction=settings["batch_lead_fraction"], batch_capacity_factor=settings["batch_capacity_factor"])
    results = {}
    failure = _write_optional_observations(simulator)
    if failure is not None:
        results["observation-evidence"] = {"status": "FAILED", "message": failure}
    materials = perf_counter() - origin
    _write_generation_status(output, through="compiler", status="MATERIALS_READY",
        materials_seconds=materials, total_seconds=materials, producer_snapshot=snapshot, model_descriptors=descriptors)
    print(f"Materials ready after {materials:.3f}s: {output}", flush=True)
    results.update(_run_static_tools(simulator, inspection, physical_frame=frame,
        physical_link_observation=observation,
        physical_link_inspection_path=output / "inspection-evidence/physical-link-observations.jsonl.gz",
        model_descriptors=descriptors, phase_records=phases))
    dynamic, records = _run_dynamic_tools(output, through="compiler", model_descriptors=descriptors, phase_records=phases)
    results.update(dynamic)
    _write_tool_records(inspection, records)
    write_aggregate_index(inspection, results, dict(results.get("physical-world", {}).get("counts", {})))
    _write_orchestration_timing(inspection, phases, total_wall_seconds=perf_counter() - origin, run_origin_monotonic=origin)
    complete = all(result.get("status") in {"PASS", "COMPLETE"} for result in results.values())
    _write_generation_status(output, through="compiler", status="TOOLS_COMPLETE" if complete else "TOOLS_FAILED",
        materials_seconds=materials, total_seconds=perf_counter() - origin,
        producer_snapshot=snapshot, tool_results=results, model_descriptors=descriptors)
    print(f"Tool portal: {inspection / 'index.html'}", flush=True)
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
