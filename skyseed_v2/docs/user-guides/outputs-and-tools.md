# Find results and use Tools

Open the example's `output/index.html`, then its visualization index. The same browser pages
are reachable at `output/visualizations/index.html`. They are generated from completed facts;
calculation may remain alive and waiting. A later completed request schedules a refresh. Pages
use relative local JavaScript/data files, so keep the bundle together when copying it.

| Directory | What to look for |
|---|---|
| `inputs/` | Captured authored sources, provenance and effective Simulator configuration |
| `results/physical-world/` | Formal ECEF frames and candidate evidence |
| `results/planner/` | Published planning history |
| `results/compiler/` | Compiler history, Runner artifact directories, execution definition and material references |
| `results/traffic/` | Probe input plans, including continued deliveries |
| `results/experiment/` | Completed-range record, generation status/performance and presentation status |
| `deployment/` | Compose/build inputs, project identity and deployment measurements |
| `runs/<invocation>/runner`, `traffic`, `performance` | Actual execution records; resets have distinct IDs |
| `visualizations/` | Pages and browser data; never the authority for execution |

The root guide lists controls. `.control/` is internal staging/events. Raw runtime records
and probe plans are not visualization assets. Materials have one authoritative location;
there are no old-layout aliases. Historical experiment paths retain their original meaning.

## Browse and interpret

- [PhysicalWorld](../../tools/observation/physical_world/README.md): positions and feasible candidates at recorded times.
- [Zone](../../tools/observation/zone/README.md): geography, overlap and membership.
- [Planner](../../tools/observation/planner/README.md): candidate/selected/acquiring/operational distinctions, entities and directional paths; optional S3 Area explanation.
- [Compiler](../../tools/observation/bpf_compiler/README.md): Plan lineage, batches and operation workloads, not proof of kernel activation.
- [Runner](../../tools/observation/runner/README.md): actual preparation, ACTIVE boundaries, holds and cleanup.
- [Traffic generation](../../tools/generation/traffic/README.md): preview scheduled GS/Zone endpoints, intervals, estimated counts and coverage before deployment.
- [Traffic observation](../../tools/observation/traffic/README.md): actual sender records, available packet/path evidence and replay.
- [Performance](../../tools/observation/performance/README.md): recorded component timings, recorder origins and resource scopes.

[Interactive demos](../demos/index.html) preserve real generated bundles and state their
scenario, interval and provenance. Historical runtime demos are not current-code qualification.

Compiler and Runner pages identify BPF or Linux from recorded materials. Native Runner
shows ordered activation and route/link operation facts; missing measurements stay
unavailable and BPF-only measurements are not applicable. Intended routes are distinct
from installed protocol routes and observed packet paths. Empty or partial RouteModel
coverage does not establish Linux unreachability. Older standalone evidence without a
backend identity is labelled as not recorded.

Traffic generation supports GS/Zone scenes on either backend and defaults off for Linux.
`traffic_enabled=True` explicitly enables it for a compatible native scene.
[Native S4](../../examples/Sky06_s4_native_ospf/README.md) uses explicit GS↔Zone edge
coverage, an offline preview and automatic sending. Its unknown transit path is
kept separate from actual replies/loss and source-migration records. BPF telemetry
is independently controlled by `traffic_telemetry_enabled`; absent packet data is unavailable. In a Linux
node container, you can use ordinary installed network utilities such as `ip route get`
and `ping` to inspect a lookup or test a destination. Delivery still requires suitable
addresses, interfaces, forwarding and forward/return routes; a missing precomputed
outcome neither supplies those routes nor rules out other Linux routes. These manual
checks do not create SkySEED Traffic telemetry or a path replay.

## Regenerate views without computing again

During retained use, each example passes `tools.write_experiment_tools` as optional completed
range side work. For an already completed output and its original physical World:

```python
from tools import write_experiment_tools
write_experiment_tools(output, physical_world=simulation.get_physical_world())
```

This reads the latest completed record. The World supplies static identity/geometry; Tools
read recorded positions and Plans and do not run Models. Individual Tool CLIs accept their
record paths; use `python -m tools.observation.runner --help` or `python -m tools.observation.traffic --help` for runtime
records. A partial-cutoff output only has views supported by that cutoff. No runtime records
means no claim of actual Runner/Traffic behavior. A failed presentation retains domain results
and reports failure separately; retry rendering after correcting the optional Tool problem.
Runtime views become available after the run publishes its completion record.

Tool code is separated into [generation](../../tools/generation/traffic/README.md), [observation](../../tools/observation/) and [development](../../tools/development/README.md). Observation commands now use `python -m tools.observation.…`; the simple `tools.write_experiment_tools` Example entry is unchanged. Development checks are separately invoked Python scripts and are never started by viewing a page. Local route arrangements display their facts or explicit unknown reachability without inventing paths.

## Live Internet Map

When enabled, deployment builds InternetMap2 from the installed SEED source and exposes
[localhost:8080](http://localhost:8080). Applied dynamic connections and the recorded
terminal/capture behavior require the separately adapted local Map checkout, not only
the ExtensionNode-capable dependency. `--no-internet-map` omits it. The normal
Map combines Docker ground networks with Runner's acknowledged dynamic connection
changes. An optional bounded observer publishes current state, never future
prepared resources or a replay of planned timestamps. Unavailable observation is
labelled; it does not control forwarding. See the [native example](../../examples/Sky06_s4_native_ospf/README.md#observe-the-network)
for filtered capture, node terminals, ping and tcpdump. These instructions also
apply to BPF, whose fixed local veth is never displayed as an inter-node edge.

Native Traffic observation includes the shared globe without BPF telemetry. Its
positions, Zone services and dynamic connections are planned context aligned to
recorded runtime ACTIVE progression and holds. Communication selection, seeking
and playback retain sender migration/reply evidence; endpoint relationships do not
claim observed native transit paths. See [native globe semantics](../../tools/observation/traffic/README.md#native-sender-globe).
The opening notice and legend explicitly say no intermediate packet hops were recorded;
the endpoint line is a communication relationship. Top links expose sender totals/flows,
timed observations and raw execution records. Live Internet Map observations do not
automatically become Traffic replay evidence. BPF retains its existing observed-hop replay.
