# Assembly and control

This is a navigation map for scenario authors and advanced integrations. For a complete
runnable flow, start with [scene construction](../user-guides/inputs-and-scenes.md)
and [computation and controls](../user-guides/computation-and-controls.md). Model hooks
should compute their own results, not drive the owners that invoke them.

## Scene construction

Import `configuration`, `emulator`, `adapters` and `simulator` from `skyseed_v2`.

| Entry | Purpose and result |
|---|---|
| `configuration.emulator.load_shell_configs(path)` | Read shell data into configuration values |
| `configuration.emulator.load_ground_station_configs(path)` | Read GS data separately |
| `emulator.create_static_emulator(config)` | Build a SEED Emulator from a complete static configuration; does not deploy it |
| `emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer("Base"))` | Obtain the SkySEED AS owners in the SEED Base layer |
| `adapters.create_simulator_static_input(systems)` | Project those AS owners into immutable Simulator input; pass the AS iterable, not the Emulator itself |

The [configuration module](../../src/skyseed_v2/configuration/emulator/README.md)
describes file formats. For explicit construction, an
[`EmulatorAutonomousSystem`](../../src/skyseed_v2/emulator/README.md) provides
`create_shell(shell_id)` and `create_ground_station(ground_station_id, location=..., ...)`;
a Shell provides `create_orbit(orbit_id, parameters=...)`; an Orbit provides
`create_satellite(...)` or `create_satellites(...)`. The [Tiny example](../../examples/Sky00_tiny_network/tiny_network.py)
shows exact constructor arguments. [S4](../../examples/Sky02_s4_default/s4.py) separately
loads satellite and GS data before building the same entity hierarchy.

