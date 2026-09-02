# Traffic Tool

This detached Tool combines formal PhysicalFrame-history ECEF evidence, canonical
planning evidence with explicit Route outcomes, Compiler intended-state
lineage, runtime traffic telemetry, and optional probe/Runner evidence.

Generated execution invokes it with `--physical-frames` and `--planning` paths.
Compiler inspection v3 supplies compact lineage, activation timestamps, intent
counts, and stable Zone prefixes. Compact `compiler.jsonl` supplies sealed
lineage, fingerprints, activation timestamps, and intent counts without Zone
prefixes; the separate realization handoff owns the full semantic/backend
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
