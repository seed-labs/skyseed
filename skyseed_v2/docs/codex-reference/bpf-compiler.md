# BPF Compiler contract

The BPF Compiler lowers one self-contained semantic Plan into one immutable
compiled realization. It is downstream of Planner and upstream of BpfRunner.

## Public entry

```python
from skyseed_v2.backend.bpf import compile_bpf

realization = compile_bpf(plan, physical_world, previous_realization)
```

The three arguments are the complete source boundary:

- `plan`: timestamped semantic transitions, precedence, and lineage;
- `physical_world`: exact static identity, capability, and provenance;
- `previous_realization`: preceding compiled semantic/backend state, or `None`
  for bootstrap.

Compiler never queries Planner history, physical candidates, or a hidden
context object. It does not run selection, acquisition, Zone assignment,
parameter policy, Route planning, LOS, or elevation algorithms.
No Planner output-state side channel is accepted: semantic output is always
reconstructed by replaying `plan` from the previous realization output.

## Validation and lowering

Compilation:

1. compares `plan.physical_world_fingerprint` with
   `physical_world.semantic_fingerprint()`;
2. validates bootstrap or immediate previous-Plan lineage;
3. compares input fingerprint with the previous realization output;
4. replays every transition through the public semantic apply contract;
5. verifies the Plan output fingerprint;
6. lowers Connection establish/withdraw and effective parameters;
7. lowers Zone assign/change/unassign;
8. lowers Route reachable/change/unreachable/withdraw;
9. maps semantic precedence into backend constraints;
10. adds backend-only resource and ordering constraints;
11. verifies the resulting intended state.

Zone lowering separates readiness, forwarding publication, and retirement so
new reachability becomes available before old resources disappear.

Compiler validates misuse and backend feasibility; it is not a second Planner
and does not rerun physical or planning algorithms. It does not reject a valid
semantic choice merely because another policy could have chosen differently.

## BpfCompiledRealization

The public immutable result carries:

- `source_plan_id` and `source_previous_plan_id`;
- `physical_world_fingerprint`;
- `input_state_fingerprint` and `output_state_fingerprint`;
- exact `observation_interval` and aggregate semantic-precedence folding proof;
- `start_timestamp` and `end_timestamp`;
- `output_planned_state`;
- `compiled_activation_boundaries` and `activation_timestamps`;
- Connection/resource, parameter, forwarding, and Zone-edge intentions;
- semantic and backend constraints;
- compact intended backend state required by the next compile.

Each PlanTransition produces one `BpfCompiledActivationBoundary` containing its
timestamp and fingerprint. Empty transitions remain explicit. Across
canonical S3, 30 realizations therefore retain all 121 activation timestamps.

The output planned state is intended semantic truth at the realization
boundary. It is not `RealizedRuntimeState`; only BpfRunner owns live kernel
truth.

## Persistence and Tools

The realization handoff uses a strict format-version-3 checksum manifest and
`deterministic-gzip-json-v1` records. Construction of the persisted supplier is
the eager reader boundary: before `BpfRunner` exists, it validates the manifest,
compressed and uncompressed hashes, sequence lineage, and references, then
decodes the complete bounded sequence. It requires valid intent references,
state fingerprints, equal adjacent realization intervals, and an exact next
start boundary. It does not reconstruct the source Plan or repeat Compiler
lowering; the aligned authoritative Plan remains in Planner history for Traffic,
Tools, and qualification. Only the resulting immutable tuple reaches Runner.
No pickle or live Planner object is accepted.

The sealed `domain-history/compiler.jsonl` has a different, deliberately compact
contract: lineage, fingerprints, timestamps, activation timestamps, and intent
counts. It does not duplicate complete intended state or backend intentions;
the canonical realization handoff owns that payload and
`runtime/execution.json` anchors its hashes.

The BPF Compiler Tool renders full Plan/realization values as lineage,
transitions, activation boundaries, intended state, operations, and constraints.
Its compact-history reader validates only the compact `compiler.jsonl` contract;
it does not reconstruct missing semantic payload. Neither path recompiles.

## Failure behavior

Wrong world, wrong predecessor, stale fingerprints, malformed transition
references, invalid output state, missing resulting semantic state, or
impossible backend lowering raises before publishing a realization. The
previous realization remains immutable.