Emu AS lookups `get_shell`, `get_satellite`, `get_ground_station` use local string IDs;
`shells()` and `ground_stations()` are methods. Shell/Orbit `orbits`/`satellites` are
properties. These are mutable construction owners, distinct from the
[immutable physical entities and NodeId queries](physical.md#physicalworld).

## Simulator assembly

| Entry | Inputs → result / effect |
|---|---|
| `SimulationTimeline(start_time, end_time, observation_interval)` | Timezone-aware UTC datetimes and positive timedelta → initial calculation range and cadence |
| `SimulationDefinition(static_input, timeline)` | Pure scene input and Timeline → construction value |
| `create_default_simulator(definition, ..., route_model=your_model, ...)` | Assemble World and Planner; omitted roles get defaults, supplied instances are used directly |
| `simulation.get_definition()` / `get_timeline()` | Read original definition / initial Timeline |
| `simulation.get_physical_world()` / `get_planner()` / `get_physical_link_model()` | Read assembled owners or the configured physical-link Model |
| `simulation.shutdown(wait=True)` | Close owned Model resources; called by workflow ownership during normal completion/cancellation |

Use `DefaultSimulatorConfiguration` for built-in settings and `PlannerConfiguration`
for acquisition durations and Plan batch size; cadence belongs to the Timeline.
A custom Model's constructor belongs
to its author; it need not accept built-in configuration parameters.
[Factory source](../../src/skyseed_v2/simulator/defaults.py) lists all independent
replacement keywords. For manual assembly,
[`Simulator(definition)`](../../src/skyseed_v2/simulator/simulator.py) provides
`create_physical_world(...)` then `create_planner(...)`; getters require their owner
to have been created. Assembly alone neither computes the Timeline nor launches containers.

## Calculation and continuation

Normal examples use `ExperimentController` from `skyseed_v2.experiment`:
`ExperimentController(network, simulation, output, entry_path=..., through=...)`.
`output` and `entry_path` are Paths; `through` is `"physical-world"`, `"planner"` or
`"compiler"`. Full options and returns are in
[`controller.py`](../../src/skyseed_v2/experiment/controller.py).

| Method | Effect |
|---|---|
| `generate()` | Finite generation and cleanup of computation owners; returns output Path |
| `serve(delivery_duration=..., run=False, ...)` | Generate, publish and retain computation; blocks until cancelled/completed lifecycle; default does not deploy or run |
| `continue_calculation(additional_duration=..., end_time=..., delivery_duration=...)` | Request a suffix on the live retained owner; supply either a timedelta duration or an aware end datetime, aligned with cadence; returns status |
| `status()` | Read calculation/runtime progress without advancing it |
| `cancel_computation()` / `cancel_runtime()` | Request the corresponding cancellation; acceptance is not completed drainage |

Use generated `experiment_control.py` from another terminal while `serve()` blocks.
`continue` extends calculation; `resume` resumes the execution clock. Pause/resume/reset
belong to the generated execution controls, not to the Timeline or a Model. The original
definition remains the initial range; status/publication report extensions. A finite
`generate()` does not leave a computation session for later `continue`.

For a custom calculation driver, [`Producer`](../../src/skyseed_v2/experiment/README.md)
owns ordered physical evaluation, Planner progression and optional compilation. Its
`produce_next_observation()`, `produce_next_plan()` and `produce_next_realization()`
advance work; `snapshot()` reads progress. The last method's existing name refers to a
BPF compilation result. Prefer one Producer over independently advancing each owner.

Lower-level [`Planner`](../../src/skyseed_v2/simulator/planner/planner.py)
operations include `prime(frame, observation, formal_start=...)`,
`observe(frame, observation)`, `flush()`
and `finish(end_timestamp=...)`. These change calculation state; `finish` closes further
extension, while `flush` publishes pending work without closing it. They are not lookup
methods. `set_route_demands(demands)` changes future route inputs; an explicit empty
iterable disables automatic demands. See the [Planner lifecycle](../development/planner-state-and-plans.md).

`compile_bpf(plan, physical_world, previous_result)` from `skyseed_v2.backend.bpf`
returns `BpfCompilationResult`. The first previous result is `None`; subsequent calls
use the predecessor result. Translation does not deploy the network. Producer also
handles batching and sealed delivery; a standalone result is not a running network.

## Deployment and execution

These entry points perform real host work. Most users should use the generated scripts
so the normal owner handles setup, delivery, cancellation and cleanup.

| Owner / entry | Operation |
|---|---|
| `skyseed_v2.deployment.Deployer(output)` | Own one output's deployment |
| `deployer.prepare(emulator, ...)` | Render SEED/Compose inputs and control scripts |
| `deployer.build()` / `start()` / `status()` / `cleanup()` | Build images, start containers, inspect deployment, remove owned deployment resources |
| `skyseed_v2.backend.bpf.Runner(...)` | Own one execution invocation; see [constructor and lifecycle](../../src/skyseed_v2/backend/bpf/runner/lifecycle.py) |
| `runner.prepare()` / `activate_next()` / `run()` | Prepare backend resources, apply the next network boundary, or drive paced execution |
| `runner.status()` / `pause()` / `resume()` | Inspect or control logical execution progress |
| `runner.append_batch(runtime_artifact)` | Register one next sealed Path; not an arbitrary future queue; subsequent delivery follows consumption |
| `runner.cancel_runtime()` / `cleanup()` | Request cancellation / release owned backend resources; cleanup does not remove SEED containers |

Normal generated execution retains the last ACTIVE state while waiting for material.
For manual Runner construction, `keep_alive` defaults to `False`; select `True` when
you need the retained behavior. Reset is managed by the outer execution owner creating
a new Runner; there is no `Runner.reset()` method. See
[backend ownership](../development/compiler-and-runner.md) for delivery and cleanup boundaries.

## Optional Tools

Repository examples use `import tools` from the application directory; this package is
separate from the installed `skyseed_v2` library. `tools.write_experiment_tools(output,
physical_world=world, publication=...)` writes views of a completed publication. Omit
`publication` to read the current completed record; a retained Controller callback
receives the exact publication to pass. It does not rerun Models to fill missing data.

Per-Tool entry points and the optional active Traffic workflow are described in
the [Tools README](../../tools/README.md) and [output guide](../user-guides/outputs-and-tools.md).
Tools consume available domain data; custom visualization requirements do not expand
the minimum Model contract.
