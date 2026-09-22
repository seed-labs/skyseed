# Component boundaries and input obligations

Use this reference to review dependency direction, ownership and the values passed
between components. For a first explanation of the system, read the
[architecture overview](../architecture/overview.md).

## Owners and dependencies

Emulator constructs the SEED scene using the required ExtensionNode-capable
[fork and tested revision](../user-guides/getting-started.md#python-and-the-required-seed-source). The external
[`create_simulator_static_input`](../../src/skyseed_v2/adapters/emulator_to_simulator.py)
adapter projects it into receiver-owned `SimulatorStaticInput`. Simulator owns
PhysicalWorld, its physical-link Model and Planner. Backend owns a shared Compiler, execution-material package and Runner, with explicit BPF and Linux adapters.
Each backend supplies pure translation and a Runner-owned runtime adapter; the public
`DataPlane` authoring type currently belongs to BPF, not a universal backend plugin API; reusable Linux
kernel mechanisms and the native adapter live in `backend/linux`. The backend selection is bound before SEED deployment preparation; simulation values do not contain it. Compiler material writing invokes translation;
Runner never imports its C compiler/preparation module or intention translator. Producer owns chronological calculation;
ExperimentController integrates the experiment. Deployer owns deployment and
SEED-generated static files. Deployment is not a time-dependent calculation stage.

Simulator never reads Emulator or SEED nodes. Runner receives sealed material paths,
never a Simulator, Producer or calculation callback. Tools consume detached facts;
only the experiment boundary may import explicitly enabled active Traffic.
Shared facilities remain authority-free values, geometry-independent identities,
recording and mechanical I/O. Root imports are inert selectable modules, not a new owner.

Native daemon authoring uses the optional per-node
`emulator.configure_native_software` adapter for concrete configurations. It owns
small dynamic-environment software defaults and delegates installation/files/startup
to public SEED Node facilities, without Routing convenience methods; Example authors
own OSPF and redistribution policy.
The Example projects its scene once and passes its complete identity set to the
backend's read-only `NativeRoutingEnvironment`, also used by native translation.
The software adapter does not project a world, allocate identities or require a
callback. Direct SEED preparation remains available. No environment record crosses
into Runner: compiled foundation remains its only native input. See
[authoring usage](../api/assembly-and-control.md#native-routing-software).
`simulator.create_default_simulator` selects the backend-dependent default RouteModel at
assembly: empty arrangements for Linux, the existing gateway Model for BPF.
Explicit Models pass through unchanged; Simulator static input and definitions
remain backend/runtime independent. Examples pass the same selected backend at both assembly boundaries; the Controller
never replaces Models later. Only the mode enum and validator live in neutral
`configuration.forwarding`; backend selection re-exports the same values. Simulator
assembly has no backend execution dependency; Planner and SimulationDefinition
remain independent of this default choice.
Example-local templates and their renderer hold editable protocol policy. The
native GS↔Zone RouteModel supplies only local edge arrangements. The existing
Traffic Tool selects from those arrangements with unknown transit; it neither
completes routes nor assumes Runner ownership. Its derived workload representation
is documented under [Traffic generation](../../tools/generation/traffic/README.md).

## Identity and construction

`NodeId` is a neutral immutable value derived once from ASN, entity kind and entity ID.
Downstream code does not reconstruct identity by guessing display, service or container
names. Owning lookups reject unknown, wrong-kind or inconsistent identities.
Shell and GroundStation files can be loaded separately; the aggregate YAML format also
remains supported. Configuration validates declarations before SEED construction.
The adapter copies orbital epoch/phase, site coordinates, per-satellite ISL/GS capability
and per-station access capability into pure receiver values. `None` means unspecified
capability, not zero. Engineering fallback limits belong to Models.

## PhysicalWorld and queries

PhysicalWorld owns immutable physical AS/shell/orbit/satellite/site facts, capabilities,
PositionModel and optional ZoneLayout. `frame_at(timestamp)` validates complete position
coverage and returns one immutable `PhysicalPositionSnapshot`: timestamp and ECEF positions.
The snapshot contains no operational selection or time cursor.

A `PhysicalLinkRequest` bounds potential ISL pairs and GS/Zone query families. Planner's
configured selection roles supply the candidate envelope and optional search advice;
physical-only production uses that envelope without advancing planning. The physical-link
Model returns timestamp-matched sparse candidates in `PhysicalLinkQueryResult`.
Missing candidate measurements remain unavailable; feasibility does not imply selection.
Minimum and optional Model requirements are in [Models](model-interfaces.md).

World identity is cached once from static semantics plus the module-qualified types of
Position and membership Models and their optional `semantic_fingerprint_material()`.
Without that hook, same-type undeclared settings are indistinguishable. Private fields,
workers and caches are never inspected; `describe_configuration()` is provenance only.
Declared identity material supports neutral scalars, finite floats, aware datetimes,
timedeltas, NodeIds, enums and recursively supported collections/dataclasses. Opaque
objects, callables, cycles and non-finite numbers in an explicit declaration fail.

## Zones

ZoneLayout owns geography, ordering, overlap policy and membership Model. Rectangles
and spherical caps retain longitude, antimeridian and pole semantics. Membership
answers location; Planner decides service. Service identity is `(network_scope, zone_id)`.
Scopes are caller-supplied Model policy, not permanent physical isolation. The Five Shells
example reserves per-shell GS slots and scoped Zone destinations without cross-shell transit.

Structured routing remains a planning-interface value: dispositions, local exits, candidates and immutable policy records have no BPF dependency. The BPF-specific DataPlane owns schema/layout generation, encoding and scoped object selection; Compiler preserves the complete logical bindings. Development scripts are opt-in clients of normal calculation/validation. Observation remains read-only under `tools/observation`; `tools/generation/traffic` owns schedules and sending through experiment orchestration. Runner retains its independent optional BPF telemetry recorder.
