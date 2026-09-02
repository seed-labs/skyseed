# Route planning and path contracts

Route outcomes are Planner-owned semantic truth. `RoutePlanningModel` consumes
the exact PhysicalWorld, operational Connections, Zone-service assignments,
Route demands, and previous outcomes. It produces complete deterministic
outcomes and transition payloads.

## Demands and targets

`RouteDemand` identifies a source node and one target:

- `GroundStationRouteDemandTarget` names an exact GroundStation;
- `ZoneRouteDemandTarget` names a Zone whose current serving Satellite is the
  operational terminal.

Demands are explicit and bounded. The Planner does not materialize an
unrequested all-pairs matrix.

## Outcomes

`ReachableRouteOutcome` contains the demand, next hop, and complete ordered
path. Every adjacent path pair must correspond to an operational Connection in
the current state.

`UnreachableRouteOutcome` carries one explicit reason. Current required reasons
are:

- `ZONE_UNSERVED`: the target Zone has no serving Satellite;
- `NO_OPERATIONAL_PATH`: all terminals are defined but the operational graph
  provides no valid path.

Absence of a forwarding entry is not a substitute for one of these outcomes.

`RouteOutcomeTransition` carries the previous and current outcome or `None`.
This makes reachable, changed, newly unreachable, and withdrawn semantics
replayable without calling the Route Model.

## Built-in Model

`GatewayHopCountRoutePlanningModel` uses the shared reverse-BFS routing core,
bounds Route demands, and reuses a bounded worker pool. Canonical tie-breaking
is deterministic. GroundStation backbone relay and Zone transit rules remain
explicit policy rather than accidental graph behavior.

Route work is skipped when only positions or effective parameters change.
Operational graph, Zone-service, or demand change triggers recomputation.

## Downstream use

Compiler lowers the outcome carried in Plan; it does not rerun path search.
`BpfCompiledRealization.output_planned_state` preserves explicit reachability
for the next compile and Traffic. Traffic distinguishes planned unreachable
from unexplained loss of a planned-reachable path.

## Failure behavior

Unknown identities, duplicate demands, incomplete outcome coverage, a reachable
path with absent edges, inconsistent next hop, invalid Zone terminal, or a
malformed transition fails before Planner publication. Model exceptions trigger
producer cleanup and Route worker shutdown.
