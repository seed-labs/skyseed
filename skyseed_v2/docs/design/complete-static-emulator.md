# Complete Static Emulator Catalog and Integration

## 1. Status and scope

This slice combines the completed Satellite, GroundStation, unified AS and
logical identity contracts into the formal static Emulator output boundary.
`SimulationEntityCatalog` and its Builder are implemented, and automated
Emulator integration acceptance is complete. Manual InternetMap visual
acceptance remains pending.

The slice adds no node kind, Simulator, UT, GS–Satellite link, dynamic
interface, topology/planning system, execution-resource resolver or Catalog
persistence format.

## 2. Immutable Catalog contract

`SimulationEntityCatalog` is defined in
`src/skyseed_v2/contracts/catalog.py`. It contains only frozen
`SatelliteDescriptor` and `GroundStationDescriptor` values plus a read-only
index keyed by `SimulationNodeId`.

Construction snapshots and sorts all Descriptors by `node_id.value`. Every
complete logical ID is globally unique; any duplicate fails before a Catalog
is returned, including two equal Descriptors or two different Descriptors with
the same node ID. No input order can silently select a winner.

The public query surface is:

- `get(node_id)`;
- `get_satellite(node_id_or_id)`;
- `get_ground_station(node_id_or_id)`;
- `list_satellites()`;
- `list_ground_stations()`;
- `list_nodes()`.

List methods return immutable tuples in deterministic logical-ID order. A full
`SimulationNodeId` is authoritative. A string entity ID is accepted only when
it identifies exactly one Descriptor of the requested kind; cross-AS
ambiguity fails explicitly.

The Catalog contains no domain entity, SEED Node/Router/Registry, AS, Compiler,
Docker/Compose object, namespace, container name, Registry kind or execution
resource locator.

## 3. Builder boundary

`SimulationEntityCatalogBuilder` is the independent application service in
`src/skyseed_v2/catalog_builder.py`. It accepts one AS or an iterable of AS
objects, ignores ordinary SEED AS instances, and traverses only
`SkyseedAutonomousSystem.satellites()` and `.ground_stations()`.

It delegates projection to the existing `SatelliteDescriptorProjector` and
`GroundStationDescriptorProjector`; it does not duplicate projection logic.
The Builder never reads Registry, Compiler output or Compose. Building before
render is therefore valid, and later mutation of the source AS cannot alter an
already-built Catalog snapshot.

Multiple mixed, Satellite-only and GS-only AS instances are supported.
Different ASNs may reuse an entity ID, and Satellite/GroundStation may reuse a
text ID in the same ASN, because the full logical ID remains distinct.

## 4. Formal complete example

`examples/complete_static_emulator.py` builds:

```text
Skyseed AS 65001
├── shell-1 / orbit-1
│   ├── satellite-0 (isolated)
│   └── satellite-1 (isolated)
└── gs-new-york ── ix100
                     │ explicit eBGP
ordinary AS 65002    │
└── ground-router ───┘
    └── ground-net 10.202.0.0/24
```

Before rendering, the example builds and prints a deterministic Catalog
summary containing two Satellite Descriptors, one GroundStation Descriptor
and three unique logical node IDs. It then renders and compiles through
`SkyseedDockerCompiler` with runtime InternetMap enabled. The printed summary
is human-readable example output, not a serialization contract.

## 5. Identity and runtime separation

Catalog logical IDs correspond to the same user entities registered and
compiled by the Emulator, but they do not expose Registry identity. SEED still
uses `satnode`, `rnode` and `brdnode`; running InternetMap still uses role/type
labels. Neither layer is copied into the Catalog.

The two Satellites remain `network_mode: none`. GroundStation remains on the
standard Router path with only its real IX interface, forwarding and explicit
eBGP. No fake edge or compatibility network is added for visualization.

## 6. Deferred work

Catalog persistence, Simulator loading, Zone, Topology, Planner, Runner, UT,
dynamic links and logical-ID-to-execution-resource resolution remain outside
this slice. A future Emulator facade may delegate to the Builder, but no giant
facade or placeholder API is introduced here.
