# Traffic generation

This GS ↔ Zone workload Tool selects and schedules traffic from the existing Plan
and Compiler results. `TrafficProbePlanBuilder` seals `results/traffic/probe-plan*.json`
before deployment. Experiment orchestration admits those same files to
`TrafficProbeExecutor`; it owns ordinary source-bound ping processes, migration,
pause/resume, continuation and joined process cleanup. Runner remains the network
and clock authority. No routes are completed or installed by this Tool.

Normal Example navigation includes **Traffic generation preview** after generation,
without Docker, packet telemetry or sending. It shows endpoints/direction, periods,
intervals, estimated packets, planned service/source changes and computed coverage.
Continued computation publishes additional plans and refreshes the preview only
through its completed request. To regenerate just this page:

```bash
python -m tools.generation.traffic examples/Sky00_tiny_network/output
```

The sender starts automatically after Runner preparation, then follows actual
completed ACTIVE frontiers and Runner's adjusted clock. Slow activation does not
advance source ownership prematurely. A blocking migration stop is followed by a
fresh ACTIVE decision. Stop/reset drains sending before Runner resource teardown.

`ExperimentController(traffic_enabled=..., traffic_telemetry_enabled=...)` separates
sending from BPF packet/path collection. Each omitted setting defaults to true for
BPF, false for Linux. Existing BPF Examples retain both ordinary defaults:
`--no-traffic` disables sending; `--no-traffic-telemetry` disables collection;
`--no-tools` disables optional page rendering. Linux scenes can explicitly enable
sending when they provide GS/Zone identities and forward/return arrangements.
Telemetry is BPF-only; explicitly enabling it for Linux rejects during assembly.
[Native S4](../../../examples/Sky06_s4_native_ospf/README.md) uses the same sender
with explicit local GS↔Zone edge arrangements. Native selection checks the GS
uplink, installed attached-GS edge and Zone service assignment; it cannot predict
OSPF transit. BPF selection still requires complete planned forward/return paths.

Native selection retains bounded low/high great-circle distances between GSs and
Zone representative points, using the existing PhysicalWorld facts only for Linux.
Distances are cached only for coverage encountered in the current delivery scan;
the temporary cache is released afterwards. Selected-path reconstruction shares
one current GS return-edge set per native boundary. The Tool considers
only independently arranged coverage with a served Zone and actual access edges
at some boundary; later withdrawals and unserved intervals remain explicit.
An eight-probe allowance selects up to two near and two far pairs in both
directions. Far is relative to eligible coverage, not an intercontinental promise.
Ties are deterministic; equal distances or a sole candidate receive separate
labels. Without geographic facts, covered pairs remain eligible but their
separation is unavailable. No candidates produces an explicitly empty workload.

For local native arrangements, Tool-owned plan format 4 writes transit as `expected_reachable: null`,
`path_status: "unknown-native-transit"`, an empty `path_node_ids` and null hop count.
Missing local edges or an unserved Zone remain explicitly unavailable. Identity
path length/stability are `unknown`; estimated packet-path telemetry is zero.
Geographic distance and relative class have separate fields and preview columns;
they never substitute for path/hop evidence. Per-delivery labels remain attached
to that delivery's preview rows. Readers retain format-2/3 plans without inventing
geographic labels. This private workload artifact does not change
Plan, Compiler material, Runner ownership or route outcomes. Native persistent
flows are bounded by the same configured episode, concurrency and packet budgets;
per-Shell transit-path coverage is not inferred from endpoint membership.

[Traffic observation](../../observation/traffic/README.md) reads actual records.
Opening either page never starts traffic. Planned counts and coverage are not
measurements. This is not an arbitrary-node traffic API; use container ping for
other explicit destinations.
