# Shared runtime inputs

`starlink/` contains the existing Starlink Gen1 orbit and ground-station source
files, retained byte-for-byte through the directory migration. Prepared entries
read Shells and GroundStations separately through `load_shell_configs` and
`load_ground_station_configs`, then select Shells/filter GSs before construction.
The existing aggregate `starlink/scenario.yaml` remains supported for other callers.

- [shells.yaml](starlink/shells.yaml): all five Shells and 4,408 satellites from the
  reviewed [FCC Schedule S records](../research/starlink/gen1/shells/fcc-sat-mod-20200417-00037-gen1.json).
  The [S1–S5 mapping](../research/starlink/gen1/shells/S1-S5-MAPPING.md) explains
  Shell identity, selected planes and phase interpretation. Per-satellite ISL
  capacity 4 and GS capacity 2 remain explicit simulation assumptions.
- [ground_stations.yaml](starlink/ground_stations.yaml): 355 retained sites from
  the reviewed [gateway evidence](../research/starlink/gen1/ground-stations/gateway-evidence-2026-08-25.json),
  with the original [provenance](../research/starlink/gen1/ground-stations/PROVENANCE.md).
  `data/starlink/rebuild_frozen_ground_stations.py` verifies that exact research
  source before rebuilding this frozen file. Coordinates do not establish current
  commercial operation or hardware capacities.
- [global_zones.yaml](global_zones.yaml): the preserved deterministic 732-Zone
  file, usable for explicit file input. Default assembly creates the equivalent
  24-band, 48-equatorial-cell, antimeridian-centered layout directly with
  `build_global_zone_layout()`; no intermediate file is required.

Tiny retains its local Zones; S5 loads its authored 184-Zone global grid. Normal outputs and historical
qualification files remain separate evidence with their original source identities.
Runtime never loads the research directory.

From ``, verify the frozen GS file without refreshing it:

```bash
python data/starlink/rebuild_frozen_ground_stations.py --check \
  --research-source research/starlink/gen1/ground-stations/gateway-evidence-2026-08-25.json
```

The utility resolves its default output from its own location. Absolute source
and utility paths work from another working directory. `--write` is an explicit
rebuild operation; normal examples only read these files. The historical generator
banner and schema versions remain unchanged. Original external evidence belongs
in `research/`, separately from runtime inputs.
