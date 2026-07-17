# Isolated Satellite Static Emulator Slice

## 1. Status and scope

This document records the implemented design of the first Skyseed v2
functional vertical slice. It contains only static, isolated Satellites.
GroundStation, UT, Simulator, dynamic position, visibility, links, routing,
Zone, Topology, planning, and execution backends remain outside this slice.

The implementation targets the locally installed editable SEED package at
`/home/seed/seed-emulator-upstream-dev`, package version `0.0.7`, commit
`61acdf4c9699b25b06843ec4aad747108b05924e`.

## 2. One Satellite object and one restricted contract

The central invariant is:

```text
Satellite(Node) -> SatelliteDescriptorProjector -> SatelliteDescriptor
```

Their correspondence and dependency direction are:

```mermaid
flowchart LR
    Satellite[Satellite / satnode] -->|approved scalar values| Projector[SatelliteDescriptorProjector]
    Projector --> Descriptor[Immutable SatelliteDescriptor]
    Satellite --|> SeedNode[SEED Node]
    Satellite -->|supplies stable identity| RuntimeRef[RuntimeNodeReference]
    RuntimeRef --> Descriptor
```

- `Satellite` is defined in `src/skyseed_v2/domain/satellite.py`. It directly
  inherits SEED `Node` and is the only object representing one Satellite.
  It owns fixed orbit and payload values while reusing Node software, file,
  command, environment, Registry, configure and Compiler capabilities.
- `SatelliteDescriptor` and `RuntimeNodeReference` are frozen scalar value
  objects in `src/skyseed_v2/contracts/satellite.py`. They contain no SEED,
  Registry, Docker, file, software, command, or mutable runtime object.
- `SatelliteDescriptorProjector` is the application projection service in
  `src/skyseed_v2/projection.py`. It reads only approved scalar values and the
  stable runtime reference from Satellite and produces the restricted view.

The runtime reference is the immutable triple `(asn, registry_type, node_name)`
owned by the Satellite identity and is rendered as
`65000/satnode/satellite-0` in the example. It is a stable runtime-node
reference in the Emulator–Simulator contract, not a SEED `Node`. Its
`registry_type` currently maps the SEED integration identity. Whether that
field remains in the long-term contract is deferred to later contract design.

The projector accepts only the unique Satellite rather than a second runtime
reference input. Satellite already owns the approved stable ASN, registry type
and node name; passing another copy would recreate an avoidable identity
mismatch. The projector remains outside the Compiler, and Simulator receives
only the immutable Descriptor rather than the complete Satellite/Node.

## 3. Static domain hierarchy

The implemented aggregate is:

```text
Shell
└── Orbit
    └── Satellite(Node)
```

### Shell

`Shell` owns its Orbit instances. `create_orbit()` is the only attachment API,
so an existing Orbit cannot be re-parented. Orbit IDs must be unique within a
Shell.

### Orbit

`Orbit` owns its Satellite instances and fixed `OrbitParameters`:

- altitude in kilometres;
- inclination in degrees;
- right ascension in degrees.

Each Satellite adds a non-negative orbit index and fixed mean anomaly. Orbit
indices are unique within an Orbit. Satellite IDs share one reservation set
across all Shells in a `SatelliteAutonomousSystem`, so a stable ID cannot be
reused in another Orbit or Shell. Batch creation validates the complete batch
before committing it.

### Satellite

`Satellite` exposes its fixed ownership, orbit and payload values through
read-only properties; payload is a copied, read-only string mapping. It starts
with zero interfaces and does not own dynamic position, visibility,
connection, routing, Topology, planning or execution state. Node mutation APIs
remain available for legitimate container software, files, commands and
environment configuration.

## 4. SEED integration lifecycle

`SatelliteAutonomousSystem` extends SEED `AutonomousSystem` using the same
general pattern as the investigated SCION extension:

1. it owns Shells, while each Shell owns Orbits and each Orbit owns the unique
   Satellite instances;
2. `registerNodes()` registers those same instances as
   `(asn, "satnode", satellite_id)`;
3. `inheritOptions()` applies AS options directly to those Satellites;
4. `configure()` configures those same Satellites after upstream AS nodes;
5. Shell, Orbit and Descriptor never enter Registry.

There is no second node collection, binding map, materialization pass or
separate node query API. `Orbit.create_satellite()` and
`create_satellites()` return the exact objects later registered, configured
and compiled.

`SatelliteBase` extends the two fixed-type upstream hooks:

- `_prepare()` supplies Base global options to `satnode` objects;
- `render()` supplies the normal `/ifinfo.txt` and `/interface_setup` files.

For an isolated Satellite, `/ifinfo.txt` is intentionally empty.

