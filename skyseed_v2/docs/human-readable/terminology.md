# Terminology

| Term | Current meaning |
|---|---|
| SimulationTimeline | inclusive formal timestamp sequence with one observation interval |
| PhysicalWorld | authoritative static physical aggregate and requested-time projection owner |
| PhysicalFrame | exact timestamp plus complete immutable positions |
| PhysicalLinkRequest | Planner-created bounded candidate scope for one physical evaluation |
| PhysicalLinkObservation | sparse feasible physical candidates and work counts for one frame/request |
| physical candidate | feasible relationship fact; never automatically operational |
| selected | candidate chosen by operational policy for lifecycle advancement |
| acquiring | selected ISL whose acquisition reference has not elapsed |
| operational Connection | planned usable relationship with effective parameters |
| PlannedOperationalState | complete operational Connections, Zone assignments, and Route outcomes at one timestamp |
| PlanTransition | replay-complete semantic delta for exactly one formal timestamp; may be empty |
| Plan | immutable contiguous group of transitions with Plan/world/state lineage and precedence |
| planning frontier | latest fully planned formal timestamp |
| publication frontier | latest formal timestamp included in a published Plan |
| planning horizon | lookahead required before normal publication; canonical value 50 seconds |
| publication horizon | maximum earliest contiguous prefix per normal Plan; canonical value 20 seconds |
| semantic event reference | deterministic transition-derived event identity |
| CoordinationPolicy | precedence-only policy over semantic event references |
| BpfCompiledRealization | compiled intended state and backend intentions for one Plan boundary |
| activation boundary | compiled record for one formal transition, including a no-op transition |
| RealizedRuntimeState | Runner-owned observation of live runtime state |
| incremental realization producer | sole Timeline cursor and frame-to-observation-to-Plan-to-Compiler orchestrator |
| precomputed mode | draining the common producer and persisting every realization before runtime |
| future rolling mode | the same producer plus a bounded concurrent coordinator, buffer, and Runner consumer |
| planned unreachable | explicit Route outcome such as Zone unserved or no operational path |
| unexplained reachable loss | runtime failure while the compiled planned outcome says reachable |
| detached Tool | read-only consumer of durable records that cannot affect semantic compute or runtime handoff |

Physical, planned, compiled intended, and realized truths are never synonyms.
Likewise, candidate, selected/acquiring, operational, routed, compiled, and
realized are distinct lifecycle meanings.
