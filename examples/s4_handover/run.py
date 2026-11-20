"""Run saved S4 plans repeatedly; Ctrl+C stops and releases owned resources."""
from datetime import datetime
import json
from pathlib import Path

from skyseed import Scene, Deployment, Runner, load_plans
from experiment.console import RunConsole
from experiment.repetition import RepeatedPlans
from experiment.ut import TerminalAttachment


# Manual testing keeps nodes and application sockets alive across 600 s cycles.
REPEAT = True                     # False: execute the saved interval once.


if __name__ == "__main__":
    output = Path(__file__).parent / "output"
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
        with deployment:
            console.phase("Containers ready; preparing Runner and BPF tables")
            with Runner(stream, deployment, output=run_output) as runner:
                console.phase("Runner ready; preparing UT attachment")
                with TerminalAttachment(scene, deployment, plans, run_output,
                                        ground_stations=build["experiment"]["ground_stations"],
                                        repetition=repetition if REPEAT else None):
                    console.watch(runner)
                    runner.run()
                    console.finish()
                    console.phase("Releasing runtime resources")
    finally:
        console.close()

    print(f"Runtime records: {run_output}")
