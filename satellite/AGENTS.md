# Satellite Iteration 2 Working Rules

This file supplements the repository-root `AGENTS.md` for work under
`satellite/`. It defines the local rules for Codex sessions that touch the
satellite research platform.

## Project Posture

- Treat `satellite/` as a research platform, not a fixed demo.
- Treat current controller-driven MPLS behavior as a temporary baseline, not
  permanent architecture.
- Keep changes scoped to `satellite/` unless a design decision explicitly
  justifies broader impact.
- Do not modify SEED core in Iteration 2 unless a later reviewed design
  decision says it is necessary.

## Documentation First

- For any meaningful architectural change, update the relevant design document
  before or together with code changes.
- `design_principle.md` is the Iteration 2 source of truth. New implementation
  work must align with it or update it first.
- Large changes should start from boundaries, interfaces, schemas, and
  invariants, not from direct edits in `controller.py` or `mpls_control.py`.
- If the current implementation and the desired architecture diverge, document
  the gap explicitly instead of silently hardening the current behavior.

## Goal-Driven Workflow

- Work from project goals and stable abstractions, not one-off task lists.
- Prefer interface-first, pluggable design:
  - orbit/visibility/topology slicing
  - zone generation
  - routing/MPLS planning
  - runtime update storage/distribution
  - node-local agent execution
  - local Linux state installation
- Preserve mechanism/policy separation. Codex should not fuse platform
  mechanism with one specific routing or MPLS policy.
- The default G/Z MPLS planner is only one planner. Do not treat it as the
  platform definition.
- Treat G/Z as logical planning semantics, not as a requirement to materialize
  two literal runtime tables.
- Do not hard-code one zone partitioning algorithm as permanent platform
  behavior.

## Investigation Rules

- Investigate OpenSN before adopting its ideas in architecture or code.
- Do not claim OpenSN findings unless they are verified from source or
  documentation.
- When borrowing an idea from OpenSN, record:
  - what was inspected
  - what problem it solves
  - what is adopted
  - what is rejected
  - why the choice fits `satellite/`
- Do not copy OpenSN structure blindly.

## Status Discipline

- Update `status.md` after each meaningful milestone.
- Each `status.md` update should keep the sections current:
  - `Current Baseline`
  - `Iteration 2 Goal`
  - `Completed`
  - `In Progress`
  - `Open Questions`
  - `Risks`
  - `Next Recommended Action`
- If validation commands are run, record them in `status.md` or in the session
  reply with enough detail to reproduce them.

## Validation And Evidence

- Record validation commands whenever they are run, even for partial work.
- Keep validation scoped to the change:
  - document review commands for documentation-only work
  - schema or unit validation for interface work
  - runtime commands only when runtime behavior is intentionally exercised
- Do not describe a design as validated if only static reading was performed.

## Editing Rules

- Keep diffs small and recoverable.
- Prefer adding or updating explicit interfaces, schemas, and contract docs
  before expanding implementation logic.
- Do not hard-code future update stores such as `etcd` before the abstraction
  boundary is documented.
- Keep eventual scale in mind. Avoid architecture choices that are only
  acceptable because the current demo is small.
- Do not put planner logic inside runtime agents.
- Do not make the installer understand why a plan was generated; it should only
  apply node-local actions safely.
