# Adopted mechanisms and their tradeoffs

The implementation reduces repeated work while keeping formal state and evidence explicit.
These mechanisms are defaults and backend choices, not universal requirements for custom Models.
The [decision history](../history/optimization-decision-history.md) records dated evidence;
its measurements do not establish general hardware requirements.

| Mechanism | Reason and cost |
|---|---|
| Bounded physical geometry tiles and contact proofs | Reuse orbit/site geometry and avoid allocating rejected candidates. Conservative proofs can leave future contact unknown; prediction is optional. |
| Retained sparse GS assignment | Start from valid prior reservations, then solve the exact admitted-graph objectives when reuse cannot be certified. Tie choices can affect later continuity. |
| Reverse BFS by destination and bounded route caches/workers | Share searches across current demands and reuse unaffected dependencies. Cache/IPC memory has a cost; custom routes run each step unless they opt into reuse. |
| Accumulated delay deadband | Reduce parameter churn while tracking change from the last adopted value. Threshold and refresh cadence are explicit simulation policy. |
| Bounded Plan and packaging queues | Retain chronological ownership while overlapping serialization. Flush can change Plan grouping without skipping formal transitions. |
| Incremental hashes and compressed evidence | Publish completed byte prefixes without rereading or copying history. Readers must honor one completion record and stream format. |
| Compiler precedence folding and predecessor indexes | Avoid expanded redundant edges and rebuilding unchanged lowering state; unsupported ordering still retains exact constraints. |
| Prepared deltas and future forwarding generations | Move decoding and semantic prewrite outside formal Execution. Fixed capacity and retained material consume memory; unknown future demands can exceed admission. |
| Retained namespace sockets and grouped deletion | Avoid per-action subprocess overhead and overlap independent namespace work. Each current boundary still waits for its physical withdrawals; cleanup responsibility is preserved. |
| Reader/reference-safe version sharing | Reuse equal node content, reclaim only retired versions with no readers/references. Monotonic identities and accounting add complexity. |
| Shared SEED build contexts and grouped Compose operations | Avoid rebuilding identical content while preserving deployment identity and dependencies. Startup/teardown remain explicit Deployer operations. |

There are three independent batching choices: formal observation cadence, Planner publication
frames, and Compiler delivery duration. A five-second cadence with 32-frame Plans can use
300-second deliveries; neither batching choice permits dropping timestamps. A retained request
adds a finite suffix and waits again. Tools refresh at completed requests, with at most one
pending newer range; no full history scan is added to internal delivery batches.

Performance evidence distinguishes accumulated CPU/work durations, overlapping wall spans,
intended pacing and measured resource scopes. A summed stage duration is not automatically
an end-to-end elapsed time. Historical host changes (cgroup driver, D-Bus/inotify limits,
NVMe layout) retain their original experimental conditions in
[history](../history/optimization-decision-history.md#宿主机配置记录).
