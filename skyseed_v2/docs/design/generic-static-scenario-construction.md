# Generic Static Scenario Construction

## Scope

The large static Emulator example accepts versioned, v2-owned scenario data
and constructs only already-approved static entities. Its file path is:

```text
example-local v2 YAML
        -> StaticEmulatorScenarioLoader
        -> immutable generic specifications
        -> StaticEmulatorSpecificationApplier.apply
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
It also rejects generated angular distributions that would create duplicate
Orbits or Satellites, and normalizes collection ordering before returning
frozen specifications. The Applier therefore receives fully validated input
and does not repeat these checks.

The contract deliberately excludes v1 commits and labels, epoch/time, current
position, ISL, GS–Satellite links, Zone, dynamic routes, qdisc/BPF/MPLS,
container/Compose names and runtime locators. `operator` is descriptive only;
it does not select ASN, node type, routing or Compiler behavior.

## Specification application and approved ownership

`StaticEmulatorSpecificationApplier` is the one optional application service
for applying a complete immutable specification. It directly expands
`ShellSpecification` values and calls the public ownership methods; it does
not delegate entity creation through another builder/factory layer.

The approved hierarchy remains:

```text
SkyseedAutonomousSystem
└── Shell
    └── Orbit
        └── Satellite
```

Several Shells are only a collection owned by an AS. They do not form a
population or `Constellation` entity, identity, Descriptor, Catalog entry or
Simulator contract. The Applier invokes
`SkyseedAutonomousSystem.create_ground_station` directly for GroundStations,
uses ordinary SEED methods for ground topology, and delegates only read-only
Catalog projection to the existing Catalog builder.

Generated IDs are deterministic and independent of input mapping/order. The
Applier contains no large-example counts and no historical-label branch. Its
flat `StaticEmulatorApplyResult` is an operation result containing Emulator,
AS collections and Catalog; it owns no entity and enters no Descriptor or
Simulator contract. Both the Applier and result are public only from
`skyseed_v2.scenario_application`, not the root package.

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
rule. The normative names and prohibited ambiguous bare terms are maintained
in `docs/architecture/terminology.md`; the root `AGENTS.md` makes this approval
gate part of the long-term project workflow.

## Self-contained formal examples

Each formal experiment owns its `main.py`, README, thin `start.sh`, scoped
`cleanup.sh` and ignored `output/` directory. The isolated and complete
teaching examples are handwritten and use no YAML. The large static network
alone owns versioned scenario data, including exactly the 12 GroundStation
records it needs.

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
