# Finite Runner correction and work-reduction milestone

Historical qualification record, closed by user direction on 2026-09-07.
This records measured results and implementation history, not a new runtime
contract. Current ownership remains in `../accepted-design.md`.

## Version and scope

The Git commit introducing this record is the final source checkpoint. Its
parent is `1b3d14cb296caefd2e87d1811cea8dcff08fab51` (2026-09-02). It consolidates
several previously uncommitted finite-execution and Runner iterations; the whole
diff must not be attributed to the final grouped-deletion change alone.

The final production source fingerprint matches the work-reduction S3 below.
Closeout only corrects documentation, synchronizes an opt-in live test's private
request fixtures, and archives evidence; it does not change production execution.
Intermediate source snapshots were not captured as separate commits. Their
recorded fingerprints identify those versions but cannot reconstruct them.

## Recorded changes and Architecture Delta

- Finite compute completes before Runner startup. A sealed typed artifact
  replaces the former runtime supplier/handoff; there is no rolling execution.
- Preparation discovers stable runtime identities, retains namespace sockets
  and BPF descriptors, constructs all finite plans, prewrites semantic BPF maps,
  and physically activates B0. Formal `run()` starts at B1.
- Generation tables use stable backend-private logical ports, with live ifindex
  bindings and explicit generation publication controlling visibility. Future
  veths are not created early. Maintenance/READY/deferred-generation/reclaim
  machinery was removed; Cleanup retains final resource ownership.
- Main uses one persistent privileged helper and direct netlink/BPF operations,
  rather than per-action container/namespace subprocesses.
- Veth creation includes both MACs, one RX/TX queue per endpoint, and exact
  future retirement-group metadata. Withdrawn UP pairs are deleted directly.
  Namespace-scoped group requests collect kernel unregister work; eight helper
  threads overlap independent deletion batches in the measured run. All batches
  join before Main completion. Rollback uses exact individual candidate pairs.
- Preparation removes repeated parameter scans, unused interface derivation and
  full generation-key copies; it reuses immutable identities, names, slots and
  previous derived routes, indexes telemetry routes, normalizes operations, and
  encodes activation requests once.
- Automatic pacing uses fixed monotonic origin plus nominal offset. Helper
  arrival and wakeup delay no longer shift every subsequent deadline.
- Traffic/Runner inspection consumers track B0 separately, actual generation
  switches, reused generations and recreated interfaces. Failure paths preserve
  uncertain cleanup ownership. Compact timing remains nonblocking; parallel
  task-duration sums are separate from elapsed wall time.

Across the **whole checkpoint**, `BpfRunner.__init__` now takes
`compose_project_name` and `runtime_artifact`, replacing the future supplier and
lookahead/depth arguments. `SimulationTimeline.end_time` is finite and its old
`is_bounded` property is removed. Existing public Runner lifecycle methods,
root exports, top-level packages and Simulator/Planner/Compiler ownership remain.
The final work-reduction iteration itself did not change those public contracts
or artifact v3.

The added production files have existing backend ownership:

| File | Responsibility |
|---|---|
| `backend/bpf/_runner_artifact.py` | Finite artifact writer/reader, backend lowering and Runner input |
| `backend/bpf/_runner/kernel_netlink.py` | Runner-private retained socket/UAPI execution |
| `backend/bpf/_runner/process_incarnation.py` | Runner-private startup PID/netns identity capture |

The retirement pool and owned-pair ledger live inside the existing privileged
helper. New test files cover these contracts; neither tests nor this historical
evidence introduce a runtime component. The old `_realization_handoff.py` and
its retired test were removed. Public ownership diagrams remain consistent;
private execution diagrams and Example documentation describe the finite flow.

Example remains: `main.py` generates output; generated `start.sh` deploys;
`run.sh` starts Runner/Traffic; `launch.sh` combines start and run; `cleanup.sh`
is independent. Qualification used maintained explicit output paths to preserve
prior evidence, not a substitute execution chain.

## Measured canonical S3 progression

All runs below use 720 satellites, 355 ground stations, 732 zones, a 600-second
timeline and five-second nominal cadence. Steady samples exclude the initial
network construction: first activation for the original run, `preparation=true`
for the corrected runs. Each comparison contains 120 steady activations.

| Run | Main p50 / p95 / max (s) | Final start lag (s) |
|---|---|---:|
| Original S3, 2026-09-04 | 4.722 / 7.531 / 10.452 | 312.177 |
| Finite/logical-port correction, 2026-09-05 | 3.526 / 4.082 / 4.328 | 4.709 |
| Detailed-timing baseline, 2026-09-06 | 3.555 / 4.018 / 4.206 | 5.064 |
| Work reduction, 2026-09-06 | 1.059 / 1.252 / 1.288 | 0.000127 |

