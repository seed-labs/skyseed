# Inspection Tools

Tools are repository-local read-only views, not installed product APIs.

| Tool | Input | Output subdirectory |
|---|---|---|
| Topology | one created `Topology` | `inspection/topology/` |
| Zone | the same `Topology` | `inspection/zone/` |
| Dynamic Slice | one exact `Slicer` with retained slices | `inspection/dynamic-slice/` |
| Planner | that exact `Slicer`, consecutive `Plan` publications, retained `PlanningDecision` values | `inspection/planner/` |

The normal imports are:

```python
from tools.topology import write_topology_tool
from tools.zone import write_zone_tool
from tools.dynamic_slice import write_dynamic_slice_tool
from tools.planner import write_planner_tool
```

The output contains standalone HTML, private JSON audit data, and summaries.
It is non-authoritative: formal Zone membership/resolution, path lookup, and
Difference creation are read from production objects. Tools never execute
Models or create fallback results.

Run the rich bundle:

```bash
python examples/mini_satellite_internet/main.py
```

Validate the generated Dynamic Slice page in Firefox without keeping a
screenshot:

```bash
python scripts/check_dynamic_slice_tool_browser.py \
  examples/mini_satellite_internet/output/inspection/dynamic-slice/index.html
```

Browser smoke validates the UI only and never enters the Tool's formal object
read chain.

Run the real complex Planner inspection from the repository root with one
entry point:

```bash
python -m examples.mini_satellite_internet.main
```

Open `examples/mini_satellite_internet/output/inspection/planner/index.html`.
The page shows sampled Slicer facts beside actual published transitions,
Planner coverage and temporal groups, semantic forwarding precedence, and
retained decisions. It does not infer rejected suppression candidates or an
expected Plan.

Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
