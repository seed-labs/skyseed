# Current Design State

## Current phase

Second functional vertical slice implemented and validated.

## Current workflow step

Review the static GroundStation and minimal ground-network slice. Do not start another slice automatically.

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
- `SatelliteDescriptorProjector` reads approved static values and the stable runtime reference from that unique Satellite.
- Satellite is a first-class `satnode` with role `Satellite`; it is never registered or labelled as Host.
- Satellite containers use explicit `network_mode: none` and expose only loopback.
- Satellite-specific Docker compilation is concentrated in one internal boundary; the private SEED service accumulator has one fail-closed access point.
- Satellite metadata is normalized by complete key before serialization, so identity labels are unique.
- Runtime InternetMap validation found two Satellite vertices and zero edges.
- `GroundStation` is the one user-visible GS object, directly inherits SEED `Router`, and is the same instance returned by its AS, registered as `rnode`/border router, compiled and run.
- GroundStation static fields and immutable `GroundStationDescriptor` projection are implemented; no complete Router crosses the future Simulator contract.
- The formal example gives GroundStation one IX interface, disables GS iBGP and masks OSPF, establishes one explicit eBGP session to an ordinary ground AS, and creates no GS–Satellite link.
- GroundStation uses the inherited standard Router Compiler path. Skyseed only replaces its metadata by key so InternetMap sees `GroundStation` with `type=rnode`; no GS-specific dispatch or private accumulator access was added.
- Runtime validation proved forwarding, established eBGP, packet-level ground reachability, one GS ground edge, and two unchanged Satellite vertices with zero Satellite edges.

## Open design questions

- Future GroundStation dynamic satellite-side interface and service boundaries; the static Router inheritance result does not pre-authorize them.
- The read-only contracts for entities other than Satellite.
- Whether a future SEED public node-dispatch hook can replace the current narrow private Compiler integration point.
- The next vertical slice, which requires explicit design approval.

## Next single objective

Review and accept or revise the static GroundStation slice and its runtime evidence.
