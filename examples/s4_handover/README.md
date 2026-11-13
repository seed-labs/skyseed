# S4: build, inspect and run

[中文](README.zh-CN.md)

One workflow builds 348 S4 satellites, 32 GS and 732 global zones, precomputes
600 seconds of BPF label forwarding, then uses a temporary UT and one TCP session
to observe handovers and a 60-second regional GS outage.

| File | Main calls | Artifacts |
|---|---|---|
| [scene.py](scene.py) | Create and save Scene | `output/scene/scene.scene` |
| [emu.py](emu.py) | SEED configuration, dump/render/compile, image-build Tool | `output/seed/` |
| [simu.py](simu.py) | P&C, model registration, two plan streams, Compiler; save optional drawing data | `output/plans/`, `observations/`, `build.json` |
| [view.py](view.py) | Standard Tools read saved files and export layer views | `output/views/`, `index.html` |
| [run.py](run.py) | Store, Deployment, Runner, UT; archive and render results | `output/runs/run-timestamp/` |

`scene.py` reads the S4 [parameter preset](../../data/README.md) and explicitly
calls `Scene.create_shell`: six planes with 58 satellites each. Change the shell
name and `satellites_per_plane` to 59 or 60 for a custom layout. GS remain
individual source records. The saved `.scene` contains the complete generated
scenario, so Emu and P&C load the same identities and geometry without regeneration.

From the repository root in the development environment:

```bash
python -m examples.s4_handover.scene
python -m examples.s4_handover.emu
python -m examples.s4_handover.simu
python -m examples.s4_handover.view
# After inspecting the plans, run the real network when needed:
python -m examples.s4_handover.run
```

View starts no containers, etcd, P&C or measurements. Repeating it only reads
artifacts and renders pages. Run starts the real network; repeated runs reuse
plans. Requirements are in [Runner](../../src/skyseed/runner/README.md).
Scene changes require matching artifacts. SEED does not overwrite an existing
deployment directory; choose a new output directory for another build.

[Manual tests, handover observations and shortcuts](MANUAL.md). Runs repeat by default; Ctrl+C cleans up and saves results.

## Public interfaces and example extensions

Imports from `skyseed` and `seedemu` are public interfaces.
`tools.deployment.build_images` and `tools.observation.plan_views` are independently
usable repository Tools. [experiment/](experiment/README.md) contains helpers,
with no second Simu/Run workflow:

- `ut/`: temporary access, the GS's UT prefix classifier and TCP applications.
- `perturbation.py`: circular regional GS outage Model.
- `record_store.py`: this example's etcd hosting, frozen inputs and measurement archive; Store itself remains public.
- `configuration.py`, `observation/`: settings summary, traffic results, UT/outage overlays and navigation.

The main entries show registration and attachment explicitly.
`list(generate_*_plans(...))` consumes the iterators to carry out planning requests.
Python's `timedelta(seconds=60)` is a duration, not a model sampling interval.
Python's `with` manages resources, releasing UT, Runner, deployment and Store in that order.

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
  runs/run-timestamp/             # Frozen inputs, execution, measurements, results
  index.html
```

[Experiment notes](experiment/README.md) explain configuration, packet paths,
perturbation and interpretation of results.