## 5. First-class Satellite node identity

The integration constants are:

- Registry type: `satnode`;
- role object: `SatelliteRole.SATELLITE`;
- visualization role: `Satellite`;
- container role prefix: `sat`.

`SatelliteRole` is distinct from upstream `NodeRole.Host`; no Satellite is
registered as `hnode`. `Satellite.scope()` uses upstream `ScopeType.ANY`
because SEED 0.0.7 has no public ScopeType extension registry. This retains
global/AS/node option precedence without claiming Host or Router identity.

Users configure node-side behavior directly through inherited SEED Node APIs.
This slice does not duplicate those APIs with wrappers and implements no
payload service or dynamic network behavior.

## 6. Docker Compiler contract

`SatelliteDockerCompiler` is a formal subclass of the upstream Docker
Compiler. It retains the inherited image selection, Dockerfile generation,
software cache tiers, Node options, labels, custom services, and Compose
pipeline, while adding these contracts:

1. `satnode` participates in software grouping and node dispatch;
2. an empty interface list is valid for the dedicated `Satellite` type;
3. zero-interface naming never indexes `interfaces[0]`;
4. the service contains `network_mode: none` and no service `networks` field;
5. an empty upstream top-level `networks: null` is removed rather than filled
   with a compatibility network;
6. metadata emits role `Satellite`, type `satnode`, and Skyseed stable IDs;
7. every metadata label key is serialized at most once.

All Satellite-specific recognition, software grouping, naming, metadata and
zero-interface service generation are concentrated in the internal
`_SatelliteCompilerBoundary`. The top-level Compiler identifies a
`satnode`, delegates to that boundary, appends its service, and then invokes
SEED's inherited compile pipeline. There is no handler registry, placeholder
for future node types, or parallel Compose generator.

Upstream Docker 0.0.7 exposes no additional-node dispatch hook. The subclass
therefore uses `_append_upstream_service()` as the sole access point to
Docker's private `_Docker__services` accumulator. The point validates that
the member exists and is a string, and fails explicitly if the upstream
contract drifts. It has no fallback path. This is technical debt caused by
the missing public custom-node dispatch hook and must be revisited if SEED
adds such a hook.

No independent Docker/Compose generator exists in Skyseed.

## 7. Explicit no-network semantics

The environment probe and formal runtime both use:

```yaml
network_mode: none
```

This is not the project default network. Docker inspect reports
`HostConfig.NetworkMode=none`, and the container exposes only `lo`.

Docker 19.03 represents this mode in raw inspect data as a pseudo network named
`none`. The stock InternetMap frontend treats every raw network entry as an
edge, even though SEED metadata correctly has `nets=[]`. The generated
InternetMap image therefore applies one generic adapter at its backend API
boundary: when `HostConfig.NetworkMode == "none"`, it normalizes the returned
`NetworkSettings.Networks` to `{}`. This does not create, remove, or reconnect
any Docker network; it prevents a Docker representation detail from becoming
a false topology edge.

The adapter is pinned to `handsonsecurity/seedemu-internetmap:2.0`, generated
under `skyseed-internet-map/`, and fails its image build unless exactly one
known patch point is present in both compiled and source backend files.

## 8. Metadata projection

Each Satellite service includes SEED metadata:

- `asn`;
- `nodename`;
- `displayname`;
- `role=Satellite`;
- `type=satnode`.

It also includes:

- `skyseed.entity_type=Satellite`;
- `skyseed.satellite_id`;
- `skyseed.shell_id`;
- `skyseed.orbit_id`;
- `skyseed.runtime_node_ref`.

No `net.*` labels are fabricated. Complete orbit parameters remain in the
domain Satellite and SatelliteDescriptor rather than being packed into
Compose labels. The Compiler never reconstructs a domain object from labels.

The Satellite compiler boundary obtains the upstream metadata once, folds it
into a mapping keyed by the complete label name, then explicitly replaces the
stable `asn`, `nodename`, `role`, `type`, and `skyseed.*` values. Serialization
happens only after this keyed normalization, so an upstream or node-provided
collision cannot create duplicate YAML keys or retain a Host identity.

## 9. Minimal executable scenario

`examples/isolated_satellites.py` constructs one AS, one Shell, one Orbit, and
two Satellites through the owning objects. See `examples/README.md` for compile,
run, inspection, visualization, and cleanup commands.

## 10. Deferred work

At completion time this slice deliberately did not establish a GroundStation
model, ground network, Satellite interface, link, route, dynamic location, or
Simulator. The later GroundStation slice is documented separately in
`static-ground-station-emulator.md`; it does not change this Satellite slice's
isolation contract.
