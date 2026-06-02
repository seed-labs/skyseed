# Orbit Design Skill

Use this guidance when work touches orbit ingestion, visibility evaluation, or
topology slicing.

## Goal

Produce orbit-driven topology artifacts that are explicit, time-sliced, and
replaceable. Do not couple planners or runtime installers to one specific
orbit model.

## Required Approach

- Define the input schema explicitly before implementation.
- Define the output snapshot schema explicitly before implementation.
- State time-slice semantics clearly:
  - slice identifier
  - wall-clock or simulation time reference
  - slice duration
  - whether slices are independent or delta-related
- State visibility assumptions clearly:
  - gateway visibility
  - `UT` visibility
  - inter-satellite visibility
  - any elevation, distance, or policy thresholds
- State `UT` and zone assumptions clearly:
  - `UT` coordinates are fixed for the current stage
  - `UT` zone membership is stable once assigned
  - zone serving-satellite selection must consider the `UT`s in the zone, not
    only an abstract zone center
- Preserve replaceability:
  - orbit model must be swappable
  - visibility model must be swappable
  - zone generation must be swappable
  - topology slicer must be swappable

## Required Inputs

Any orbit/topology design change should specify the expected schema for at
least:

- satellite identifiers and orbit state source
- gateway identifiers and locations
- `UT` identifiers, locations, or zone membership
- optional zone definitions or zone-generation inputs
- visibility constraints
- `ISL` policy constraints
- `GSL` policy constraints
- time-slice configuration

## Required Output

The design must define a stable `TopologySnapshot`-like artifact containing at
least:

- snapshot identifier
- time-slice metadata
- node inventory relevant to that slice
- active `ISL` set
- active gateway-satellite connectivity
- active `UT` or zone-to-serving-satellite mapping
- exactly one active responsible serving satellite per zone per slice
- any planner-facing metadata needed for replaceable routing policy

Do not make planners depend on hidden controller state or raw orbit data files
when snapshot fields can carry the required information directly.

## Time-Slice Rules

- Say whether the snapshot is full-state or delta-state.
- Say whether serving-satellite ownership is unique or multi-candidate.
- For zone egress, the active responsible serving satellite must be unique in a
  slice even if backup candidates are also tracked.
- Say whether link activation is deterministic from inputs or policy-selected
  from candidates.
- Say how absent visibility is represented.

## Validation Expectations

Every orbit/topology design change should include validation examples such as:

- one small example input set
- one expected snapshot example
- one edge case with no visible serving satellite
- one edge case with competing candidate satellites or links

Validation may be documentation-only at first, but examples must be concrete
enough to implement later.

## Anti-Patterns

- Do not hard-code one orbit source as platform behavior.
- Do not hard-code one zone partitioning strategy as platform behavior.
- Do not mix visibility policy with runtime installation logic.
- Do not make topology slicing depend on MPLS planner assumptions.
- Do not describe a topology artifact vaguely; provide fields and semantics.
