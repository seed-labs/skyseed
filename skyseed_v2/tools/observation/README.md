# Observation Tool

The Observation Tool parses completed generic JSONL records and never executes
the observed work. It preserves invocation owner, component, stage, scope,
duration role, work fields, resource fields, Tool wall/RSS/input/output
accounting, and raw records.

Current grouping covers physical projection, physical-link observation, ISL
selection, acquisition/lifecycle, GroundStation selection/ranking, validation,
evidence projection and the non-additive planning rollup, Zone planning,
parameter planning, Route planning, semantic transition/application,
coordination/precedence, Plan publication, Compiler, realization producer,
Runner artifact writer, Runner, and Traffic.

Generation writes the base stream. For a generated runtime invocation, the
private execution wrapper emits actual one-shot `runner-total` and
`traffic-total` rollups and merges them with the generation records into that
invocation's `observation/execution-observations.jsonl`. This Tool only parses
the combined stream; it does not synthesize runtime completion or expose a new
public runtime-state object.

Rollups overlap their leaf stages and are not summed with them. Zero-duration
resource samples remain visible. The output bundle contains raw JSONL, external
browser records, summary, HTML, inventory, and checksums.

The primary view is a hierarchical monotonic wall-time timeline. It starts with
large component spans on one shared axis and expands a selected component into
its recorded child stages. Geometry comes only from actual run-relative
`start_seconds`/`end_seconds` intervals; sequence order plus duration is never
used to invent placement. The primary interval sweep reports busy union, wall
with at least two distinct components active, cross-component overlap ratio,
maximum simultaneously active distinct components, component active wall, and
recorded pipeline gaps. It deduplicates nested spans within a component before
concurrency accounting. Raw span overlap, including nested rollups, remains an
advanced diagnostic. The timeline supports cursor-centred horizontal zoom,
drag pan, full-run reset, selected-span focus, and exact scope/work/resource
detail.

Tool rendering is detached side work. A rendering failure is reported for that
Tool but does not replace a successful generated runtime outcome.

The maintained Starlink example may also provide
`inspection/orchestration-timing.js`. Observation includes its monotonic outer
spans in the primary timeline vocabulary alongside, but not summed into,
nested component records. The separate collapsed orchestration section is a
non-navigating audit overview. Only measured spans have timeline geometry;
aggregate totals are labelled as such and have none. This separates semantic
production, inspection capture, persistence, detached Tool work, and remaining
orchestration without process polling.
