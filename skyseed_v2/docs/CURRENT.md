# Current Design State

## Current phase

Emulator–Simulator logical node identity contract convergence implemented and validated.

## Current workflow step

Review the bounded logical identity change and its evidence. Do not start another slice automatically.

## Confirmed direction

- The system is divided into Emulator and Simulator.
- Emulator owns static entities, SEED integration and container-related configuration.
- Simulator owns time-dependent topology and execution planning.
- The Emulator exposes only a read-only view containing information required by the Simulator.
- The Emulator–Simulator boundary is a domain contract, not a specific file format.
- Topology is a subsystem coordinator rather than a giant implementation class.
- Planning is separated into backend-independent semantic planning and backend-specific planning.
- Compiler lowers an approved backend plan into executable content.
- Runner executes approved content and emits structured observations.
- UT is temporarily outside the current main slice.
- v1 is referenced only when a current design problem requires a focused investigation.
- The first slice implements `Shell -> Orbit -> Satellite` as a static domain hierarchy.
- `Satellite` is the one Skyseed Satellite object, directly inherits SEED `Node`, and is the same instance created by Orbit, registered by AS, and handled by Compiler.
- Immutable `SatelliteDescriptor` is the restricted Emulator–Simulator view; Simulator never receives the complete Satellite/Node.
- `SatelliteDescriptorProjector` reads approved static values and constructs a stable logical `SimulationNodeId` from that unique Satellite's ASN and entity ID.
- Satellite is a first-class `satnode` with role `Satellite`; it is never registered or labelled as Host.
- Satellite containers use explicit `network_mode: none` and expose only loopback.
- Satellite-specific Docker compilation is concentrated in one internal boundary; the private SEED service accumulator has one fail-closed access point.
- Satellite metadata is normalized by complete key before serialization, so identity labels are unique.
- Runtime InternetMap validation found two Satellite vertices and zero edges.
- `GroundStation` is the one user-visible GS object, directly inherits SEED `Router`, and is the same instance returned by its AS, registered as `rnode`/border router, compiled and run.
- GroundStation static fields and immutable `GroundStationDescriptor` projection are implemented; no complete Router crosses the future Simulator contract.
- `SimulationNodeId` is a shared immutable logical identity in `contracts.identity`, with `asn`, `entity_kind` and `entity_id`; both Descriptors contain that same type.
- Satellite and GroundStation domain entities do not import contracts or construct logical IDs; their single-input Projectors own contract construction.
- Simulator-facing contracts contain no SEED Registry identity, container, namespace, Compose service or execution target. Satellite and GroundStation IDs remain consistent with their Descriptor node IDs.
- `SkyseedAutonomousSystem` is the only public Skyseed AS type. One instance can own Satellites, GroundStations and inherited ordinary SEED objects, while multiple instances remain independent.
- `SkyseedDockerCompiler` is the only public Skyseed Docker Compiler. Satellite special handling remains in `_SatelliteCompilerBoundary`; GroundStation and ordinary nodes use the inherited SEED path.
- Shared identifier validation is owned by `domain.validation`; SEED metadata names are owned by `seed.metadata`.
- The formal example puts two isolated Satellites and one GroundStation in the same Skyseed AS, gives GroundStation one IX interface, explicitly disables/masks GS-AS internal routing, establishes one eBGP session to an ordinary ground AS, and creates no GS–Satellite link.
- GroundStation uses the inherited standard Router Compiler path. Skyseed only replaces its metadata by key so InternetMap sees `GroundStation` with `type=rnode`; no GS-specific dispatch or private accumulator access was added.
- Runtime validation proved forwarding, established eBGP, packet-level ground reachability, one GS ground edge, and two unchanged Satellite vertices with zero Satellite edges.
- Structural convergence combined validation is recorded in `docs/evidence/skyseed-structure-convergence-validation.md`; it also proves all owned runtime resources were removed.

## Open design questions

- Future GroundStation dynamic satellite-side interface and service boundaries; the static Router inheritance result does not pre-authorize them.
- Read-only contracts for entity kinds beyond Satellite and GroundStation.
- Whether a future SEED public node-dispatch hook can replace the current narrow private Compiler integration point.
- Whether a future SEED public custom-Router insertion hook can replace the current narrow GroundStation AS lifecycle extension.
- How a future Backend Planner, Execution Compiler or Runner maps `SimulationNodeId` to actual SEED, container, namespace or kernel resources; this is future design scope, not current technical debt.
- The next vertical slice, which requires explicit design approval.

## Next single objective

Review and accept or revise the logical identity contract convergence. Catalog, Simulator and runtime-resource resolution remain unstarted.
