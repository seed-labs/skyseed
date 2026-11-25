"""Run saved S4 plans repeatedly; Ctrl+C stops and releases owned resources."""
from contextlib import nullcontext
from datetime import datetime
from functools import partial
import json
from pathlib import Path

from skyseed import Scene, Deployment, Runner, load_plans
from experiment.console import RunConsole
from experiment.repetition import RepeatedPlans
from experiment.ut import TerminalAttachment
from tools.internet_map import InternetMap, ensure_image
from experiment.map_store import map_store
from experiment.ut.map import display_terminal


# Manual testing keeps nodes and application sockets alive across 600 s cycles.
SHOW_MAP = True
REPEAT = True                     # False: execute the saved interval once.


def live_map(scene, deployment, repetition, run_output, store, build):
    if not SHOW_MAP:
        return nullcontext()
    positions = run_output.parents[1] / "observations/map-positions.json"
    return InternetMap(
        scene, deployment, repetition.iterate(cycles=2) if REPEAT else repetition.plans, run_output,
        store=store, period=repetition.seconds if REPEAT else None, zones=build["routing"].get("associations"),
        extra=partial(display_terminal, scene, run_output), positions=positions if positions.is_file() else None,
    )


if __name__ == "__main__":
    output = Path(__file__).parent / "output"
    if SHOW_MAP:
        ensure_image()
    scene = Scene.load(output / "scene/scene.scene")
    plans = output / "plans/physical/bpf.plan"
    run_output = output / "runs" / datetime.now().strftime("run-%Y%m%d-%H%M%S")
    build = json.loads((output / "build.json").read_text())
    deployment = Deployment(output / "seed/deployment", project="skyseed-s4",
                            compose_file="docker-compose.runtime.yml")
    console = RunConsole(output, run_output, build)

    try:
        repetition = RepeatedPlans(load_plans(plans))
        stream = repetition.iterate() if REPEAT else iter(repetition.plans)
        console.phase(f"Deploying {build['satellites']} satellites and {build['ground_stations']} GS")
        with (map_store(run_output.name) if SHOW_MAP else nullcontext()) as store, deployment:
            console.phase("Containers ready; preparing Runner and BPF tables")
            with Runner(stream, deployment, output=run_output, store=store) as runner:
                console.phase("Runner ready; preparing UT attachment")
                with TerminalAttachment(scene, deployment, plans, run_output,
                                        ground_stations=build["experiment"]["ground_stations"],
                                        repetition=repetition if REPEAT else None):
                    with live_map(scene, deployment, repetition, run_output, store, build):
                        console.watch(runner)
                        runner.run()
                        console.finish()
                        console.phase("Releasing runtime resources")
    finally:
        console.close()

    print(f"Runtime records: {run_output}")
