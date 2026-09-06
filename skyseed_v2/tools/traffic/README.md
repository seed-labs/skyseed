# Traffic Tool

This detached Tool combines formal PhysicalFrame-history ECEF evidence, canonical
planning evidence with explicit Route outcomes, Compiler intended-state
lineage, runtime traffic telemetry, and optional probe/Runner evidence.

Generated execution invokes it with `--physical-frames` and `--planning` paths.
Compiler inspection v3 supplies compact lineage, activation timestamps, intent
counts, and stable Zone prefixes. Compact `compiler.jsonl` supplies sealed
lineage, fingerprints, activation timestamps, and intent counts without Zone
prefixes; the separate sealed Runner artifact owns the normalized backend
payload.

Each ICMP transaction is matched to the active formal planned boundary. Output
shows source/destination direction and its explicit planned path or unreachable
reason, then distinguishes:

- explicit planned unreachable;
- observed planned-reachable traffic;
- activation-associated transient loss;
- unexplained loss while planned reachable;
- unclassified evidence.

Missing forwarding is diagnostic and is never treated as proof of planned
unreachability.

The Tool writes `traffic-replay.json`, standalone HTML, summary, inventory,
checksums, and wall/RSS/byte accounting. It neither controls Runner nor
recomputes physical or Route semantics.

The optional Runner-observer summary consumes complete Main duration from
current `activation_end.main_seconds`. It keeps `runtime_apply_seconds` and
`activation_switch_seconds` semantically distinct and does not substitute
either for Main duration. Missing or invalid optional Main timing produces an
empty distribution with `null` percentiles/max rather than a fabricated zero.

## Boundary and generation evidence

The maintained telemetry writer records every completed nominal boundary in
`boundary_activation`, including startup, parameter-only and no-op transitions.
Interface identities describe one generation lifetime, not one boundary index.
The reader checks the complete activation prefix against formal history, maps
interface snapshots to actual publication boundaries, and retains stable logical
ports across withdrawal/recreation with fresh ifindexes. It keeps packet kernel
generation and actual completed/in-flight boundary attribution separate from the
ideal wall-clock presentation timestamp. No repeated interface snapshot is
needed for a reused generation.
Within an actual in-flight Main interval, adjacent snapshots also identify
new or retiring physical interfaces whose selected generation excludes them.
This physical attribution does not add those links to the completed semantic
graph. Unrelated past/future incarnations are never used as a fallback.

Qualification correlates publication transients only with actual published
switch intervals. Reused generations carry no switch timestamps. Formal Main
statistics exclude Preparation's boundary 0; all boundaries remain in the raw
and projected history.