Exact independently recomputed values and interpolation method are in the
[evidence index](runner-optimization-20260907-evidence.json). The older
approximately 2.4–2.5-second reference discussed by the user was not located as
reproducible source/evidence and is not used as a measured baseline here.

| Detailed-timing baseline → work reduction | Before | After |
|---|---:|---:|
| Formal Main cumulative wall (s) | 427.030 | 125.895 |
| Deleted veth pairs | 23,120 | 23,120 |
| Deletion requests | 23,120 | 15,033 |
| Cumulative deletion critical wall (s) | 323.356 | 57.294 |
| Created pairs | 23,110 | 23,110 |
| Cumulative creation time (s) | 33.363 | 13.006 |
| Total formal netlink requests | 1,577,814 | 1,477,267 |
| Preparation including B0 (s) | 213.374 | 125.216 |
| Finite boundary decode/materialization (s) | 68.175 | 52.692 |
| Finite plan construction, excluding prewrite (s) | 114.946 | 42.445 |
| Semantic prewrite roundtrips (s) | 9.392 | 9.775 |
| B0 physical activation (s) | 5.130 | 3.757 |
| Runner Cleanup (s) | 32.007 | 4.014 |

The 100,547 fewer netlink requests comprise 46,220 standalone MAC requests,
46,240 standalone DOWN requests, and 8,087 deletion requests. New construction
includes 0.747 seconds of one-time wire encoding. New deletion task durations
sum to 443.645 seconds, including overlapping waits; this is neither CPU time
nor boundary wall time. The last completion lag is 1.228868 seconds, including
the last Main itself; maximum start lag over the run is 0.005291 seconds.
Changes were measured together, not as isolated A/B treatments; creation gains
must not be assigned entirely to single-queue configuration.

The target-kernel check on `7.0.0-30-generic` confirmed default allocated RX/TX
32/32 with effective 1/1, versus explicit allocated/effective 1/1. One group
request deleted two UP veth pairs including peers while preserving an unrelated
pair and another group. Its original JSON is included in the archive.

Runner completed B0 plus 120 formal activations through 00:10:00 without an
activation limit; detached tools and scoped cleanup completed. The final run's
generation/deployment/generated-run/deployment-cleanup times were respectively
859.590/517.184/989.286/61.627 seconds; these are not Main timings.

Input snapshots, Compiler history and the Runner binary matched the detailed
timing baseline; Traffic's 19 episode workload definitions matched. Traffic
remained WARN (sent/received 2522/1647 → 2492/1666; total loss 34.69% → 33.15%),
with no reported persistent/infrastructure failure or ring loss. This is not
relabeled PASS. The user explicitly accepted proceeding without further Traffic
investigation; differing actual send counts preclude attributing the loss-rate
change directly to this optimization.

## Evidence preservation and verification

The [compressed original evidence](runner-optimization-20260907-evidence.tar.gz)
contains 75 files from four runs (about 29 MB uncompressed), including complete
Runner event streams/snapshots, available invocation and artifact manifests,
Compiler history, input snapshots, probe plans and compact tool summaries.
Every archived file's original path, byte count and SHA-256 is listed in the
[index](runner-optimization-20260907-evidence.json), together with the archive
checksum and exact runtime invocation IDs. These bytes survive output cleanup
because they are versioned with this record.

The archive is historical evidence, **not a runnable relocated output**. Large
Runner binaries, Docker images, packet telemetry and rendered tool payloads are
not included; their original local outputs remain untouched. Full output is not
claimed to be backed up by this commit. Old source snapshots cannot be recovered
from hashes alone. The commit introducing this record preserves the final code.

Closeout used `skyseed_dev`, Python 3.12.13 at
`/home/lionheart/miniconda3/envs/skyseed_dev/bin/python` (pip 26.1.2).
The focused non-privileged command below passed 126 tests in 4.67 seconds:

```bash
python -m pytest tests/unit/test_runner_runtime_optimization.py \
  tests/unit/test_runner_netlink.py tests/unit/test_runner_artifact.py \
  tests/unit/test_bpf_logical_ports.py tests/unit/test_bpf_runner_contract.py \
  tests/tools/test_runner_tool.py -q -p no:cacheprovider --disable-warnings --tb=short
```

The opt-in live observability fixture was synchronized with retirement groups,
normalized route tuples and timing-v2 phases. Both opt-in integration tests were
collected and skipped with live opt-in disabled (2 skipped in 0.73 seconds).
No live fixture or S3 was rerun for closeout. Earlier live evidence is retained
separately from non-privileged test results. The full documentation gate passed
(84 authoritative documents, 63 Mermaid diagrams); import dependency and
whitespace checks passed. All 75 archived members matched the indexed hashes
and original files. These checks do not replace the original live qualification.
