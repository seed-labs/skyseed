# Invariants and failure semantics

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md); detailed object contracts
remain in their owning reference files.

## Scope

Validation, physical-frame projection, atomicity, identities, lookup behavior,
and external project launch.

## Owners and consumers

Each owner validates at its boundary. Consumers must not repair invalid output
or guess an alternative identity.

## Contract

| Boundary | Required behavior | Failure |
|---|---|---|
| Emulator configuration/YAML | immutable validated declarations; schema/unknown-field checks | `StaticEmulatorConfigLoadError` |
| Simulator Zone-layout YAML | immutable validated Zone definitions; unknown-field/geometry checks | `ZoneLayoutDefinitionLoadError` |
| entity creation | owner-local uniqueness; no partial batch publication | `KeyError` / `ValueError` |
| Emulator→Simulator | complete scalar copy; unique `NodeId`; no SEED reference | `TypeError` / `ValueError` |
| Simulator lifecycle | Topology once with one explicit PositionModel, temporary legacy Slicer once, then Planner at most once | `RuntimeError` |
| Topology lookup | exact owner/facade identity | `KeyError` |
| Topology physical projection | sole ownership/call of PositionModel; aware requested timestamp; exact immutable Satellite/GroundStation Positions; no progression or operational state | `TypeError` / `ValueError` |
| Zone membership | owned distinct matches; owned winner | `LookupError` / `ValueError` |
| temporary Slicer Model chain | consume one PhysicalFrame; complete typed Connection/Route output; atomic legacy publication | `TypeError` / `ValueError` |
| path/Difference | retained lineage, valid chain, adjacent IDs | `KeyError` / `LookupError` / `ValueError` |
| Planner | planning/publication windows selected before Models, exact input prefixes, continuous `(start, end]` coverage, planned-state continuity, active-lifetime parameters, supported forwarding and valid same-time precedences | `TypeError` / `ValueError` / `RuntimeError` |
| Compose launcher | exact project/file/services/digest/final states | `ComposeMaterializationError` |
| BPF Runner | exact-next plus gap-free dual-bound rolling READY coverage; exact frozen Compose/container/PID/netns incarnation; real Preparation before READY; Main-only active transition; owned-resource cleanup | `TypeError` / `ValueError` / `RuntimeError`; invalid bound combinations fail at construction, stale evidence fails before mutation, resident-capacity deadlock fails before preload, and partial activation failure stops progression without claiming rollback |
| Tools | current-run artifact or conspicuous failure artifact | no stale/fallback success |

## Invariants

- `NodeId` is a pure value with no lookup capability;
- owner lookup uses stable identity, never display/container/service names;
- UTC-aware time is normalized at input boundaries;
- immutable public values defensively snapshot iterables/mappings;
- deterministic ordering is part of published tuples and patch output;
- each PositionModel has one exact Topology owner/caller;
- a PhysicalFrame timestamp is its identity and its Position keys are exactly
  that Topology's Satellites and GroundStations;
- Topology owns no Timeline cursor, next/current frame, or operational state;
- a failed operation never publishes partial owner state;
- a failed Slice candidate consumes neither its ID nor pending Timeline
  timestamp;
- generated Slice progress is independent of retained storage;
- failed built-in Planner synchronization preserves the published Plan,
  consumed-Difference progress, and Planner-owned incremental state.

## Failure semantics

Fail closed at the first owning boundary. Do not catch a contract error to
invent a successful fallback. External subprocess failure includes bounded
context but does not trigger legacy Compose or another output path.

Physical projection failure returns no frame and changes no progression,
because Topology has none. A temporary legacy Slicer candidate failure retains
the same pending Timeline timestamp and publishes no partial Slice.

Planner's built-in/reference algorithms are stateless and its own candidate
state is committed only after validation. The existing replaceable Protocols
cannot undo private mutations made by an arbitrary third-party stateful
strategy before it raises; no transactional rollback promise or public
prepare/commit/discard API is implied.

## Prohibited behavior

No silent coercion of booleans as numeric IDs, stale artifact reuse, string
identity guessing, partial mutation, exception-to-empty-result conversion,
slicing PositionModel alias/call, operational state in PhysicalFrame, or
compatibility alias.

## Extension points

Extensions must return values satisfying the same owner validation. Extension
errors propagate and cannot broaden accepted output.

## Maintenance

Update this matrix when a public failure type or atomicity rule changes; add a
negative test at the real owner boundary.
