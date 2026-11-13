"""Run saved S4 plans repeatedly; Ctrl+C stops and releases owned resources."""
from datetime import datetime
import json
from pathlib import Path

from skyseed import Scene, Deployment, Runner, load_plans
from .experiment.console import RunConsole
from .experiment.repetition import RepeatedPlans
from .experiment.ut import TerminalExperiment
from .experiment.record_store import RecordStore, archive_measurements, freeze_inputs
from .experiment.observation.results import write_results
from .experiment.observation.replay import write_replay
from .experiment.observation.index import write_index


# Manual testing keeps nodes and application sockets alive across 600 s cycles.
REPEAT = True                     # False: execute the saved interval once.
CAPTURE = False                   # True: additionally save endpoint PCAP files.


if __name__ == "__main__":
    output = Path(__file__).parent / "output"
    scene = Scene.load(output / "scene/scene.scene")
    plans = output / "plans/physical/bpf.plan"
    run_output = output / "runs" / datetime.now().strftime("run-%Y%m%d-%H%M%S")
    build = json.loads((output / "build.json").read_text())
    build["repeat"] = REPEAT
    duration = build["simulated_seconds"]
    deployment = Deployment(output / "seed/deployment", project="skyseed-s4",
                            compose_file="docker-compose.runtime.yml")
    console = RunConsole(output, run_output, build)

    try:
        console.phase("Starting record store and freezing inputs")
        with RecordStore(run_output) as store:
            plans = freeze_inputs(scene, plans, run_output, build=build,
                                  context=output / "observations/plans.json")
            repetition = RepeatedPlans(load_plans(plans))
            stream = repetition.iterate() if REPEAT else iter(repetition.plans)
            console.phase(f"Deploying {build['satellites']} satellites and {build['ground_stations']} GS")
            with deployment:
                console.phase("Containers ready; preparing Runner and BPF tables")
                with Runner(stream, deployment, output=run_output, store=store) as runner:
                    console.phase("Runner ready; preparing UT and TCP echo")
                    with TerminalExperiment(scene, deployment, plans, run_output,
                                            seconds=None if REPEAT else duration, capture=CAPTURE,
                                            ground_stations=build["experiment"]["ground_stations"],
                                            repetition=repetition if REPEAT else None):
                        console.watch(runner)
                        runner.run()
                        console.finish()
                        console.phase("Stopping traffic and releasing runtime resources")
            records = archive_measurements(store, run_output)
    finally:
        console.close()

    print("Saving results and replay from recorded operations…", flush=True)
    traffic = write_results(output, run_output)
    write_replay(scene, plans, run_output, traffic, store_records=records)
    write_index(output)
    print(f"Results: {run_output / 'views/results/index.html'}")
