# Extension points

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

SEED integration, Zone membership, three Slicer Models, five Planner
algorithm roles, configuration loaders, Compose launch options, and repository
Tools.

## Owners and consumers

| Extension | Owner boundary | May customize | Must not own |
|---|---|---|---|
| `SkyseedAutonomousSystem` | `emulator/seedemu` | Skyseed entity creation plus standard router/extension-node inventory attachment | Simulator or Tools |
| upstream `ExtensionNode` / `Docker` | SEED | generic extension-node lifecycle, metadata, and zero-interface deployment | Skyseed domain semantics |
| future functional Layer (unfrozen) | Skyseed Emulator | shared software, files, build/start commands, and daemons | entity creation, Registry, ownership, identity metadata, compiler dispatch |
| `ZoneMembershipModel` | ZoneLayout | physical matches and winner among matches | Zones or service assignment |
| `PositionModel` | Slicer | complete Position mapping | Slice publication |
| `ConnectionModel` | Slicer | complete ConnectionSlice | Route or publication |
| `RouteModel` | Slicer | complete RouteSlice | path validation/publication |
| `PlanningWindowPolicy` | Planner | select a planning window from available Differences and its publication window before Models run | Slicer retention, Slice creation or execution scheduling |
| `ConnectionPlanningModel` | Planner | planning-window-aware nominal time and short-lifetime candidates | planning-window selection or final suppression |
| `ParameterPlanningModel` | Planner | cross-Difference sparse targets against last planned values | Connection lifetime or backend control |
| `ForwardingPlanningModel` | Planner | timing and minimal same-time precedences from formal RoutePatch and narrow immutable path facts | Slicer, RouteModel, route selection or execution scheduling |
| `CoordinationPolicy` | Planner | transform all three typed Model results into one coherent keep/suppress result | fixed Planner validity |
| YAML loader data | receiving configuration package | accepted schema values | domain runtime objects |
| Compose strategy | ComposeProjectLauncher | explicit single/batched project startup | compile/edit/fallback |
| Inspection projection/UI | repository `tools/` | non-authoritative display | formal computation |

The private `experiment.execution` BPF composition reads a definition that
records one explicit Python
module/callable identity for the experiment's existing progression builder.
That direct reference is not a plugin registry, model registry, or new public
extension hierarchy; generation and execution call the same example-owned
builder source, and source drift requires output regeneration.

## Contract

SEED extraction direction is:

```text
Skyseed construction/entities → emulator/seedemu → upstream SEED
```

`Satellite` is a public upstream `ExtensionNode`; `GroundStation` is an
upstream `Router`. Both project immutable identity through public Node labels,
and GroundStation also uses `setGeo`. The standard SEED lifecycle/compiler
consumes those surfaces without importing Skyseed entity types. The Skyseed
integration package has no dependency on Simulator, Tools, Compose launcher,
compiler subclass, package asset, or examples.

No functional Layer exists yet because no shared runtime software/file/command
contract is approved. Its future name, number, and public API remain
deliberately unfrozen.

## Invariants

Extensions remain narrow, typed, owner-validated, and replace no formal owner.
Planner algorithm roles may be implemented by the same Python object when it
satisfies multiple Protocols; role separation does not impose object-identity
separation.

## Failure semantics

Unsupported upstream API shape or invalid entity ownership fails closed.
There is no compatibility adapter to an older path.

## Prohibited behavior

No plugin discovery/framework, generic ToolRegistry, Simulator logic inside
SEED integration, Slice / Difference semantics in a compiler hook, or example-only
branch.

## Extension points

New extension categories are public architecture decisions. Private helpers
inside an accepted extension are implementation detail.

## Maintenance

For SEED changes update package README, direct-import/consumer checks,
standard compiler integration tests, and review evidence. For Model,
Planner-algorithm, Zone, or Tool changes update the corresponding contract and
focused tests.
