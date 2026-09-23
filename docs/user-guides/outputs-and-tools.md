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
| `results/revisions/<revision>/attempt-NNN/` | Candidate material versions and their own evidence; failed/superseded attempts remain |
| `results/traffic/` | Fixed communication workload; no per-delivery plans |
| `results/experiment/` | Completed-range record, generation status/performance and presentation status |
| `deployment/` | Compose/build inputs, project identity and deployment measurements |
| `runs/<invocation>/runner`, `traffic`, `performance` | Actual execution records; resets have distinct IDs |
| `visualizations/` | Pages and browser data; never the authority for execution |

The root generated README lists controls. Historical experiment paths retain their original meaning.

## Files, records and authored programs

Your scene, Models and host/container programs are authored source. Generation
captures only listed inputs in `inputs/sources/` with `inputs/provenance.json`, plus
effective configuration and optional `inputs/preparation.json`; it does not invent
a container application or install its Python dependencies. Compiler output also
provides `inputs/interaction-client/` (standalone client, requirements and README)
and Compose endpoint variables/mounts. See [host/container connection](computation-and-controls.md#interaction-setup-and-independent-programs).

| Retained location | Authority and use |
|---|---|
| `.control/interaction.json` | Discovery, experiment ID, endpoints, service ownership and stop state; no Model or runtime recovery image |
| `.control/etcd-data/`, `etcd.log`, `etcd-client` | Owned service database/log and link to its native socket directory; external services keep their own database |
| Etcd keys in this experiment namespace | Owner state/history/liveness, request intent/progress/results, user values and small coordination records; access through the client, not by editing database files |
| `.control/revision-proposals/`, `inspections/`, events and lock files | Experiment-owned proposal references, requested inspections, coordination diagnostics and ownership claims |
| `results/compiler/materials.jsonl`, `selection.json` | Original sealed delivery references and the selected version overlay; retained candidates live in `results/revisions/` |
| `results/experiment/completed.json` | Pinned completed calculation publication; subsequent writes do not change an older pinned evidence prefix |
| `runs/<invocation>/`, `runs/latest.json` | Actual invocation identity, Runner/Traffic raw evidence and completion index; selection alone does not prove use |
| `visualizations/` | Derived views of completed evidence, possibly older than the latest owner observation |

Etcd stores control records, not the bulk material/evidence files. Stopping its
service preserves both; restarting makes retained records queryable but does not
restore Models or running resources. Keep the output bundle together. Do not edit
selection files or treat a candidate directory's existence as adoption. Use the
request result and selected manifest/version, then actual Runner load/activation
evidence to establish use. The [precise output contract](../development/experiment-control.md#output-ownership)
and [revision contract](../development/experiment-control.md#revision-and-reusable-continuation)
define ownership and publication order.

Fixed Traffic communications retain their GS/Zone IDs across delivery and revision.
Sender replies/loss and source migration come from actual observations. Missing BPF
packet data and Linux intermediate hops remain unknown; neither a selected route nor
a visualization supplies unrecorded packet evidence. Raw runtime records and
configured workloads are not visualization assets.

## Browse and interpret

- [PhysicalWorld](../../tools/observation/physical_world/README.md): positions and feasible candidates at recorded times.
- [Zone](../../tools/observation/zone/README.md): geography, overlap and membership.
- [Planner](../../tools/observation/planner/README.md): candidate/selected/acquiring/operational distinctions, entities and directional paths; optional S3 Area explanation.
- [Compiler](../../tools/observation/bpf_compiler/README.md): Plan lineage, batches and operation workloads, not proof of kernel activation.
- [Runner](../../tools/observation/runner/README.md): actual preparation, ACTIVE boundaries, holds and cleanup.
- [Traffic generation](../../tools/generation/traffic/README.md): preview fixed GS/Zone identities, direction, interval and intended duration before deployment.
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

Traffic observation uses the shared globe and a simple event timeline. Select a fixed
communication, play/pause/seek, and inspect actual sender or packet observations.
Sealed positions provide geometry; they do not prove forwarding. BPF ingress can support
hop arrows. Linux sender records lack complete intermediate per-packet transit evidence.
Aggregates, missing timestamps and gaps remain as recorded. See
[Traffic evidence semantics](../../tools/observation/traffic/README.md).
