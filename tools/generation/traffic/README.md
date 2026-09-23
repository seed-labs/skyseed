# Traffic generation

Traffic selects fixed GS/Zone communications from stable PhysicalWorld identities.
The deterministic default selects at most two GS/Zone pairs in both directions.
Explicit pairs can include a network scope when a Zone is served by multiple shells.
No RouteModel result, complete path, predicted reachability, coverage score or route
stability participates in selection. `results/traffic/workload.json` is written once;
network production, delivery sealing and revision do not wait for Traffic planning.

```python
controller = ExperimentController(network, simulation, output, entry_path=entry,
    traffic_enabled=True,
    traffic_options={"pairs": [("65001/ground-station/gs-new-york", "zone-id")],
                     "direction": "both", "interval_seconds": 2,
                     "duration_seconds": 300})
# Scoped pair form: (ground_station_id, network_scope, zone_id).
```

Options are `pairs`, `direction` (`both`, `ground-to-zone`, `zone-to-ground`),
`interval_seconds`, and `duration_seconds`. Defaults use the initial requested
simulation duration plus one cadence; set intended application duration explicitly
for longer retained experiments. `python -m tools.generation.traffic OUTPUT` creates
a read-only configuration preview without calculating route suitability.

`TrafficExecutor` starts ordinary source-bound ping senders after readiness and formal
clock start. Logical communications persist across deliveries and Feedback revisions.
Runner's `communication_endpoints()` supplies actual bindings, stable addresses and
completed ACTIVE Zone assignment. Prepared/candidate moves never relocate a sender.
A topology pause keeps a sender with a valid source running; intended duration uses
wall time. Missing source is recorded, selection retained, and sending resumes when
a source exists. Source migration drains the old sender and rereads ACTIVE facts
before launching its replacement. Stop/reset drains owned senders before Runner
resources are released. Aggregate totals and missing replies remain actual evidence,
not synthetic packet/hop events.

Sender evidence format v3 streams each `ping_observation` (sequence, RTT, duplicate
marker, time and segment). Live sender state keeps only counters and online RTT
count/min/max/mean, not duration-sized sequence sets or RTT lists. Terminal records
contain these bounded observation summaries; unique `sent`/`received`/`loss` require
ping's terminal summary and remain null when that summary is missing. Recorded
observations alone do not establish complete send or unique-receive counts.

Sending and BPF telemetry remain separate flags (`traffic_enabled`,
`traffic_telemetry_enabled`). The BPF defaults enable both; Linux requires explicit
sending and rejects BPF telemetry. Ordinary node applications are still author-owned.
[Observation](../../observation/traffic/README.md) reads recorded events only.

For live status meanings and a copyable per-communication evidence inspection, see
[the Tiny smoke workflow](../../../docs/user-guides/getting-started.md#installation-smoke-test-tiny).
Runtime coverage, Traffic completion and owner/resource drainage are separate checks.
