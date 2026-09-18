# Generate, continue, deploy and control

All six examples default to foreground retained computation. They compute only the initial
finite request, publish results and optional views, and wait efficiently for another request.
They do not deploy, execute or calculate an unbounded future by default.

## Initial range and finite cutoffs

```bash
# Default retained S4; five-second cadence is declared in the entry.
python examples/Sky02_s4_default/s4.py --duration-seconds 600 --batch-seconds 300

# One-shot calculation at any cutoff; these examples exit after publication.
python examples/Sky00_tiny_network/tiny_network.py --operation generate --through physical-world --output /tmp/tiny-physical
python examples/Sky01_s5_custom_models/s5.py --operation generate --through planner --duration-seconds 30 --output /tmp/s5-plans
python examples/Sky00_tiny_network/tiny_network.py --operation generate --through compiler --output /tmp/tiny-materials

# Retention also works at partial cutoffs.
python examples/Sky00_tiny_network/tiny_network.py --through planner --output /tmp/tiny-retained
```

PhysicalWorld produces positions/candidates. Planner additionally publishes Plans. Compiler
additionally creates execution materials and deployment controls. Partial cutoffs do not
prepare deployment or execute downstream stages. `--no-tools` disables optional rendering;
`--no-traffic` disables active Traffic inputs/collection. Neither option changes Model results.

Observation cadence, `PlannerConfiguration.frames_per_batch`, and delivery duration are
independent. A delivery can flush a partial Plan while retaining operational state. Example
Python constants `DEFAULT_OPERATION` and `DEFAULT_BATCH_DURATION` define lifecycle defaults;
initial duration is a Timeline setting. CLI options override those choices.

## Direct Python configuration

```python
from datetime import timedelta
from pathlib import Path
from skyseed_v2.experiment import ExperimentController
import tools

# network and simulation come from your explicit scene/Simulator assembly.
controller = ExperimentController(network, simulation, Path("output"),
    entry_path=Path(__file__), through="planner", traffic_enabled=False)

def show_completed(output, publication):
    return tools.write_experiment_tools(output,
        physical_world=simulation.get_physical_world(), publication=publication)

controller.serve(delivery_duration=timedelta(seconds=300), on_completed=show_completed)
# For a one-shot script, use controller.generate() instead, then optionally render.
```

While `serve()` owns its foreground thread, another caller may use
`controller.continue_calculation(additional_duration=timedelta(seconds=300))`, or
`end_time=<aware datetime>`, with an optional new `delivery_duration`. The generated command
client is usually simpler from a second terminal. Its editable defaults are ordinary Python
values: `ADDITIONAL_SECONDS`, `TARGET_END` and `BATCH_SECONDS`.

## Continue from a second terminal

```bash
cd examples/Sky02_s4_default/output
python experiment_control.py status
python experiment_control.py continue --seconds 300 --batch-seconds 300
# Alternative target form:
python experiment_control.py continue --until 2026-01-01T00:20:00+00:00
```

After 0–600 seconds at five-second cadence, adding 300 computes 605–900. It reuses the
original scene, Models, Planner memory and Compiler predecessor. Targets must increase and
align to the original cadence. Status separates initial/requested/completed endpoints,
material batches, computation waiting and presentation. Refresh the browser after a completed
request; the view may lag while rendering and identifies its recorded data. A terminated
calculation process cannot be recovered by `continue`.

## Deployment and execution

For Compiler output only:

```bash
./deploy.sh                      # Deployer builds and starts Compose
./run.sh                         # Runner plus explicitly enabled Traffic; separate foreground
python experiment_control.py pause
python experiment_control.py resume
python experiment_control.py reset
python experiment_control.py status
```

Run controls from another terminal while `run.sh` is active. Pause holds network updates after
a complete boundary; container apps, wall clocks and in-flight traffic continue. Resume releases
that pause and does not request calculation. Reset drains Traffic/Runner/evidence and starts
a fresh invocation over retained materials without recalculating Models or rebuilding Compose.
A retained Runner with exhausted materials waits for a successor.

Explicit automation remains available through `--operation run` and
`run_generated_experiment(output, cleanup=True)`. The former retains calculation and manages
execution; the latter operates existing generated controls. `cleanup=False` assigns deployment
teardown to its caller. Neither is an example default.

## Exit and cleanup

```bash
python experiment_control.py cancel-computation   # calculation only
python experiment_control.py cancel-runtime       # Runner/Traffic only
# Or request both owners:
python experiment_control.py shutdown
# Wait for run.sh to finish its cleanup, then for independently deployed output:
./cleanup.sh
```

Ctrl-C follows the foreground operation's owned drain. Cancellation requests are not proof
that cleanup has finished. Independently deployed containers remain Deployer-owned until
`cleanup.sh`; managed execution performs that teardown after runtime drainage. Repeated Ctrl-C
cannot abandon accepted cleanup. No elapsed-time cutoff forces a process kill.
