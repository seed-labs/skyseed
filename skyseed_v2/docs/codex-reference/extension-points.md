# Extension points

Skyseed uses narrow constructor-injected Protocols. It does not provide a
runtime plugin registry or alternate production pipeline.

| Extension | Owner/caller | May decide | Must not own |
|---|---|---|---|
| `PositionModel` | PhysicalWorld | complete positions at one requested timestamp | frame publication, time cursor, operational state |
| `PhysicalLinkModel` | realization producer | feasibility for one frame and bounded request | candidate scope, selection, acquisition, Route, history |
| `ZoneMembershipModel` | ZoneLayout | geographic matches and deterministic resolution | service assignment |
| `InterSatelliteConnectionPlanningModel` | Planner | bounded pair scope, desired relation mix, selection and lifecycle proposal | physical geometry, capability definition, other planning domains |
| `GroundStationAccessPlanningModel` | Planner | operational GroundStation access selection | ISL acquisition or Zone service |
| `ZoneServicePlanningModel` | Planner | served/unserved assignment from feasible operational access | physical membership or Route lowering |
| `ParameterPlanningModel` | Planner | effective parameters and bounded deadband memory | Connection lifetime or backend control |
| `RoutePlanningModel` | Planner | complete reachable/unreachable outcomes and worker lifecycle | physical selection, Plan publication, Compiler |
| `CoordinationPolicy` | Planner | precedence between exact transition-derived events | event payload or existence, backend schedule |
| `PlanningWindowPolicy` | Planner | planning/publication/prehistory horizons | Timeline or execution scheduling |
| Compose strategy | Compose launcher | explicit project startup grouping | compilation or semantic fallback |
| Tool projection/UI | repository Tools | detached non-authoritative presentation | domain computation or lifecycle |

## Direct injection

Simulator creation supplies one exact object for each Model/policy role. Role
identity must be distinct at the composition boundary. Replacement affects only
that role's defined input/output contract.

The canonical ISL separation is intentional: maximum simultaneous links belongs
to PhysicalWorld capability, while configurable cyclic pair scope (reference K1), desired 2+2 mix, retain-first
selection, 5-second preparation and future-service requirements belong to the
existing planning Models. Physical contact proof remains in physical Models.

## Validation

Extensions return immutable values satisfying the same owner validation as
reference implementations. Exceptions propagate. Consumers do not repair
invalid output, reinterpret identities, rerun a competing algorithm, or select
an alternate implementation.

## New extension categories

A new public role, discovery mechanism, backend selector, functional Emulator
layer, or lifecycle owner is an architecture change requiring explicit approval.
Private helpers within an accepted owner do not create a new extension point.
