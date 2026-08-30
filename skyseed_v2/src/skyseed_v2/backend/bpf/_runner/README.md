# BpfRunner private implementation

This directory is implementation grouping under the public `BpfRunner` owner.
It is not a public package, fourth Runner phase, dataplane plugin, telemetry
subsystem, runtime authority, or second lifecycle. Preparation, Main, and
Maintenance remain capabilities of the one `BpfRunner` implemented in
`../runner.py`.

## File responsibilities

| File | Responsibility | Execution boundary |
|---|---|---|
| `runtime_realization.py` | Maintain the private tc-BPF realization state shared across Runner Preparation, Main, Maintenance, reclaim, and teardown; retain stable interface identity with owned resource/container incarnation; group boundary work into concrete privileged requests; clear inventory only after successful teardown evidence. | Long-lived object in the ordinary BpfRunner process. |
| `privileged_runtime_command.py` | Execute one requested Linux/tc-BPF operation, batch by node/namespace/generation ownership, run independent node batches through a bounded local worker pool, and return operation/crossing metrics; includes the private raw BPF loader. | One-shot command inside the Runner-owned privileged helper container. Its worker pool ends with that command and is not a daemon or scheduling authority. |
| `event_history.py` | Accept compact lifecycle records through a bounded nonblocking queue and perform JSON materialization/write/flush on one detached writer thread; report overflow, failure, and completeness explicitly. | Private ordinary-process evidence capability owned and bounded by BpfRunner. It cannot affect network state. |
| `traffic_telemetry_collector.py` | Poll the pinned shared ring buffer, decode fixed events, report bounded loss/final-drain evidence, and obey the recorder's stop request. | Long-lived command inside the same privileged helper container, supervised by BpfRunner through the ordinary-process recorder. |
| `traffic_telemetry.py` | Supervise the collector process, write node/interface/clock/packet/loss records incrementally, preserve partial evidence, and close the ordinary-user durable JSONL after final drain. | Long-lived private object/thread in the ordinary BpfRunner process; it does not decide forwarding or scheduling. |
| `traffic_telemetry_abi.py` | Define and decode the fixed private binary event layout shared by the tc-BPF producer and collector. | Imported on both sides of the process/privilege boundary; no public ABI/export. |

`../runner.py` owns the helper-container lifecycle and all calls into these
files. The privileged command and collector can mutate/read kernel state only
within requests supervised by that owner; neither may publish READY/ACTIVE,
advance input, schedule Main/Maintenance, or outlive successful Runner teardown.

## Process and privilege flow

```text
ordinary BpfRunner process
  → private runtime realization builds a scoped request
  → Runner-owned privileged helper container
      → one-shot privileged_runtime_command for kernel mutations/reads

ordinary BpfRunner process
  → ordinary traffic recorder supervises
  → long-lived collector in the same privileged helper
      → reads pinned ring/loss maps
      → emits detached records to the recorder
  → recorder persists traffic-telemetry.jsonl
```

Telemetry failure remains non-authoritative and cannot change the already
selected forwarding action. The collector is long-lived only because ring
consumption spans the run; this does not make it a Runner phase or lifecycle
owner.
