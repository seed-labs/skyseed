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

- [PhysicalWorld](../../tools/physical_world/README.md): positions and feasible candidates at recorded times.
- [Zone](../../tools/zone/README.md): geography, overlap and membership.
- [Planner](../../tools/planner/README.md): candidate/selected/acquiring/operational distinctions, entities and directional paths; optional S3 Area explanation.
- [Compiler](../../tools/bpf_compiler/README.md): Plan lineage, batches and operation workloads, not proof of kernel activation.
- [Runner](../../tools/runner/README.md): actual preparation, ACTIVE boundaries, holds and cleanup.
- [Traffic](../../tools/traffic/README.md): actual probes/paths and expected-unreachable versus unexplained loss.
- [Performance](../../tools/performance/README.md): recorded component timings, recorder origins and resource scopes.

[Interactive demos](../demos/index.html) preserve real generated bundles and state their
scenario, interval and provenance. Historical runtime demos are not current-code qualification.

## Regenerate views without computing again

During retained use, each example passes `tools.write_experiment_tools` as optional completed
range side work. For an already completed output and its original physical World:

```python
from tools import write_experiment_tools
write_experiment_tools(output, physical_world=simulation.get_physical_world())
```

This reads the latest completed record. The World supplies static identity/geometry; Tools
read recorded positions and Plans and do not run Models. Individual Tool CLIs accept their
record paths; use `python -m tools.runner --help` or `python -m tools.traffic --help` for runtime
records. A partial-cutoff output only has views supported by that cutoff. No runtime records
means no claim of actual Runner/Traffic behavior. A failed presentation retains domain results
and reports failure separately; retry rendering after correcting the optional Tool problem.
Runtime views become available after the run publishes its completion record.
