# Generic Static Scenario Construction

## Scope

The static Emulator accepts versioned, v2-owned scenario data and constructs
only already-approved static entities. The formal path is:

```text
example-local v2 YAML
        -> StaticEmulatorScenarioLoader
        -> immutable generic specifications
        -> StaticEmulatorScenarioBuilder
        -> SkyseedAutonomousSystem -> Shell -> Orbit -> Satellite
                                 \-> GroundStation
        -> SimulationEntityCatalog
        -> SkyseedDockerCompiler
```

No formal example imports reference tooling or reads `skyseed_v1`. Historical
v1 adapters remain under `examples/reference/` only for offline investigation
or one-way conversion; they are not a supported scenario format or success
path and are excluded from the wheel.

## Versioned v2 scenario contract

`StaticEmulatorScenarioLoader` reads `skyseed-static-emulator/v1` YAML. A
scenario contains Skyseed AS, Shell generation or explicit Orbit/Satellite
specifications, optional GroundStations, ordinary AS/Network/Router values,
IX attachments and explicit eBGP peerings. A scenario may explicitly reference
an example-local `skyseed-ground-stations/v1` file.

The loader rejects unknown versions and fields, missing fields, duplicate YAML
keys, invalid identifiers/ASN/angles/coordinates/prefixes, duplicate logical
IDs, unresolved IX/Router/network references and external ground-station paths.
It normalizes collection ordering before returning frozen specifications.

The contract deliberately excludes v1 commits and labels, epoch/time, current
position, ISL, GS–Satellite links, Zone, dynamic routes, qdisc/BPF/MPLS,
container/Compose names and runtime locators. `operator` is descriptive only;
it does not select ASN, node type, routing or Compiler behavior.

## Application builders and approved ownership

`ShellPopulationBuilder` is a deterministic application helper that expands
one or more `ShellSpecification` values through the owning
`SkyseedAutonomousSystem`. It returns the Shell, Orbit and Satellite objects
created through the existing ownership APIs. It is not a domain entity and
does not introduce a parent above Shell.

The approved hierarchy remains:

```text
SkyseedAutonomousSystem
└── Shell
    └── Orbit
        └── Satellite
```

Several Shells are only a collection owned by an AS or scenario. They do not
form a `Constellation` entity, identity, Descriptor, Catalog entry or Simulator
contract. `GroundStationBuilder` has the equivalent narrow application role
for GroundStations. `StaticEmulatorScenarioBuilder` coordinates those helpers,
ordinary SEED ground objects and approved Layers, then delegates Catalog
projection to the existing Catalog builder.

Generated IDs are deterministic and independent of input mapping/order. No
builder contains the large example's counts or branches on a historical S4
label.

## Domain vocabulary discipline

Before a new noun enters public API, contracts, a Descriptor, Catalog, domain
class diagram or Simulator input, its design must explicitly establish:

1. the real domain concept it represents;
2. its relationship to existing objects;
3. its long-term consumer;
4. whether it is only a helper, builder result or application service;
5. the alternative that avoids introducing it; and
6. explicit user approval.

Implementation convenience does not promote a name into the domain model. No
approval framework or placeholder class is implied by this documentation
rule.

## Self-contained formal examples

Each formal experiment owns its `main.py`, versioned data, README, thin
`start.sh`, scoped `cleanup.sh` and ignored `output/` directory. The large
static network owns exactly the 12 GroundStation records it needs; it does not
copy the historical 250-item source catalog.

`large_static_network` currently describes one Shell, six Orbits, 348 isolated
Satellites, 12 GroundStations, two ordinary ground AS, two IX and two explicit
eBGP peerings. Those are example data values, not framework defaults or a
claim that dynamic v1 behavior has been reproduced.

## Materialization boundary

The examples compile one normal Skyseed Compose output. The existing
`ComposeProjectLauncher` remains an integration helper for one already-built
project, with Compose v2 single invocation as default and explicit fixed
batching as optional tooling. It is not extended in this slice.

These tools may later be used underneath `Runner.initialize()`. They are not a
separate top-level architecture, Simulator, or current Runner, and provide no
automatic recovery, reconciliation, dynamic tuning or restart policy.
