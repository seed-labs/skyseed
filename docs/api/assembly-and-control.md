# Assembly and control

This is a navigation map for scenario authors and advanced integrations. For a complete
runnable flow, start with [scene construction](../user-guides/inputs-and-scenes.md)
and [computation and controls](../user-guides/computation-and-controls.md). Model hooks
should compute their own results, not drive the owners that invoke them.

## Scene construction

Import `configuration`, `emulator`, `adapters` and `simulator` from `skyseed`.

| Entry | Purpose and result |
|---|---|
| `configuration.emulator.load_shell_configs(path)` | Read shell data into configuration values |
| `configuration.emulator.load_ground_station_configs(path)` | Read GS data separately |
| `emulator.create_static_emulator(config)` | Build a SEED Emulator from a complete static configuration; does not deploy it |
| `emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer("Base"))` | Obtain the SkySEED AS owners in the SEED Base layer |
| `adapters.create_simulator_static_input(systems)` | Project those AS owners into immutable Simulator input; pass the AS iterable, not the Emulator itself |

The [configuration module](../../src/skyseed/configuration/emulator/README.md)
describes file formats. For explicit construction, an
[`EmulatorAutonomousSystem`](../../src/skyseed/emulator/README.md) provides
`create_shell(shell_id)` and `create_ground_station(ground_station_id, location=..., ...)`;
a Shell provides `create_orbit(orbit_id, parameters=...)`; an Orbit provides
`create_satellite(...)` or `create_satellites(...)`. The [Tiny example](../../examples/Sky00_tiny_network/tiny_network.py)
shows exact constructor arguments. [S4](../../examples/Sky02_s4_default/s4.py) separately
loads satellite and GS data before building the same entity hierarchy.

