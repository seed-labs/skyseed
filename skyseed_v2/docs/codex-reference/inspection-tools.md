# Inspection Tools contract

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Repository-local Topology, Zone, and Dynamic Slice inspection pipelines.
Tools are read-only consumers and are not installed `skyseed_v2` product API.

## Owners and consumers

Each Tool preserves this responsibility boundary:

```text
reader → projection → display_analysis → renderer → artifact_writer
```

The formal reader consumes the exact `Topology` or `Slicer`. Projection and
later responsibilities consume only Tool-owned detached values. A small Tool
may colocate adjacent responsibilities when the boundary remains explicit;
file count and filenames are not part of the contract. Examples call the Tool
facades; browser smoke consumes emitted HTML from the validation side.

## Contract

- the formal-reader responsibility performs every Topology/Slicer lookup and
  returns a Tool-owned read snapshot with no retained formal object;
- a private read-plan helper may choose deterministic coordinates required by
  a reader's formal queries, but cannot read a formal object or perform
  display annotation;
- the projection responsibility converts only that snapshot into private
  serializable view data and never imports or invokes a formal domain object;
- display analysis adds explicitly non-authoritative display values,
  including orbit-plane fitting/eigendecomposition, rectangle-boundary
  sampling, focus visibility, guides, and classifications;
- rendering accepts final view data and returns standalone display text;
- artifact writing writes supplied data/page bytes and owns no rendering or
  formal reads;
- the Tool facade composes the responsibilities and preserves its Tool-facing
  function.

Dynamic reads use retained Slice, path-resolution, and adjacent-Difference
APIs. Zone reads use formal membership/resolution results. Display analysis
may explain or arrange those results but cannot replace them.

## Invariants

- Tools never mutate formal values or invoke Models;
- only readers import or call formal Topology/Slicer APIs;
- read snapshots contain detached Tool-owned primitives/values, not Topology,
  Slicer, Slice, Difference, or entity instances;
- projection, display analysis, renderer, and writer cannot become a second
  authoritative computation path;
- renderer receives final view data only;
- writer performs filesystem output only;
- browser smoke remains outside the pipeline.

## Failure semantics

Wrong formal input, missing retained data, foreign identity, or formal lookup
failure propagates conspicuously; readers do not invent empty/fallback data.
Projection/analysis/render validation failures stop artifact success. The
writer creates output only from values supplied for that invocation and does
not reuse stale success artifacts.

## Prohibited behavior

No Model execution, position propagation, authoritative connection/routing/
membership/Difference computation, formal object retained past the reader,
formal API call outside a reader, browser driver inside a Tool, generic
registry/plugin framework, renderer-side read, writer-side render, stale
artifact fallback, or production-to-Tools dependency.

## Extension points

A Tool may add private snapshot fields, display analysis, rendering, or
artifacts while preserving the responsibility boundary. It may merge
low-complexity adjacent implementation files when the snapshot/formal-read,
final-view/render, and file-only-write boundaries remain testable. A new
formal read belongs in the formal-reader responsibility and requires a
boundary test. Shared abstractions require a concrete repeated contract and
cannot introduce a registry or authority.

## Maintenance

Update the affected Tool package, its local README, Tool/user guide,
traceability map, boundary and focused Tool tests, browser smoke when UI
changes, and this contract. A formal domain change also updates its owning
Simulator contract first.
