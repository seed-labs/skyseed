# Current Design State

## Current phase

Static Emulator core excluding UT: COMPLETE
Generic static scenario loading: COMPLETE
Examples self-contained and reorganized: COMPLETE
Large static network compile validation: PASS
Compose v2 migration: COMPLETE
Compose v2 materialization tooling: COMPLETE
Fixed batching: optional tooling mode

Migration baseline: READY

Simulator: NOT STARTED

## Current objective

Review the self-contained Emulator examples and native-Linux migration
baseline. Await explicit approval before selecting another objective.

## Implementation status

Complete for versioned v2 static scenario loading, reorganized self-contained
examples, large static compile/config validation and Docker Compose v2
materialization tooling.

## Current workflow step

The large static example is now driven only by example-local v2 data and is
validated through object construction, Catalog, Compiler and Compose config.
This closeout deliberately does not repeat the 365-service build/up. A minimal
complete example supplies the current runtime regression. Do not start dynamic
Satellite networking, Runner design or Simulator work automatically.

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
- `SimulationEntityCatalog` is the immutable, deterministic Emulator output boundary containing only Satellite/GroundStation Descriptors keyed by `SimulationNodeId`.
- `SimulationEntityCatalogBuilder` supports mixed, Satellite-only, GroundStation-only and multiple Skyseed AS inputs, ignores ordinary SEED AS objects, delegates to the existing Projectors and never reads Registry or Compiler output.
- `SkyseedAutonomousSystem` is the only public Skyseed AS type. One instance can own Satellites, GroundStations and inherited ordinary SEED objects, while multiple instances remain independent.
- `SkyseedDockerCompiler` is the only public Skyseed Docker Compiler. Satellite special handling remains in `_SatelliteCompilerBoundary`; GroundStation and ordinary nodes use the inherited SEED path.
- Shared identifier validation is owned by `domain.validation`; SEED metadata names are owned by `seed.metadata`.
- The formal `examples/complete_static_emulator/` experiment puts two isolated Satellites and one GroundStation in the same Skyseed AS, builds a three-node Catalog, gives GroundStation one IX interface, explicitly disables/masks GS-AS internal routing, establishes one eBGP session to an ordinary ground AS, and creates no GS–Satellite link.
- GroundStation uses the inherited standard Router Compiler path. Skyseed only replaces its metadata by key so InternetMap sees `GroundStation` with `type=rnode`; no GS-specific dispatch or private accumulator access was added.
- Runtime validation proved forwarding, established eBGP, packet-level ground reachability, one GS ground edge, and two unchanged Satellite vertices with zero Satellite edges.
- Automated and manual complete Emulator validation is recorded in `docs/evidence/complete-static-emulator-validation.md`. Its formal runtime resources and generated temporary output have been cleaned.
- The v1 S4 investigation is recorded in `docs/reference/v1/s4-static-scenario-investigation.md`. It treats v1 only as a historical static-fact source and does not make S4 a permanent Emulator default.
- Immutable backend-independent specifications and application builders now construct arbitrary positive static Satellite populations, multiple Shells and GroundStations without a Constellation entity or S4-specific domain API.
- Generated Orbit/Satellite IDs are derived from v2 Shell hierarchy and ordinal position; generated GS IDs are stable hashes of explicit semantic identity keys. They are deterministic, order-independent and do not preserve v1 IDs.
- The v1 JSON-shape adapter is isolated under `examples/reference/`, immediately returns v2 specifications and is excluded from the wheel. The core package contains no v1 import or file-shape dependency.
- The reference adapter pins `ad331b9c15e3a5fb522af9ff18a06434f99c0860` and fail-closed verifies ancestry plus the exact Git blobs of its three selected files before loading. The investigation commit `06b19eb718a79a9113ee95bc045f10953c1d8ab7` is its ancestor and has the same three blobs and key S4 inputs.
- The formal `examples/large_static_network/` experiment reads only adjacent v2 YAML and builds one Shell, six Orbits, 348 isolated Satellites, 12 descriptive `operator=Starlink` GroundStations, two ordinary ground ASes, two IXes and two explicit eBGP peerings. It has no runtime v1 input or permanent S4 semantic.
- The scale Catalog contains 360 Satellite/GS Descriptors; the compiled Compose contains 365 services. Runtime validation proved 348 no-network Satellite containers, 12 one-IX GS, two learned external prefixes, packet reachability and 348 zero-edge Satellite InternetMap vertices.
- `ComposeProjectLauncher` is a small integration-layer materialization tool outside the Emulator, Descriptor/Catalog, Compiler and Simulator domains. It consumes one existing Compose output/project, an explicit long-running/helper partition and configurable Compose v2 parallel limit. Its default issues one full-project `docker compose up`, then fail-closed verifies the final complete inventory without recompiling or rewriting Compose.
- Legacy `docker-compose` is not a Skyseed success path. The launcher requires Compose v2 and never falls back. Fixed batching remains available only through explicit `strategy=batched`, with configurable batch size, for compatibility and diagnosis.
- Formal revalidation used Compose v2.40.2 and `parallel_limit=16`: one `up` materialized all 365 services, ending with 363 running services and two helpers exited zero. Full v2 evidence is recorded in `docs/evidence/s4-scale-compose-v2-single-materialization-validation.md`; the v1 fixed-batch run remains historical evidence in `docs/evidence/s4-scale-batched-compose-materialization-validation.md`.
- Docker Engine 19.03 requires the documented `DOCKER_BUILDKIT=0` build compatibility setting on this host; the launcher itself does not build or select a builder. This introduced no second Compiler output or scene change.
- No epoch, position computation, dynamic link, RuntimeNodeReference or runtime resource locator was introduced. Dynamic Satellite-network reproduction remains unstarted.

## Open design questions

- Future GroundStation dynamic satellite-side interface and service boundaries; the static Router inheritance result does not pre-authorize them.
- Read-only contracts for entity kinds beyond Satellite and GroundStation.
- Whether a future SEED public node-dispatch hook can replace the current narrow private Compiler integration point.
- Whether a future SEED public custom-Router insertion hook can replace the current narrow GroundStation AS lifecycle extension.
- How a future Backend Planner, Execution Compiler or Runner maps `SimulationNodeId` to actual SEED, container, namespace or kernel resources; this is future design scope, not current technical debt.
- The next vertical slice, which requires explicit design approval.

## Next single objective

No next slice is authorized. Review the completed static scale evidence before
explicitly choosing any dynamic Satellite-network or Simulator objective.

## Acceptance state

- Satellite slice: completed
- GroundStation slice: completed
- Unified AS and identity contracts: completed
- SimulationEntityCatalog: implemented
- Automated Emulator integration: PASS
- Manual InternetMap visual acceptance: PASS
- S4 static-scenario investigation: completed
- Generic static scenario loading: completed
- Examples self-contained and reorganized: completed
- Large static network compile validation: PASS
- Compose v2 migration: complete
- Compose v2 materialization tooling: complete
- Fixed batching: optional tooling mode
- Static ground-network integration at scale: PASS
- Simulator: not started
- UT: outside current Emulator core
- Migration baseline: READY

Static Emulator core excluding UT: COMPLETE. Formal examples no longer have a
runtime v1 reference dependency; native-Linux migration can start from the
baseline recorded in `docs/migration/native-linux-baseline.md`. Current
validation evidence is in
`docs/evidence/emulator-self-contained-migration-validation.md`.
