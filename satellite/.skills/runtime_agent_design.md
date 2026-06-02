# Runtime Agent Design Skill

Use this guidance when work touches runtime update delivery, node-local agent
behavior, or Linux state installation.

## Goal

Move toward a generic node-local runtime execution model where each container
applies only its own local plan. Separate planning, storage, transport, agent
execution, installation, validation, and failure handling.

The final update mechanism is intentionally undecided. The architecture must
stay open to push, pull, `etcd`, file or snapshot stores, or another design
until investigation and scale tradeoffs are reviewed.

## Required Separation

Any runtime-agent design should distinguish these roles explicitly:

- update store
- distributor or client retrieval path
- agent
- installer
- validator
- rollback and failure reporting

Do not merge these roles casually in architecture documents.

## Required Responsibilities

### Update Store

- persists update plans or plan fragments
- does not decide routing or MPLS policy
- may be file-based, controller-pushed, polling-based, or future `etcd`
  backed

### Distributor / Client

- moves plan data from the store to the node
- may push or pull
- should not rewrite planning semantics

### Agent

- receives or fetches node-local plan data
- validates plan envelope and local applicability
- invokes the installer in a controlled sequence
- reports success, failure, and observed status

### Installer

- applies local Linux state only
- does not compute topology, labels, gateway choice, or zone ownership
- should be generic across planner types when possible

### Validator

- checks local post-apply state
- records evidence for success or failure

### Rollback / Failure Reporting

- defines what happens on partial failure
- defines what is reported upstream
- defines whether rollback is best-effort or guaranteed

## Node-Local Rule

The agent executes local node plans only. It must not mutate remote nodes or
act as a distributed planner.

Design choices should be evaluated against eventual large constellations on the
order of `10,000` satellites, not only the current small demo.

## Required Plan Contract

Any runtime-agent design should define the node-local contract clearly:

- plan identifier
- snapshot or time-slice reference
- target node identifier
- ordered local actions
- validation expectations
- idempotency or replay semantics
- replacement, supersession, or rollback semantics

## Storage And Transport Rule

Keep update transport and storage abstract. `etcd` may be investigated later,
but do not make it mandatory in the architecture before its tradeoffs are
reviewed.

## Validation Expectations

Each proposal should include:

- one node-local plan example
- one success path example
- one partial failure example
- one description of rollback or failure reporting behavior
- one note on why the design is expected to scale better than naive
  controller-driven per-node shell mutation

## Anti-Patterns

- Do not embed planner logic in the agent.
- Do not require the installer to understand global topology intent.
- Do not make a controller-triggered shell script the unexamined permanent
  interface just because it exists today.
- Do not assume a design is acceptable just because it works at tiny container
  counts.
