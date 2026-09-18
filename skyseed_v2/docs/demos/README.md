# Interactive demonstrations

Open [index.html](index.html) directly in a browser. The HTML pages and relative
JavaScript data are self-contained; opening them starts no experiment or traffic.
The browser may take a moment to load a selected frame or route shard.

- **S5:** 172 satellites, 10 GSs and 184 Zones; 121 five-second formal boundaries
  from 2026-01-01 00:00:00 through 00:10:00 UTC. The custom current-geometry and
  greedy ISL/GS Models are [example-local](../../examples/Sky01_s5_custom_models/README.md).
  PhysicalWorld, Zone, Planner, Compiler, Runner, Traffic and performance pages share
  this bounded interval. Runtime records are the actual ME2 invocation
  `20260917T121036.490317822Z-681081`, conducted on 2026-09-17.
- **S3:** the first complete published Plan, 00:00:00–00:02:35 UTC on 2026-01-01,
  with 32 five-second boundaries. This supplements S5 with distinctive Area/ABR
  browsing for 720 satellites. It contains calculated routes, not live OSPF evidence.

Try PhysicalWorld's time slider and Shell/entity selection. In Planner, choose a
stage/entity, seek time and select a route demand; S3 adds Area and cost explanation.
Compiler lets you inspect activation work. Runner reports observed activity; Traffic
replays probes and recorded paths. Performance switches between separate generation
and runtime time bases. Each page explains its encodings and unavailable values.

[provenance.json](provenance.json) records exact retained source paths, hashes and
covered intervals. The original records are under
`skyseed-local/experiments/me2-20260917/current-results/`, located via the prior
phase's retention manifest. [Original measurements](../history/me2-examples.md)
retain their actual conditions. These pages are historical demonstrations, not
validation of current live behavior.

[regenerate.py](regenerate.py) uses the existing environment and current detached
Tools. It copies retained records into temporary current-layout envelopes, separates
the candidate timestamp index, and adjusts relative metadata references. S3 selects
one whole existing Plan and its matching frame prefix; no new Plan values are invented.
S5 materializes static world facts from unchanged example inputs; stored snapshots
supply every timed position. No Models advance and no Docker/BPF experiment runs.
Only browser files are retained here; raw histories and executable materials are
not duplicated as a second authoritative archive.

```bash
python docs/demos/regenerate.py --records /path/to/me2-20260917/current-results
```

Run `python scripts/check_demo_browser.py docs/demos --evidence /path/to/evidence`
with the existing Firefox/geckodriver installation. If this environment uses a proxy,
exclude `127.0.0.1,localhost` through `NO_PROXY` for the local driver.

Portability verification copies this folder away from original outputs, checks
relative links/assets, then exercises time, entity, route, Area and source selection
in Firefox. Screenshots and render evidence are implementation checks, not user
visual acceptance. The regeneration command requires retained inputs; browsing does not.
