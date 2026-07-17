# Current Design State

## Current phase

GroundStation post-slice structural convergence implemented and validated.

## Current workflow step

Review the unified Skyseed AS, public contract boundaries and combined runtime evidence. Do not start another slice automatically.

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
- `SatelliteDescriptorProjector` reads approved static values and constructs the stable runtime reference from that unique Satellite's stable scalars.
- Satellite is a first-class `satnode` with role `Satellite`; it is never registered or labelled as Host.
- Satellite containers use explicit `network_mode: none` and expose only loopback.
- Satellite-specific Docker compilation is concentrated in one internal boundary; the private SEED service accumulator has one fail-closed access point.
- Satellite metadata is normalized by complete key before serialization, so identity labels are unique.
- Runtime InternetMap validation found two Satellite vertices and zero edges.
- `GroundStation` is the one user-visible GS object, directly inherits SEED `Router`, and is the same instance returned by its AS, registered as `rnode`/border router, compiled and run.
- GroundStation static fields and immutable `GroundStationDescriptor` projection are implemented; no complete Router crosses the future Simulator contract.
- `RuntimeNodeReference` is a shared immutable contract in `contracts.runtime`; both Satellite and GroundStation Descriptors contain that same type, and neither entity-specific contract module depends on the other.
- Satellite and GroundStation domain entities do not import contracts, store a RuntimeNodeReference or expose `runtime_reference`; their Projectors own contract construction.
- `SkyseedAutonomousSystem` is the only public Skyseed AS type. One instance can own Satellites, GroundStations and inherited ordinary SEED objects, while multiple instances remain independent.
- `SkyseedDockerCompiler` is the only public Skyseed Docker Compiler. Satellite special handling remains in `_SatelliteCompilerBoundary`; GroundStation and ordinary nodes use the inherited SEED path.
- Shared identifier validation is owned by `domain.validation`; SEED metadata names and runtime-reference formatting are owned by `seed.metadata`.
- The formal example puts two isolated Satellites and one GroundStation in the same Skyseed AS, gives GroundStation one IX interface, explicitly disables/masks GS-AS internal routing, establishes one eBGP session to an ordinary ground AS, and creates no GS–Satellite link.
- GroundStation uses the inherited standard Router Compiler path. Skyseed only replaces its metadata by key so InternetMap sees `GroundStation` with `type=rnode`; no GS-specific dispatch or private accumulator access was added.
- Runtime validation proved forwarding, established eBGP, packet-level ground reachability, one GS ground edge, and two unchanged Satellite vertices with zero Satellite edges.
- Structural convergence combined validation is recorded in `docs/evidence/skyseed-structure-convergence-validation.md`; it also proves all owned runtime resources were removed.

## Open design questions

- Future GroundStation dynamic satellite-side interface and service boundaries; the static Router inheritance result does not pre-authorize them.
- The read-only contracts for entities other than Satellite.
- Whether a future SEED public node-dispatch hook can replace the current narrow private Compiler integration point.
- Whether the contract's `registry_type` should remain a long-term runtime-node locator field.
- The next vertical slice, which requires explicit design approval.

## Next single objective

Review and accept or revise the structural convergence and combined runtime evidence.
