# Physical inspection

[中文](README.zh-CN.md) · **English**

Inspect real [Physical](../../../new_src/skyseed/pc/physical/README.md) results:
position snapshots, per-node candidates, distance, one-way delay, elevation and
custom fields. This optional intermediate-results tool supports development and
research; it does not select connections or generate LinkPlan.

From the repository root, in `skyseed_dev`:

```bash
python -m tools.observation.physical.inspection --output /tmp/skyseed-physical-view
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

Add `--scene path/to/input.scene` for a saved Scene. Use `--position-model sgp4`
for TLE satellites. `--start`, `--steps`, `--step-seconds`, `--max-isl-distance-m`,
`--min-elevation-deg` and `--max-ground-distance-m` control sampling and geometry.
The default demo shares its 64 satellites and three ground stations with
[Scene inspection](../scene/README.md); Physical additionally samples 13 one-minute
states. Scene shows static orbital organization and ground geography together,
with optional orbit-only and ground-only views.
Physical shows actual model results and does not impose schematic orbit lines on
custom trajectories.

`inspection_data(physical, timestamps)` accepts a configured PhysicalLayer with
custom models. `write_inspection(data, output)` writes `physical.json` and embeds
those same facts in the page. Custom fields must be JSON serializable for this
tool; the core does not impose that requirement. Query and write times are reported
separately. The browser does not rerun models or infer unrecorded rejection reasons.

`inspection.py` owns export and CLI; `viewer.html` owns the results view. Shared
page/globe assets are in `../_presentation/`. The globe and its documented
Natural Earth coastlines are display references; model geometry remains authoritative.
