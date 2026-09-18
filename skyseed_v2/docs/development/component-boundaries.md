# Component boundaries and input obligations

Use this reference to review dependency direction, ownership and the values passed
between components. For a first explanation of the system, read the
[architecture overview](../architecture/overview.md).

## Owners and dependencies

Emulator constructs the SEED scene using the required ExtensionNode-capable
[fork and tested revision](../user-guides/getting-started.md#python-and-the-required-seed-source). The external
[`create_simulator_static_input`](../../src/skyseed_v2/adapters/emulator_to_simulator.py)
adapter projects it into receiver-owned `SimulatorStaticInput`. Simulator owns
PhysicalWorld, its physical-link Model and Planner. Backend BPF owns Compiler,
execution-material codecs and Runner. Producer owns chronological calculation;
ExperimentController integrates the experiment. Deployer owns deployment and
SEED-generated static files. Deployment is not a time-dependent calculation stage.

Simulator never reads Emulator or SEED nodes. Runner receives sealed material paths,
never a Simulator, Producer or calculation callback. Tools consume detached facts;
only the experiment boundary may import explicitly enabled active Traffic.
Shared facilities remain authority-free values, geometry-independent identities,
recording and mechanical I/O. Root imports are inert selectable modules, not a new owner.

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
