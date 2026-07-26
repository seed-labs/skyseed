# Inspection Tools

Tools are repository-local read-only views, not installed product APIs.

| Tool | Input | Output subdirectory |
|---|---|---|
| Topology | one created `Topology` | `inspection/topology/` |
| Zone | the same `Topology` | `inspection/zone/` |
| Dynamic Slice | one exact `Slicer` with retained slices | `inspection/dynamic-slice/` |

The normal imports are:

```python
from tools.topology import write_topology_tool
from tools.zone import write_zone_tool
from tools.dynamic_slice import write_dynamic_slice_tool
```

The output contains standalone HTML, private JSON audit data, and summaries.
It is non-authoritative: formal Zone membership/resolution, path lookup, and
Difference creation are read from production objects. Tools never execute
Models or create fallback results.

Run the rich bundle:

```bash
python examples/dynamic_slice_network/main.py
```

Validate the generated Dynamic Slice page in Firefox without keeping a
screenshot:

```bash
python scripts/check_dynamic_slice_tool_browser.py \
  examples/dynamic_slice_network/output/inspection/dynamic-slice/index.html
```

Browser smoke validates the UI only and never enters the Tool's formal object
read chain.

Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
