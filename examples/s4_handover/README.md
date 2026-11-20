# S4: build, inspect and run

[中文](README.zh-CN.md)

S4 is the reference configuration for this configurable handover example.
The default workflow builds 348 S4 satellites, 32 GS and 732 global zones, precomputes
600 seconds of BPF label forwarding, then attaches a temporary UT for manual
communication and observation of handovers and a 60-second regional GS outage.

| File | Main calls | Artifacts |
|---|---|---|
| [scene.py](scene.py) | Create and save Scene | `output/scene/scene.scene` |
| [emu.py](emu.py) | SEED configuration, dump/render/compile, image-build Tool | `output/seed/` |
| [simu.py](simu.py) | P&C, model registration, two plan streams, Compiler; save optional drawing data | `output/plans/`, `observations/`, `build.json` |
| [view.py](view.py) | Standard Tools read saved files and export layer views | `output/views/`, `index.html` |
| [run.py](run.py) | Deployment, Runner and temporary UT | `output/runs/run-timestamp/` |

`SHELLS = ("S4",)` in `scene.py` selects the [parameter presets](../../data/README.md).
Use `("S5",)` or `("S1", "S2", "S3")` to reuse this workflow without copying another
example. Each complete preset goes to `Scene.create_shell`, including S5's nonuniform
RAAN and phase parameters. Custom geometry also belongs here. GS use individual site
records, and subsequent stages share the saved `.scene`.
The manual's specific times and paths refer to S4.

Complete [development setup](../../README.md#installation) first. If the local `skyseed`
shortcut is configured, use it to enter the environment and checkout. Existing environments
need a one-time `python -m pip install -e ./tools` from the repository root after updating.

From the repository root in the development environment:

```bash
cd examples/s4_handover
python scene.py
python emu.py
python simu.py
python view.py
# After inspecting the plans, run the real network when needed:
python run.py
```

`simu.py` prints each stage and each 60-second planning interval as it runs.
Interval bounds are simulation time; `elapsed` reports actual computation time.
`view.py` reports each layer as it writes pages and prints the `output/index.html` entry.

View starts no containers or P&C. Repeating it only reads
artifacts and renders pages. Run starts the real network; repeated runs reuse
plans. Requirements are in [Runner](../../src/skyseed/runner/README.md).
Scene changes require matching artifacts. Stop the previous run and rename its
`output` in this example directory (for example `mv output output-s4`), then rebuild
from `scene.py`. Larger constellations require more planning/runtime resources. Check coverage, handover timing and hop counts in
the new scene; the default S4 walkthrough does not establish those for S5 or multiple shells.

[Manual tests, handover observations and shortcuts](MANUAL.md). Runs repeat by default; Ctrl+C cleans up.
You can also enter this example directory and execute `python run.py` directly.

## Public interfaces and example extensions

Imports from `skyseed` and `seedemu` are public interfaces.
`tools.deployment.build_images` and `tools.observation.plan_views` are independently
usable repository Tools. [experiment/](experiment/README.md) contains helpers,
with no second Simu/Run workflow:

- `ut/`: temporary access and the GS's UT prefix classifier.
- `perturbation.py`: circular regional GS outage Model.
- `configuration.py`, `observation/`: settings summary and offline plan navigation.
- `console.py`, `repetition.py`: runtime status and repeated saved plans.

The main entries show registration and attachment explicitly.
`list(generate_*_plans(...))` consumes the iterators to carry out planning requests.
`timedelta(seconds=60)` sets each planning request's duration.
Python's `with` manages resources, releasing UT, Runner and deployment in that order.

## Saved artifacts and Tools

```python
from tools.observation.plan_views import write_link

write_link(
    scene_file, base_link_file, execution_link_file,
    context=observation_file, output=view_directory,
)
```

Scene can be inspected directly. Link/Route globe views also need positions;
candidate/service decisions need optional observations. Simu explicitly calls
`save_context` during calculation to preserve these facts. Viewing them later
never reruns models. Observation JSON is for display; `.plan` files remain the
reusable plans. Physical shows position/candidate snapshots; Compiler shows the
backend PhysicalPlan. See [observation Tools](../../tools/observation/README.md).

```text
output/
  scene/scene.scene
  seed/{emulator.bin,deployment/}
  plans/{logical/,physical/bpf.plan}
  observations/plans.json        # Geometry and optional model observations
  build.json                     # Example experiment settings
  views/{scene,physical,link,network,compiler}/
  runs/run-timestamp/             # Execution, attachment state and diagnostic logs
  index.html
```

[Experiment notes](experiment/README.md) explain configuration, packet paths,
perturbation and manual observation.
