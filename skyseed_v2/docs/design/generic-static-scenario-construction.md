# Generic Static Scenario Construction

## Scope

This slice adds generic static construction for constellations and ground
stations, then exercises it with an S4-scale container experiment. S4 is only a
scale and input-data label. It is not a default scenario, compatibility mode,
domain API, or claim that the dynamic v1 experiment has been reproduced.

The implemented path is:

```text
reference-only external data adapter
        -> immutable v2 specifications
        -> application builders
        -> current Shell / Orbit / Satellite / GroundStation APIs
        -> SimulationEntityCatalog and SkyseedDockerCompiler
```

No core package module imports v1, understands a v1 JSON shape, or stores a v1
path. The executable reference adapter lives under `examples/reference/` and is
excluded from the wheel.

## Immutable specifications

`skyseed_v2.specifications` is backend-independent and contains no SEED import.
It defines:

- `ShellSpecification`, using exactly one of a generic generation rule or
  explicit `OrbitSpecification` values;
- `OrbitGenerationSpecification`, `RaanDistributionSpecification` and
  `InOrbitPhaseDistributionSpecification`;
- explicit `OrbitSpecification` and `SatelliteSpecification` values;
- `GroundStationSpecification`, with either an explicit v2 ID or a stable
  identity-generation key.

The specifications contain only static Emulator data. They do not contain an
epoch, simulation start time, position, visibility, link, Zone, topology slice,
resource locator, runtime node reference, UT, BPF, MPLS or qdisc field.

## Builders and identity

`ConstellationBuilder` and `GroundStationBuilder` are application services. The
domain entities remain responsible only for their own invariants and SEED node
capabilities; the Compiler does not construct domain objects.

Generated constellation IDs use the v2 rule:

```text
<shell-id>-orbit-<one-based orbit number, zero padded>
<orbit-id>-satellite-<one-based satellite number, zero padded>
```

A generated GroundStation ID is `ground-station-` plus the first 20 hexadecimal
digits of SHA-256 over its explicit identity key. The experiment adapter builds
that key from normalized operator, display name, location and elevation values,
not from list position. Explicit IDs remain supported by the general
specification.

The builders snapshot and sort specifications, preflight every candidate ID,
and mutate the owning `SkyseedAutonomousSystem` only after validation. The same
input therefore produces the same entity IDs independently of iterable order.
The same rules work for arbitrary positive sizes, multiple Shells and repeated
use with independent Skyseed AS instances. They do not contain the numbers 348
or 12 and do not branch on S4.

## Reference adapter

`examples/reference/v1_static_input_adapter.py` is experiment tooling. It reads
the committed S1-S5 orbit catalog and selected gateway files, validates their
historical shapes, and immediately translates them into v2 specifications.

For the current experiment it derives one 6-orbit, 58-satellite-per-orbit Shell
with 560 km altitude, 97.6 degree inclination, uniform 60 degree RAAN spacing,
uniform in-orbit spacing and the alternating plane phase offset. It selects 12
catalog GroundStations and sets the descriptive operator to `Starlink`.

The adapter intentionally discards historical node IDs, epoch values, service
`/32` values, IX/upstream bindings and every dynamic field. Its output is not a
new supported file format.

## Scale experiment ground network

`examples/s4_scale_static_emulator.py` creates:

- one generic Shell, six Orbits and 348 isolated Satellites in AS65000;
- 12 GroundStations in the same Skyseed AS;
- two real SEED IX networks, with six GS memberships on each IX;
- ordinary AS65101 and AS65102, each with one Router and one explicit service
  network;
- one explicitly router-bound eBGP session per IX.

Only one selected GS on each IX has an eBGP session. The other ten GS retain
only their explicitly declared IX membership; they receive no dummy,
management, satellite-side or historical service network. OSPF and iBGP are
masked for AS65000. The ordinary ground Routers continue through the unmodified
SEED Router path.

The two test prefixes are `10.210.0.0/24` and `10.220.0.0/24`. They are new,
clear ground test networks and are unrelated to the ambiguous historical
service `/32` values.

## What successful validation proves

The scale run can prove that current v2 constructs, catalogs, compiles and
materializes hundreds of isolated Satellite containers together with multiple
GroundStations and a small real static ground Internet. Runtime checks cover
container state, NetworkMode, interfaces, BGP, learned prefixes, ICMP and
InternetMap graph facts.

It does not prove dynamic orbit propagation, time advancement, ISL,
GS-Satellite connectivity, Zone/G/Z routing, UT behavior, Simulator planning or
v1 experiment equivalence. None of those capabilities is implemented by this
slice.
