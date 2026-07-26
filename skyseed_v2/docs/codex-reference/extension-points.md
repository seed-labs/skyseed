# Extension points

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

SEED integration, Zone membership, three Models, configuration loaders,
Compose launch options, and repository Tools.

## Owners and consumers

| Extension | Owner boundary | May customize | Must not own |
|---|---|---|---|
| `SatelliteBase` / `SkyseedAutonomousSystem` | `emulator/seedemu` | upstream Base/AS behavior for Skyseed entities | Simulator or Tools |
| `SkyseedDockerCompiler` | `emulator/seedemu` | SEED Docker dispatch, metadata, package asset use | dynamic execution |
| `ZoneMembershipModel` | ZoneLayout | physical matches and winner among matches | Zones or service assignment |
| `PositionModel` | Slicer | complete Position mapping | Slice publication |
| `ConnectionModel` | Slicer | complete ConnectionSlice | Route or publication |
| `RouteModel` | Slicer | complete RouteSlice | path validation/publication |
| YAML loader data | receiving configuration package | accepted schema values | domain runtime objects |
| Compose strategy | ComposeProjectLauncher | explicit single/batched project startup | compile/edit/fallback |
| Inspection projection/UI | repository `tools/` | non-authoritative display | formal computation |

## Contract

SEED extraction direction is:

```text
Skyseed construction/entities → emulator/seedemu → upstream SEED
```

The extraction blockers are explicit: Skyseed `Satellite` and
`GroundStation` are dual-role SEED subclasses, and compiler dispatch/type
metadata imports those Skyseed entity types/constants. The SEED extension
package itself has no dependency on Simulator, Tools, Compose launcher, or
examples. Assets are loaded through
`files("skyseed_v2.emulator.seedemu")/assets/...` and packaged by
`pyproject.toml`.

## Invariants

Extensions remain narrow, typed, owner-validated, and replace no formal owner.

## Failure semantics

Unsupported upstream API shape, foreign extension output, or missing asset
fails closed. There is no compatibility adapter to an older path.

## Prohibited behavior

No plugin discovery/framework, generic ToolRegistry, Simulator logic inside
SEED integration, Dynamic Slice semantics in a compiler hook, or example-only
branch.

## Extension points

New extension categories are public architecture decisions. Private helpers
inside an accepted extension are implementation detail.

## Maintenance

For SEED changes update package README, direct-import/consumer checks,
compiler integration tests, package-data/wheel resource check, and review
evidence. For Model/Zone/Tool changes update the corresponding contract and
focused tests.
