# Skyseed v2 Offline Tools

This regular repository-level package contains private, offline Topology,
Zone and Dynamic Slice inspection pages.

The tools read an already-created public `Topology`. They do not create or
modify Emulator/Simulator objects, participate in formal computation, provide
fallback results, enter the installed wheel, or add public `skyseed_v2`
exports. Their JSON files use a private tooling `format_version` only.

The Dynamic Slice Tool instead receives one exact public `Slicer`. It reads
its exact Topology and retained TopologySlices, asks that Slicer for formal
resolved paths and adjacent TopologyDifferences, and writes a
non-authoritative view bundle. It never imports or invokes a Model, propagates
Position, forms a Connection, computes a Route or compares Slices itself.

Run the canonical example from `examples/complete_static_emulator/`:

```bash
python main.py
```

The generated standalone pages are under the selected output directory's
`inspection/` subtree and require no server or network access.

Run the comprehensive richer reference:

```bash
python examples/dynamic_slice_network/main.py
```

Its standalone Dynamic Slice page is
`examples/dynamic_slice_network/output/inspection/dynamic-slice/index.html`.
The same run also writes the static Topology and Zone pages at
`examples/dynamic_slice_network/output/inspection/topology/index.html` and
`examples/dynamic_slice_network/output/inspection/zone/index.html`. All three
pages embed their data and have no remote runtime dependency.
`dynamic_slice_network/main.py` is the only executable assembly entry;
`simu.py` only projects an explicitly supplied existing Emulator.

The Topology page combines the existing 2D GroundStation map with a
display-only unit-sphere view of static orbit planes and GroundStation
coordinates. It does not propagate Satellites or place them from
`mean_anomaly`.

The Zone page embeds every generated probe in both its JSON artifact and
standalone HTML. Its initial focus view keeps representative centers,
failures, multi-matches, relation probes, pole probes, dateline probes, and a
small deterministic sample per Zone visible; the complete set remains
available through the all-probes view. The 2D map and unit-sphere globe share
the same client-side filters and never re-run formal APIs while switching
views.