Emu AS lookups `get_shell`, `get_satellite`, `get_ground_station` use local string IDs;
`shells()` and `ground_stations()` are methods. Shell/Orbit `orbits`/`satellites` are
properties. These are mutable construction owners, distinct from the
[immutable physical entities and NodeId queries](physical.md#physicalworld).

## Forwarding selection

Use `forwarding_backend="bpf"` (the default) or `"linux"` when constructing the
Emulator and default Simulator. `backend.ForwardingBackend.BPF`/`.LINUX` are the same
canonical values exported from pure configuration. Define the choice once:

```python
mode = "linux"
network = emulator.create_static_emulator(config, forwarding_backend=mode)
simulation = simulator.create_default_simulator(definition, forwarding_backend=mode)
```

Controller inherits the Emulator choice and rejects conflicts. For a manually constructed
SEED Emulator, select it through `ExperimentController(..., forwarding_backend=mode)`.
Standalone `Producer` and `compile_plan` accept the same execution choice. A supplied
Simulator/Model is never replaced by Controller. Only default assembly chooses the
BPF gateway RouteModel or empty Linux RouteModel; Definition and Planner remain neutral.

BPF authors may pass `dataplane_program=BpfProgram(...)` to Controller/Producer;
omission selects the packaged program. Linux rejects that BPF-specific option.
See [C/ELF authoring](../development/dataplane.md) and [ordinary node payloads](../user-guides/node-payloads.md).
Protocol software and route arrangements are separate author choices.

## Native routing software

The optional construction adapter
[`emulator.configure_native_software`](../../src/skyseed/emulator/native_software.py)
prepares one selected SEED node from concrete configuration: BIRD text or an FRR
`dict` of daemon names to text, including `zebra`. The SkySEED adapter validates the
complete configuration before node mutation and uses public SEED Node facilities
for packages, files, base-system selection and startup. Validation covers supported
configuration shapes and duplicate preparation; it does not parse protocol syntax.
BIRD installation retains the package's example-directory workaround and router base;
FRR starts only requested daemons, with Zebra first and other daemons waiting for its
`zserv.api` socket. No extra SEED convenience methods or Routing Layer are required.
Check syntax with the intended daemon version and inspect its diagnostics: FRR
8.4.4 can report an invalid command during `-C` while returning exit code zero.
Do not also manage the same node with a SEED Routing Layer.

Ordinary author code may read files and use Python's standard `string.Template`.
This self-contained snippet assumes your scene has a SkySEED AS named `system`;
it does not import another Example:

```python
from pathlib import Path
from string import Template
from skyseed import adapters, emulator
from skyseed.backend.linux.environment import NativeRoutingEnvironment
from skyseed.simulator import OperationalConnectionId, OperationalConnectionKind

static_input = adapters.create_simulator_static_input((system,))
environment = NativeRoutingEnvironment(n.node_id for n in static_input.list_nodes())
template = Template(Path("bird.conf.template").read_text())
stations = tuple(s.node_id for s in static_input.list_ground_stations())
for satellite in static_input.list_satellites():
    node = satellite.node_id
    access_names = [environment.interface_name(OperationalConnectionId(
        OperationalConnectionKind.GROUND_STATION_ACCESS, node, gs), node)
        for gs in stations]
    text = template.substitute(
        identity=environment.node_addresses[node],
        identity_interface=environment.identity_interface,
        exclusions=''.join('-"' + name + '", ' for name in access_names))
    emulator.configure_native_software(system.get_satellite(satellite.satellite_id),
                                      daemon="bird", configuration=text)
```

For example, `bird.conf.template` can contain the following minimal satellite
transit policy (GS redistribution and business routes are separate author choices):

```bird
router id ${identity};
protocol device {}
protocol kernel {
    include "/etc/skyseed/bird-kernel-options.conf";
    ipv4 { import none; export all; };
}
protocol ospf v2 {
    ipv4 { import all; export none; };
    area 0 {
        interface "${identity_interface}" { stub yes; };
        interface ${exclusions}"sv2*" { type ptp; };
    };
}
```

`node_addresses[node]` supplies a stable /32 identity. `identity_interface` is the
interface on which Compiler/Runner manage those identities and changing Zone service
addresses. `link_address_pools` gives the reserved native link prefixes; individual
future link addresses are allocated during compilation, not guessed by software
authors. `interface_name(connection, endpoint)` supplies deterministic prospective
names even before interfaces exist. Use the **complete node set**, including GSs
and all shells, for `NativeRoutingEnvironment`; a subset may allocate different
addresses. Supply the same projected input to your SimulationDefinition.

The adapter supplies two dynamic-environment defaults. BIRD gets
`/etc/skyseed/bird-kernel-options.conf`, containing `netlink rx buffer 4194304;`.
The author explicitly includes that file inside each applicable kernel protocol:

```bird
protocol kernel skyseed_kernel {
    include "/etc/skyseed/bird-kernel-options.conf";
    # Authored learn, metric and IPv4 import/export policy follows.
}
```

The adapter leaves BIRD text untouched. Omitting the include omits this default;
placing a different `netlink rx buffer` value after it overrides the value through
BIRD's own configuration grammar. FRR gets `no zebra nexthop kernel enable` before
the authored Zebra text; a later top-level `zebra nexthop kernel enable` explicitly
overrides it. Both defaults concern daemon behavior, not host sysctls. Area,
timers, participating/passive interfaces, import/export and redistribution remain
ordinary Example policy. These facilities do not guarantee arbitrary authored
configurations are valid or receive the defaults.

Users may instead use ordinary SEED Node `addSoftware`, `setFile`/`importFile`,
`addBuildCommand`, `setBaseSystem` and `appendStartCommand` directly. Such preparation
is independent of this optional adapter and supplies its own configuration/files.
The adapter needs only its selected node; it performs no world projection,
configuration callback, runtime work or identity allocation. There is no multi-node
transaction: validation and preparation apply to each individual call.

The separate read-only
[`NativeRoutingEnvironment`](../../src/skyseed/backend/linux/environment.py)
requires the **complete node identity set** when generating stable addresses.
Later additions can change allocations, so regenerate identity-dependent
configuration/materials after changing that set. This requirement belongs to
address allocation, not generic per-node software installation. Construct the
scene with `forwarding_backend="linux"`; downstream assembly inherits it.

The environment exposes `node_addresses`, `node_address_range`,
`identity_interface`, `link_address_pools`, `route_protocol`, `route_priority`, and
`interface_name(connection, endpoint)`. Compiler uses the same implementation.
It contains no future link allocation or Zone schedule; service addresses appear
and disappear on the identity interface during execution. Protocols advertising
them respond to actual address notifications. See
[native S4](../../examples/Sky06_s4_native_ospf/README.md) for editable policy and
the normal generate/deploy/run/cleanup workflow.

## Simulator assembly

| Entry | Inputs → result / effect |
|---|---|
| `SimulationTimeline(start_time, end_time, observation_interval)` | Timezone-aware UTC datetimes and positive timedelta → initial calculation range and cadence |
| `SimulationDefinition(static_input, timeline)` | Pure scene input and Timeline → construction value |
| `simulator.create_default_simulator(definition, forwarding_backend="bpf", ..., route_model=your_model)` | Assemble World and Planner; omitted RouteModel uses BPF gateway routes or Linux empty routes; supplied instances are used directly |
| `simulation.get_definition()` / `get_timeline()` | Read original definition / initial Timeline |
| `simulation.get_physical_world()` / `get_planner()` / `get_physical_link_model()` | Read assembled owners or the configured physical-link Model |
| `simulation.shutdown(wait=True)` | Close owned Model resources; called by workflow ownership during normal completion/cancellation |

Use `DefaultSimulatorConfiguration` for built-in settings and `PlannerConfiguration`
for acquisition durations and Plan batch size; cadence belongs to the Timeline.
A custom Model's constructor belongs
to its author; it need not accept built-in configuration parameters.
[Factory source](../../src/skyseed/simulator/defaults.py) lists all independent
replacement keywords. For manual assembly,
[`Simulator(definition)`](../../src/skyseed/simulator/simulator.py) provides
`create_physical_world(...)` then `create_planner(...)`; getters require their owner
to have been created. Assembly alone neither computes the Timeline nor launches containers.

## Calculation and continuation

Normal examples use `ExperimentController` from `skyseed.experiment`:
`ExperimentController(network, simulation, output, entry_path=..., through=...)`.
`output` and `entry_path` are Paths; `through` is `"physical-world"`, `"planner"` or
`"compiler"`. Full options and returns are in
[`controller.py`](../../src/skyseed/experiment/controller.py).

| Method | Effect |
|---|---|
| `generate()` | Finite generation and cleanup of computation owners; returns output Path |
| `serve(delivery_duration=..., run=False, ...)` | Generate, publish and retain computation; blocks until cancelled/completed lifecycle; default does not deploy or run |
| `continue_calculation(additional_duration=..., end_time=..., delivery_duration=...)` | Request a suffix on the live retained owner; supply either a timedelta duration or an aware end datetime, aligned with cadence; returns RequestReceipt |
| `status()` | Read local calculation diagnostics without store I/O or shared freshness/version guarantees |
| `cancel_computation()` / `cancel_runtime()` | Request the corresponding cancellation; acceptance is not completed drainage |
| `revise_future(at=None, request_id=None)` | Return a receipt for complete eligible future-suffix recalculation; no implicit Runner wait |

Use generated `experiment_control.py` from another terminal while `serve()` blocks.
`continue` extends calculation; `resume` releases the runtime update hold. Pause/resume/reset
belong to the generated execution controls, not to the Timeline or a Model. The original
definition remains the initial range; status/publication report extensions. A finite
`generate()` does not leave a computation session for later `continue`.

For a custom calculation driver, [`Producer`](../../src/skyseed/experiment/README.md)
owns ordered physical evaluation, Planner progression and optional compilation. Its
`produce_next_observation()`, `produce_next_plan()` and `produce_next_realization()`
advance work; `snapshot()` reads progress. The last method's existing name refers to a
backend-identified compilation result. Prefer one Producer over independently advancing each owner.

Lower-level [`Planner`](../../src/skyseed/simulator/planner/planner.py)
operations include `prime(frame, observation, formal_start=...)`,
`observe(frame, observation)`, `flush()`
and `finish(end_timestamp=...)`. These change calculation state; `finish` closes further
extension, while `flush` publishes pending work without closing it. They are not lookup
methods. `set_route_demands(demands)` changes future route inputs; an explicit empty
iterable disables automatic demands. See the [Planner lifecycle](../development/planner-state-and-plans.md).

Choose forwarding once before deployment preparation: `forwarding_backend="linux"`
on `create_static_emulator` or, for a manually constructed SEED Emulator, on
`ExperimentController`. Omission keeps BPF; contradictory selections and Linux with
`dataplane_program` fail. `backend.ForwardingBackend` supplies the enum values.
For normal backend-aware assembly, call
`simulator.create_default_simulator(definition, forwarding_backend=backend, ...)` with the
same `backend` used for the Emulator/Controller. Omitted or `None` RouteModel means
empty business route arrangements on Linux and the existing gateway Model on BPF.
Topology, Zone/service computation, stable identities and dynamic changes remain.
An explicitly supplied Model is used unchanged, including empty, partial and full
arrangements. Use one backend variable for Emulator and Simulator construction;
Controller inherits the Emulator choice and never swaps a Model later. The factory
uses the mode only to select omitted defaults. SimulationDefinition, Planner and
user Model semantics contain no backend execution policy. The canonical enum and
validator live in pure `configuration.forwarding`; `backend.ForwardingBackend`
remains the public export. Simulator definitions contain no container/runtime inputs. Linux supports full paths,
unconditional local single-candidate forwarding, PASS and explicit DROP; unsupported
policy expressions reject offline. Empty outcomes withdraw previous owned routes.
`traffic_enabled` selects
GS/Zone generation; `traffic_telemetry_enabled` independently selects BPF packet/path
collection. Both default on for BPF and off for Linux. Linux may explicitly enable
generation when its scene provides the required identities and routing; enabling
BPF telemetry for Linux is rejected. See [Traffic generation](../../tools/generation/traffic/README.md).
See the [native route expression contract](../development/compiler-and-runner.md#supported-route-expressions).
See [native execution and address ownership](../development/compiler-and-runner.md#native-ipv4-foundation).

`compile_plan(plan, physical_world, previous_result, forwarding_backend=...)` from
`skyseed.backend` returns `CompilationResult` (defined in `backend.compiler.result`).
Standalone Producer accepts the same backend keyword.

`compile_bpf(plan, physical_world, previous_result)` from `skyseed.backend.bpf`
returns `BpfCompilationResult`. The first previous result is `None`; subsequent calls
use the predecessor result. Translation does not deploy the network. Producer also
handles batching and sealed delivery; a standalone result is not a running network.

## Deployment and execution

These entry points perform real host work. Most users should use the generated scripts
so the normal owner handles setup, delivery, cancellation and cleanup.

| Owner / entry | Operation |
|---|---|
| `skyseed.deployment.Deployer(output)` | Own one output's deployment |
| `deployer.prepare(emulator, ...)` | Render SEED/Compose inputs and control scripts |
| `deployer.build()` / `start()` / `status()` / `cleanup()` | Build images, start containers, inspect deployment, remove owned deployment resources |
| `skyseed.backend.bpf.Runner(...)` | Own one execution invocation; see [constructor and lifecycle](../../src/skyseed/backend/runner/lifecycle.py) |
| `runner.prepare()` / `activate_next()` / `run()` | Prepare backend resources, apply the next network boundary, or drive paced execution |
| `runner.status()` / `pause()` / `resume()` | Inspect or control logical execution progress |
| `runner.append_batch(runtime_artifact)` | Register one next sealed Path; not an arbitrary future queue; subsequent delivery follows consumption |
| `runner.cancel_runtime()` / `cleanup()` | Request cancellation / release owned backend resources; cleanup does not remove SEED containers |

Normal generated execution retains the last ACTIVE state while waiting for material.
For manual Runner construction, `keep_alive` defaults to `False`; select `True` when
you need the retained behavior. Reset is managed by the outer execution owner creating
a new Runner; there is no `Runner.reset()` method. See
[backend ownership](../development/compiler-and-runner.md) for delivery and cleanup boundaries.

The optional `ExperimentController(initial_readiness_command=argv)` runs a captured
author command after B0 preparation, before Traffic, future delivery and formal
execution time. Omission adds no wait. See the [readiness contract](../development/experiment-control.md#initial-readiness-command)
for input capture, command arguments, evidence, cancellation and reset.

## Optional Tools

Repository examples use `import tools` from the application directory; this package is
separate from the installed `skyseed` library. `tools.write_experiment_tools(output,
physical_world=world, publication=...)` writes views of a completed publication. Omit
`publication` to read the current completed record; a retained Controller callback
receives the exact publication to pass. It does not rerun Models to fill missing data.

Per-Tool entry points and the optional active Traffic workflow are described in
the [Tools README](../../tools/README.md) and [output guide](../user-guides/outputs-and-tools.md).
Tools consume available domain data; custom visualization requirements do not expand
the minimum Model contract.

## Select packet processing

`ExperimentController(..., dataplane_program=BpfProgram(Path("packet.c")))`
selects a C/eBPF handler. Import `BpfProgram` from `skyseed.backend.bpf`; omit
the keyword for the default. The lower-level `Producer` accepts the same keyword.
See the [template](../../templates/dataplane/README.md) and
[C/object contract](../development/dataplane.md) for source/header, multiple-unit
and precompiled-object inputs, shared support and ownership.

For structured data, import `RouteData`, `Record`, scalar types, `AttachmentScope`, `DataPlaneBinding`, and `DataPlane` from `skyseed.backend.bpf.dataplane`. `RouteData.generate_headers(directory)` works before a Plan/C source exists; select `BpfProgram(path, data=definition, authoring_directory=directory)` through the same `dataplane_program` keyword. `DataPlane(bindings)` makes node coverage explicit through `AttachmentScope(nodes=(...))`, with one program/profile per selected node shared by network and local ingress. Endpoint selection is removed; partial/empty coverage remains valid. See the [contract](../development/dataplane.md) and [teaching example](../../templates/dataplane/threshold/README.md).

## User preparation function

Declare a module-level function in the Example and pass it to the Controller:

```python
import time

def prepare(context):
    time.sleep(600)  # No routing query or return report is required.

controller = experiment.ExperimentController(
    network, simulation, output, entry_path=Path(__file__),
    preparation=prepare, preparation_inputs={"label": "my experiment"},
    input_files=(Path("bird.conf.template"),))
```

`controller.generate()` captures the function's module and listed inputs. Invoke
`output/run.sh` later in a separate process. The captured module is loaded with a
non-main name; protect generation work with `if __name__ == "__main__":` and avoid
import-time side effects. Include local imported modules/data in `input_files`;
ordinary relative imports and sibling modules use the captured sources. Include
package initializers if their code is needed; see the
[package capture contract](../development/experiment-control.md#user-preparation-function). Installed third-party
packages remain environment dependencies. Closures and bound methods are rejected;
pass required values as JSON-compatible `preparation_inputs` instead of generator
process globals. Source files are not a pickle of a running Python process.

The single argument is `experiment.PreparationContext`. It provides
`output_directory`, `invocation_directory`, detached `inputs`, `source_files`
(original absolute path → captured Path), `runner_snapshot` (including actual
container bindings and initial ACTIVE frontier), `initial_state` (PlannedOperationalState)
and `runtime_manifest` (backend, identities, Zone prefixes and interface facts).
Read these supplied initial facts instead of rescanning complete histories.

Normal completion releases Traffic, successor delivery and the formal clock.
No hook means no wait. It runs once per fresh invocation, never on topology updates
or ordinary pause/resume. Errors and cancellation use existing execution cleanup;
there is no preparation deadline. Functions spawning children own their cooperative
drainage, especially for Docker exec or detached sessions. The Example's BIRD check
is ordinary user code; no protocol-specific interface or convergence report is required.
Do not supply both `preparation` and the lower-level `initial_readiness_command`.
See the [execution lifecycle contract](../development/experiment-control.md#user-preparation-function).

## Independent feedback clients

`skyseed.experiment` exports `InteractionClient`, `RequestReceipt`, `InteractionUnavailable`, and
`InteractionHistoryCompacted`. Use `from_output(path)` on the host or `from_environment()`
with the provisioned container client. `state(owner)` reads a time/version/session-stamped
publication. `submit(owner, command, request_id=..., at=..., **arguments)` returns a
receipt; `result(receipt)` and `wait(receipt, timeout=...)` report execution separately.
`query(topic, **arguments)` requests focused runtime facts. `shutdown()` coordinates
both owners and returns an owner-to-receipt mapping. Close clients when finished;
`close()` does not stop owners or Etcd. Source signatures are in
[`interaction.py`](../../src/skyseed/experiment/interaction.py).

| Public operation | Inputs and returned data |
|---|---|
| `from_output(path, container=False)` / `from_environment()` | Host discovery file or generated endpoint/experiment environment; no service startup |
| `state("computation" or "runtime", revision=None)` | Last owner facts, outcome, session, observation/store versions and separate freshness/availability |
| `query("materials", offset=0, limit=100, timeout=...)` | Request result envelope containing selected references and Runner freeze/progress |
| `query("bindings", node_ids=None, offset=0, limit=100, timeout=...)` | Envelope containing runtime identity/address facts; node IDs are canonical strings |
| `query("communication", ground_station_id=..., zone_id=..., network_scope="", timeout=...)` | Envelope containing ACTIVE endpoint/service facts, not a reachability test |
| `query("inspect", timeout=...)` | Envelope containing host-side inspection path and invocation ID |
| `submit(owner, command, at=None, request_id=None, session_id=None, **arguments)` | `RequestReceipt`; `artifact()` produces JSON-compatible fields for retention |
| `result(receipt)` / `wait(receipt, timeout=None)` | Progress or terminal result; `result` may return `None`; `wait` timeout raises without cancelling |
| `user_get(key, revision=None)` / `user_put(key, value, expected_version=None)` | Record with `value`, modification `version` and read revision, or `None` for a missing read; version conflict raises `ValueError` |
| `user_read(keys, revision=None)` / `watch_user(key="", start_revision=None, timeout=None)` | Optional consistent records or one change; no automatic interpretation |

Optional arguments shown here are keywords. Queries need a live runtime consumer;
state, user values and retained results only need the service. Pagination limits for
bindings/materials are 1–100. Inspect the envelope's `status` before reading its
`result`; `SUCCEEDED` for a query means the observation was produced, not that every
observed resource is healthy. Timeouts/outages do not establish that a mutation failed.

Controller accepts `interaction_endpoint`, `interaction_container_endpoint` and
`interaction_listen_host`. Omit them for the ordinary output-owned member/native
container socket. An external endpoint uses administrator-owned lifecycle and
retention. See [setup and external-service limits](../user-guides/computation-and-controls.md#stop-restart-or-use-external-etcd),
including the absence of public credentials/client-certificate configuration.

`ExperimentController.continue_calculation`, `cancel_computation`, `cancel_runtime`,
and `revise_future(at=None, request_id=None)` submit through this contract. The same-process
`status()` reads local diagnostics even before startup, without shared version/session/
freshness semantics. Use `interaction.state('computation')` or `state('runtime')` for
owner publications. `state_history(owner, after_revision=..., limit=...)` pages meaningful
changes across sessions; `watch_state(owner, start_revision=..., timeout=...)` waits for
one change, excluding heartbeats. Compacted pinned reads/watches raise
`InteractionHistoryCompacted`; explicitly reread retained state history/current values. Store outage or receipt timeout does not establish
rejection/completion.
`user_get`, `user_put(expected_version=...)`, `user_read` and `watch_user` carry unrestricted
JSON values with no automatic calculation effect. See the
[working examples and setup](../user-guides/computation-and-controls.md#interaction-setup-and-independent-programs)
and [precise ownership](../development/experiment-control.md).
