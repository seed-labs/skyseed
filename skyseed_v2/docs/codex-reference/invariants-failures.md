# Invariants and failure semantics

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md); detailed object contracts
remain in their owning reference files.

## Scope

Validation, atomicity, identities, lookup behavior, and external project
launch.

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
| Simulator lifecycle | Topology once, Slicer once, then Planner at most once | `RuntimeError` |
| Topology lookup | exact owner/facade identity | `KeyError` |
| Zone membership | owned distinct matches; owned winner | `LookupError` / `ValueError` |
| Slicer Model chain | complete typed output; atomic publication | `TypeError` / `ValueError` |
| path/Difference | retained lineage, valid chain, adjacent IDs | `KeyError` / `LookupError` / `ValueError` |
| Planner | visibility/publication selected before Models, exact input prefixes, continuous `(start, end]` coverage, planned-state continuity, active-lifetime parameters, supported forwarding and valid same-time precedences | `TypeError` / `ValueError` / `RuntimeError` |
| Compose launcher | exact project/file/services/digest/final states | `ComposeMaterializationError` |
| Tools | current-run artifact or conspicuous failure artifact | no stale/fallback success |

## Invariants

- `NodeId` is a pure value with no lookup capability;
- owner lookup uses stable identity, never display/container/service names;
- UTC-aware time is normalized at input boundaries;
- immutable public values defensively snapshot iterables/mappings;
- deterministic ordering is part of published tuples and patch output;
- a failed operation never publishes partial owner state.
- a failed Slice candidate consumes neither its ID nor pending Timeline
  timestamp;
- generated Slice progress is independent of retained storage.
- failed built-in Planner synchronization preserves the published Plan,
  consumed-Difference progress, and Planner-owned incremental state.

## Failure semantics

Fail closed at the first owning boundary. Do not catch a contract error to
invent a successful fallback. External subprocess failure includes bounded
context but does not trigger legacy Compose or another output path.

Planner's built-in/reference algorithms are stateless and its own candidate
state is committed only after validation. The existing replaceable Protocols
cannot undo private mutations made by an arbitrary third-party stateful
strategy before it raises; no transactional rollback promise or public
prepare/commit/discard API is implied.

## Prohibited behavior

No silent coercion of booleans as numeric IDs, stale artifact reuse, string
identity guessing, partial mutation, exception-to-empty-result conversion, or
compatibility alias.

## Extension points

Extensions must return values satisfying the same owner validation. Extension
errors propagate and cannot broaden accepted output.

## Maintenance

Update this matrix when a public failure type or atomicity rule changes; add a
negative test at the real owner boundary.
