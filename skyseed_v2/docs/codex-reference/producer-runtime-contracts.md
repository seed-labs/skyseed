# Incremental producer and persisted runtime

`skyseed_v2.experiment._realization_producer` owns the sole cross-domain
progression cursor. It is private because it composes Simulator semantics with
the BPF Compiler; neither layer owns the other.

## Producer contract

`_IncrementalRealizationProducer` is constructed with an exact
`SimulationTimeline`, `PhysicalWorld`, `PhysicalLinkModel`, and public
`Planner`. Its optional evidence directory must be new and empty.

`produce_next_realization()` performs the minimum required work:

1. sample bounded prehistory and prime the Planner once;
2. request the next formal timestamp from the Timeline iterator;
3. request `PhysicalWorld.frame_at(timestamp)`;
4. compute one matching physical-link observation from the Planner request;
5. feed the frame and observation to `Planner.observe`;
6. buffer newly published Plans;
7. flush the Planner once after formal input ends;
8. compile the next queued Plan against the previous realization;
9. persist evidence and release Planner publication state;
10. return one realization, or `None` after exhaustion.

Any failure closes the producer and shuts down Planner-owned Route workers.
The context-manager boundary and explicit `close()` are idempotent.

`snapshot()` reports counts and frontiers without exposing live semantic
objects. The queue and unpublished planning state remain bounded.

## Precomputed execution

Precomputed mode repeatedly calls `produce_next_realization()` on this same
producer. There is no separate batch Planner or Compiler. Each returned
realization is appended to `_RealizationHandoffWriter`, then the finalized
manifest is supplied to generated execution.

Planner publication retains its already computed output state only for the next
Planner publication prefix. The Producer receives and stores no parallel
Planner-state value: step 8 supplies exactly the Plan, PhysicalWorld, and
previous realization, and Compiler performs the required whole-Plan semantic
replay itself.

The handoff uses a strict format-version-3 manifest and
`deterministic-gzip-json-v1` `.json.gz` records. Deserialization validates the
complete sequence, world and state lineage, timestamps, activation boundaries,
semantic values, source observation cadence, backend intent references, and
checksums. It performs structural validation rather than reconstructing source
Plans or repeating Compiler lowering. Adjacent realizations require equal
intervals and the next start must be exactly the prior end plus that interval.
It never loads live Planner objects.

Generated execution stores only the persisted handoff reference. Before
`BpfRunner` construction, `_PersistedRealizationSupplier` eagerly verifies the
manifest and every compressed and uncompressed checksum, decompresses and
structurally decodes the complete bounded sequence, and validates adjacent
lineage. Only the resulting immutable tuple is supplied to Runner. Every
Runner pull is therefore a bounded tuple lookup; preparation and maintenance
cannot trigger persistence, decoding, planning, or compilation work.

## Durable evidence

With `evidence_path=output / "domain-history"`, the producer writes:

| Stream | Authority | Content |
|---|---|---|
| `physical-frames.jsonl` | physical | formal timestamp and authoritative ECEF positions |
| `planning.jsonl.gz` | Planner | streaming compressed canonical published evidence, compact indexed precedence endpoints, transitions, frontiers, fingerprints, and inspection facts |
| `compiler.jsonl` | Compiler | compact Plan/realization lineage, world/state fingerprints, start/end and activation timestamps, and intent counts |
| `domain-history-manifest.json` | producer invocation | terminal Timeline/cadence coverage, counts, final frontiers/lineage, and exact stream byte/hash seals |

The domain-history seal is authoritative for those three evidence streams and
the compact facts they actually contain. In particular, `compiler.jsonl` does
not contain the complete intended state or backend intentions; its header names
the canonical realization handoff as `semantic_payload_owner`.

That independent runtime handoff has its own authority and hashes:

| Artifact | Authority |
|---|---|
| `runtime/realizations/manifest.json` | ordered realization count, per-record paths and compressed/uncompressed hashes, plus the complete sequence hash |
| `runtime/realizations/realization-*.json.gz` | complete data-only runtime realization payloads, including planned output, compact activation schedule, precedence-fold counts, backend intentions, constraints, and intended state; the source Plan remains in Planner history |
| `runtime/execution.json` | generated execution identity plus the handoff format, encoding, count, sequence hash, and `manifest_sha256` anchor |

Neither artifact substitutes for the other: domain-history proves the sealed
producer evidence lineage, while the checksum handoff is the complete runtime
realization source. The streams do not duplicate a complete state at every
frame and contain no pickle payload. Tools compute derivable summaries offline.
The domain-history manifest is atomically published only after a terminal pull
returns `None`; `close()`, an exception, or stopping after the last value leaves
the history deliberately unsealed. Detached readers require the sibling seal,
stream records rather than loading raw JSONL wholesale, verify every stream,
and reject missing boundaries or mixed roots.

## Runtime observations and detached Tools

The private generated-runtime wrapper records actual one-shot `runner-total`
and `traffic-total` rollups. It merges those runtime records with the completed
generation Observation stream into the invocation-owned
`inspection/runtime-runs/<invocation-id>/observation/execution-observations.jsonl`.
The merge is diagnostic side work and creates no public runtime-state object.

After domain execution, `run.sh` invokes the detached Runner, Traffic,
Observation, and aggregate-index Tools. If domain execution succeeded, a
detached Tool failure emits a warning but does not replace the successful
runtime outcome. A domain-execution failure still determines the nonzero exit
status.

## Future live rolling stage

Future rolling execution must wrap this exact incremental producer with:

```text
incremental producer
+ bounded concurrent coordinator and buffer
+ Runner consumer
+ explicit backlog and frontier evidence
```

That coordinator is not part of Round B, and current documentation makes no
live full-scale qualification claim.
