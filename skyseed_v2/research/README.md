# Research archive

This directory is Skyseed's persistent, repository-level archive for external
research evidence and provenance. It is intended to grow over time as papers,
datasets, regulator records, external tools, and experiment references are
reviewed. It is not temporary S4 material and is not scheduled for deletion
after the S4 work.

Records here can have different authority levels. Each collection must identify
its sources and distinguish primary evidence, derived facts, secondary
cross-checks, and local interpretation. Presence in `research/` does not make a
value accepted Skyseed architecture or runtime semantics.

Formal examples freeze reviewed subsets into their own checked-in inputs and
state the transformation or selection used. Runtime code must not depend on
`research/`; this tree is neither a package nor a public data API. Focused
provenance tests may read a frozen record when that is useful, but scenario
construction must remain self-contained.

## Collections

- `starlink/gen1/`: Starlink Gen1 constellation and gateway evidence first
  consolidated during the v2 S4 work.
