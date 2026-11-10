"""Export real Physical queries to a standalone interactive inspection page."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

from skyseed import Scene
from skyseed.pc.physical import PhysicalLayer, GeometricCandidateModel
from tools.observation._presentation.inspection_page import write_page
from tools.observation._presentation.scene_display import scene_inputs


def inspection_data(physical, timestamps):
    frames = []
    for timestamp in timestamps:
        snapshot = physical.snapshot_at(timestamp)
        reports = physical.candidates(snapshot)
        frames.append({
            "timestamp": snapshot.timestamp.isoformat(),
            "states": {identity: {"position_m": state.position_m,
                                    "velocity_m_s": state.velocity_m_s, "data": dict(state.data)}
                       for identity, state in snapshot.states.items()},
            "candidates": {identity: [{"peer_id": candidate.peer_id,
                                       "distance_m": candidate.distance_m,
                                       "propagation_delay_s": candidate.propagation_delay_s,
                                       "elevation_deg": candidate.elevation_deg,
                                       "data": dict(candidate.data)} for candidate in values]
                           for identity, values in reports.items()},
        })
    return {**scene_inputs(physical.scene), "format": "skyseed.physical-inspection/v1",
            "models": {"position": type(physical.position_model).__name__,
                       "candidate": type(physical.candidate_model).__name__},
            "frames": frames}


def write_inspection(data, output):
    return write_page(data, output, template=Path(__file__).with_name('viewer.html'),
                      data_name='physical')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, help="saved Scene; omit for the small inspection scene")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="2026-01-01T00:00:00+00:00")
    parser.add_argument("--steps", type=int, default=13)
    parser.add_argument("--step-seconds", type=float, default=60)
    parser.add_argument("--position-model", choices=("circular", "sgp4"), default="circular")
    parser.add_argument("--max-isl-distance-m", type=float, default=6_000_000)
    parser.add_argument("--min-elevation-deg", type=float, default=5)
    parser.add_argument("--max-ground-distance-m", type=float)
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start)
    if start.tzinfo is None:
        parser.error("--start must include a UTC offset")
    if args.steps < 1 or args.step_seconds <= 0:
        parser.error("--steps and --step-seconds must be positive")
    if args.scene:
        scene = Scene.load(args.scene)
    else:
        from tools.observation.scene.inspection import demo_scene
        scene = demo_scene(start)
    physical = PhysicalLayer(scene)
    if args.position_model == "sgp4":
        from skyseed.pc.physical import SGP4PositionModel
        physical.set_position_model(SGP4PositionModel)
    physical.set_candidate_model(GeometricCandidateModel, model_options={
        "max_isl_distance_m":args.max_isl_distance_m,
        "min_ground_elevation_deg":args.min_elevation_deg,
        "max_ground_distance_m":args.max_ground_distance_m})
    begun = perf_counter()
    data = inspection_data(physical, [start+timedelta(seconds=i*args.step_seconds) for i in range(args.steps)])
    calculated = perf_counter()
    path = write_inspection(data, args.output)
    print(f"{path.resolve()}\n{len(scene.nodes)} nodes, {len(data['frames'])} frames; "
          f"queries {calculated-begun:.3f}s; write {perf_counter()-calculated:.3f}s")


if __name__ == "__main__":
    main()
