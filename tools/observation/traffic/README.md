# Traffic observation

The main view is a globe and timeline: select a communication, play/pause/seek, and
select a recorded event for detail. Opening or replaying is read-only. Generation and
sender ownership belong to [Traffic generation](../../generation/traffic/README.md).

The reader consumes fixed workload identities, actual sender records and available
BPF telemetry, with sealed positions for globe geometry. It does not rerun Models or
infer forwarding from planned routes. Packet/communication identities, direction,
times, replies, RTT, timeout/unknown outcomes, sender segments and migrations remain
in events. One aggregate summary stays one event; unknown send counts stay unknown.

Arrows represent only evidence-supported BPF ingress arrivals. Repeated visits remain
visible; unresolved interfaces, missing observations and ambiguous packet associations
remain gaps. An egress decision alone does not prove arrival. Linux sender observations
have no complete intermediate per-packet transit evidence; the page states this limit.
No planned-route conformance, case qualification or coverage score organizes the view.

`traffic-replay.json` and observed Traffic export contain communications and events.
Historical output files and [recorded demos](../../../docs/demos/README.md) are retained
unchanged; their old selection/qualification claims describe those recorded runs only.
Internet Map observations are not automatically packet replay evidence.

Packet association uses actual `ping_observation` replies within the recorded sender
segment. Duplicate replies do not invent sends. Sequence wrap/repeated emission,
overlapping lifetimes and incomplete recording retain uncertainty. A terminal
`reply_sequences` fallback is read only for historical segments without a detailed
observation stream; current senders no longer write those lists.

The public `collect_traffic_replay_data(telemetry, physical_frames, *, workload_path=None,
probe_execution_path=None)` and `write_traffic_replay_tool(telemetry, physical_frames,
directory, *, workload_path=None, probe_execution_path=None)` use only consumed inputs.
An older workload/identity JSON file may be passed as `workload_path`; obsolete planning,
compiler, Runner-event and physical-link arguments are removed. CLI example:

```bash
python -m tools.observation.traffic --experiment OUTPUT \
  --telemetry OUTPUT/runs/INVOCATION/traffic/traffic-telemetry.jsonl \
  --physical-frames OUTPUT/results/physical-world/physical-frames.jsonl \
  --workload OUTPUT/results/traffic/workload.json \
  --probe-execution OUTPUT/runs/INVOCATION/traffic/probe-execution.jsonl \
  --output OUTPUT/visualizations/traffic
```

`--experiment` explicitly locates the completed publication. The CLI pins it once
across reads, as does the aggregate Tool caller; generated `run.sh` uses this entry.
The Python functions accept detached input paths and inherit any enclosing completed
publication context. They never infer an experiment root from unused planning data.
A missing telemetry file is valid for Linux sender-only observation.
