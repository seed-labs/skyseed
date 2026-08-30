# Timeline, Slice, retention, and Difference

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Timeline iteration, timestamp-addressed PhysicalFrame projection, synthetic
Slice 0, temporary legacy Slicer atomic candidate/retry, generated progress
versus retained storage, retained queries/path ownership, and exact
`TopologyDifference` content.

## Owners and consumers

`SimulationTimeline` owns time rules and yields progression timestamps.
Topology owns PositionModel and projects one immutable PhysicalFrame for a
requested timestamp without owning progression. The temporary legacy Slicer
owns one iterator, pending generated progress, publication, retained queries,
and the fixed comparator; this cursor/publication ownership is migration debt
scheduled for deletion. TopologySlice owns one immutable legacy published
result. TopologyDifference is created on request and owned by its caller.

## Contract

`SimulationTimeline` accepts timezone-aware datetimes only, immediately
normalizes start/end to UTC, requires a positive interval and end not before
start, and yields UTC values. Bounded iteration includes only aligned
timestamps `<= end`; unbounded iteration is lazy/infinite.
`iter_timestamps()` is the only authority for real Slice progression
timestamps. Slicer does not accept an arbitrary timestamp and does not
duplicate interval arithmetic to generate or validate Slice time.
`Topology.frame_at(timestamp)` accepts one requested aware timestamp, but
Topology owns no Timeline iterator, current time, next-frame method, or other
progression state.

After successful Slicer construction, synthetic Slice 0 is immediately
queryable:

```text
slice_id = 0
timestamp = None
positions = empty
ConnectionSlice = empty
RouteSlice = empty
```

It is the formal baseline for `0 → 1`, is not a Timeline instant, and has no
extra kind field. `TopologySlice` is frozen, slotted, and deeply immutable and
contains only ID, timestamp, Positions, ConnectionSlice, and RouteSlice. It
contains no Topology, Model, previous Slice, Planner, or backend reference and
does not resolve paths itself.

Each real candidate uses the pending Timeline timestamp and next monotonic
continuous ID:

```text
pending timestamp
→ Topology.frame_at(timestamp)
  → PositionModel → validate exact Positions → PhysicalFrame
→ temporary legacy ConnectionModel → validate
→ temporary legacy RouteModel → validate every published chain
→ construct complete legacy TopologySlice
→ atomically publish
→ advance iterator / ID
```

Any failure publishes nothing, leaves the retained published set/latest Slice
unchanged, consumes neither ID nor pending timestamp, and retries the same
logical Slice on the next call. There is no fallback. Prepared start-time
Connection pairing may survive a downstream retry, but is not a published
partial result. Topology projection itself never consumes progression.

`create_slices(count)` and bounded `create_all_slices()` only repeat
`create_next_slice()`. Timeline exhaustion raises `StopIteration`;
`create_all_slices()` on an unbounded Timeline raises `RuntimeError`.

Slicer retains one pending value from its Timeline iterator. Generated ID/time
progress changes only after publication and is independent of `_slices`,
`_slices_by_timestamp`, their length, and any future retention policy.
Removing an older retained prefix does not recalculate, rewind, or skip
generated progress. Only the retained window is a long-term query promise;
complete permanent retention is not frozen. Retained metadata validation uses
the Timeline's iterator sequence, not a second timestamp formula.

Queries expose retained ID/timestamp lookup, latest retained published Slice,
latest ID/timestamp, pending next timestamp, exhaustion, retained ID set/range,
exact Topology/Timeline/ConnectionModel/RouteModel references, path resolution,
and adjacent Difference. PositionModel is queried through Topology, not
Slicer. There is no ambiguous `current` API.

`create_difference(previous_id, current_id)` first resolves two retained
Slices from the same Slicer, checks their identity/type, Timeline-derived
metadata and strict `N → N+1`, invokes one fixed pure comparator, and returns an independent
frozen `TopologyDifference`. Slicer does not retain it and TopologySlice does
not create it. The sibling Planner is the first formal retained consumer: it
requests each newly available adjacent Difference exactly once and does not
move comparison or Difference ownership into planning.

Difference contains exactly:

- previous/current Slice IDs and timestamps;
- full added Connections;
- removed ConnectionIds;
- changed-new-value-only directional Connection parameter patches;
- Zone service assignment addition/change/removal patches;
- Route next-hop addition/change/removal patches.

It excludes complete Slices, Position motion, old values, candidates,
visibility/coverage/geometry, Model configuration, labels, interfaces,
resources, Planning thresholds, rollback, and backend operations. `0 → 1`
uses identical semantics and carries the complete initial dynamic-network
change. Output ordering is deterministic.

## Invariants

- one iterator and one pending timestamp authority;
- Topology physical projection has no iterator, next-frame identity, retained
  current frame, or operational state;
- each real candidate consumes one exact immutable PhysicalFrame for its
  pending timestamp;
- real IDs are continuous/monotonic across successful publications;
- each timestamp is published at most once;
- PhysicalFrame and both legacy operational results validate before
  publication;
- retry uses the identical pending ID/timestamp;
- retained storage cannot define generated progress;
- Difference inputs are retained, same-lineage, and strictly adjacent;
- already-published immutable retained contents are authoritative and are not
  revalidated during Difference creation;
- Difference patches carry new values/removals only.

## Failure semantics

Naive/invalid Timeline input raises `TypeError`/`ValueError`; exhaustion raises
`StopIteration`; unbounded all-generation raises `RuntimeError`; missing
retained IDs/timestamps raise `KeyError`; corrupt retained metadata or
non-adjacent/reverse Difference requests raise `TypeError`/`ValueError`.
Candidate failure propagates after preserving pending progress and publication
state.

## Prohibited behavior

No arbitrary Slice timestamp, duplicated timestamp formula, partial
publication, consumed progress on failure, retention-length-derived next ID,
permanent-retention promise, full retained-content revalidation for a
Difference request, non-adjacent comparator, Difference retention,
Position patch, operational state in PhysicalFrame, Topology-owned
progression, full-Slice duplication, rollback/backend operation, or Tool-side
comparison.

## Extension points

A retention-window policy may evict old published Slices without changing
generated identity/time. Planner coverage and optimization are defined in the
separate planning contract and do not broaden Difference semantics;
the accepted target removes the legacy Slicer cursor/publication seam while
preserving current Planner/Compiler/Runner semantics. Implementation state is
recorded only in
[`CURRENT.md`](../CURRENT.md).

## Maintenance

Update Timeline/Topology/temporary-Slicer/Slice/Difference code, atomic retry
and retention tests, Difference tests, Slice / Difference Tool projections,
Simulator diagrams, failure reference, and this file.
