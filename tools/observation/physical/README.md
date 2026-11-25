# Physical inspection

[中文](README.zh-CN.md) · **English**

Inspect real [Physical](../../../src/skyseed/pc/physical/README.md) results:
position snapshots, per-node candidates, distance, one-way delay, elevation and
custom fields. This optional intermediate-results tool supports development and
research; it does not select connections or generate LinkPlan.

Read saved observations without running a Physical model:

```python
from tools.observation.plan_views import write_physical
write_physical("output/observations/plans.json", "output/views/physical")
```

Open the standalone `index.html` offline. The default overview shows all nodes
and all groups' candidates together. Filtering query nodes by constellation,
shell, orbit or ground stations narrows the selector without hiding the other
groups. Choose a node and change the time. Click a
faint candidate line or a peer in the table to highlight it and inspect its
properties/custom fields below the globe. A selected peer that disappears at a
later time is explicitly marked as no longer a candidate. “Focus query” isolates
the query node and its peers; “Overview” restores all nodes and candidates.
Uncheck “All candidates” to keep all nodes while showing only the selected query's
candidates. Group filtering never drops peers from the actual reports.
Opposite reports are merged only for drawing lines; missing values stay unknown.

Physical shows actual sampled model results; it does not impose schematic
orbit lines on custom trajectories.

`inspection_data(physical, timestamps)` accepts a configured PhysicalLayer with
custom models. `write_inspection(data, output)` writes `physical.json` and embeds
those same facts in the page. Custom fields must be JSON serializable for this
tool; the core does not impose that requirement. This is a calculation-side export;
`write_physical` only reads the saved context. The browser does not rerun models or infer unrecorded rejection reasons.

`inspection.py` owns sampling export and page generation; `viewer.html` owns the results view. Shared
page/globe assets are in `../_presentation/`. The globe and its documented
Natural Earth coastlines are display references; model geometry remains authoritative.
