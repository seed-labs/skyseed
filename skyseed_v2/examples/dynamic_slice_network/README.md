# Dynamic Slice Network Reference

This richer reference is separate from the minimal
`complete_static_emulator` canonical example. It is the comprehensive G
reference: one YAML-built Emulator owns the Skyseed constellation, static
GroundStation/IX attachment, ordinary SEED network and routing/eBGP layers;
that exact Emulator is both compiled into a static Docker/Compose project and
projected into the accepted A–F Dynamic Slice chain.

The explicit scale is one AS, one Shell, four Orbits, eight Satellites per
Orbit, three GroundStations, five Zones and twenty real Slices plus synthetic
Slice 0. `scenario.yaml` also owns IX 110, ordinary AS 65110,
`service-router`, `service-net`, three GroundStation IX attachments and one
explicit eBGP peering. `zones.yaml` owns the static Zone layout. `simu.py`
explicitly records:

- Timeline start `2026-01-01T00:00:00Z`, interval `120 s`, twenty instants;
- maximum ISL distance `6,000,000 m`;
- GroundStation minimum elevation `5°`, maximum two access Satellites and
  maximum distance `3,000,000 m`;
- Zone representative-point minimum elevation `10°`;
- ISL/access bandwidth `1,000,000,000 / 500,000,000 bps`;
- ISL/access loss `100 / 200 ppm`;
- propagation speed `299,792,458 m/s`.

The three GroundStation coordinates are copied into this v2 YAML from the
historical/reference v1 catalog
`skyseed_v1/data/ground/starlink_gateways.json`, specifically the Porto
Alegre, Marsala and Yamaguchi records selected by
`s4_visualization_gateway_binding_12.json` and read by the offline
`examples/reference/V1StaticInputAdapter`. The v1 repository provides names,
city labels, latitude, longitude and zero-metre reference altitude, but does
not document an external source URL or establish that these are currently
operational sites. This example therefore treats them as public/reference
locations, copies the values rather than reading v1 at runtime, and gives
GroundStations no static Zone membership.

`north-america-east` (priority 10) contains the smaller
`northeast-priority` rectangle (priority 20). Their positive-area overlap is
latitude `38°..46°`, longitude `-80°..-68°`; a probe at `(42°, -74°)` has two
physical memberships and resolves authoritatively to `northeast-priority`.
Both representative points remain inside their own rectangles.

## Comprehensive Emu + Simu run

Run the formal comprehensive entry from the repository root:

```bash
python3 -m examples.dynamic_slice_network.main \
  --internet-map-source /path/to/seed-emulator/tools/InternetMap2
```

Or directly from this directory:

```bash
python3 main.py \
  --internet-map-source /path/to/seed-emulator/tools/InternetMap2
```

The explicit source path selects an InternetMap artifact containing the
none-network backend normalization without assuming a checkout layout. Pass
`--no-internet-map` when the map is intentionally omitted.

The output is one user-facing scenario root with a runnable static environment
and isolated read-only inspection bundles:

```text
output/
├── start.sh
├── cleanup.sh
├── runtime/
│   ├── docker-compose.yml
│   └── per-node build contexts
└── inspection/
    ├── topology/
    │   ├── index.html
    │   ├── topology.json
    │   └── summary.txt
    ├── zone/
    │   ├── index.html
    │   ├── probes.json
    │   └── summary.txt
    └── dynamic-slice/
        ├── index.html
        ├── dynamic-slice.json
        ├── summary.txt
        ├── inventory.json
        └── checksums.sha256
```

`main.py` constructs the Emulator exactly once, renders and compiles it, adds
project-scoped controls, projects SimulatorStaticInput from that same
Emulator's upstream Base owner, creates one Topology and one Slicer, publishes
every formal Slice, then gives that exact Topology to the Topology and Zone
Tools and that exact Slicer to the Dynamic Slice Tool. `start.sh` validates
`runtime/docker-compose.yml` before starting:

```bash
./output/start.sh
```

`output/runtime/` is the current **static** container network.
`output/inspection/` contains only read-only views. Simulator
Position, Connection, Zone service, Route and Difference results do not
update those containers. The future Planner/backend/runtime boundary is
responsible for translating accepted dynamic results into runtime operations.

`main.py` is the only executable assembly entry. `simu.py` has no CLI and
never constructs an Emulator or writes Tool output; its
`create_dynamic_reference_slicer(emulator, ...)` function only projects an
explicitly supplied existing Emulator into the formal Simulator/Slicer chain.

The example never edits a Slice, constructs a Connection/Route/Difference
manually, or bypasses the three Models and Slicer. Inventory, observed
behavior, Difference and resolved-path summaries are produced only by the
Dynamic Tool projector from the supplied Slicer. It emits non-authoritative
JSON/HTML view data and `summary.txt`; the example contains no second
reporting path.

## Observed deterministic behavior

The accepted Models naturally produce the following current evidence without
editing any result:

- Slice 1 has 32 same-orbit and 16 cross-orbit ISLs;
- all three GroundStations receive access during the bounded run;
- every real Slice has at least one served Zone;
- all 19 post-initial Differences are non-empty;
- later Differences contain 42 Connection additions, 42 removals and 690
  directional parameter patches;
- Zone assignments exercise ADDED, CHANGED and REMOVED;
- Route patches exercise ADDED, CHANGED and REMOVED;
- the longest resolved path contains seven hops.

These are reference-scenario observations, not new algorithm guarantees.

## Five-view offline Tool

The generated directory contains exactly:

- `index.html`: directly openable interactive page with embedded data;
- `dynamic-slice.json`: private structured view data;
- `summary.txt`: stable counts and observed behavior;
- `inventory.json`: byte sizes and SHA-256 for the three payload files;
- `checksums.sha256`: hashes for all payload and inventory files.

The timeline includes synthetic Slice 0 and all twenty real Slices. Five
separate modes share it:

1. Topology/Orbit: Orbit guides and static Orbit metadata, with optional
   GroundStation and Zone layers;
2. ISL: same/cross-orbit links plus Orbit and local-node filters;
3. Ground/Zone: GroundStation access and served/unserved Zone relations;
4. Routing: one selected source/G-or-Z target and its formal resolved path;
5. Difference: one selected patch category at a time.

Details use tables and lists. Raw JSON is only a collapsed audit view.

## Manual review

1. Open `output/inspection/topology/index.html` and inspect the static hierarchy.
2. Open `output/inspection/zone/index.html` and inspect membership probes.
3. Open `output/inspection/dynamic-slice/index.html`.
4. Confirm Slice 0 shows no timestamp or Position.
5. Move the slider through several real Slices and use Previous/Next.
6. Start and pause playback.
7. Inspect Orbit metadata and select/highlight individual Orbits.
8. In ISL view, filter by Orbit and select a Satellite's local adjacency.
9. In Ground/Zone view, inspect GroundStation access and served/unserved Zones.
10. Select several Routing G/Z targets and verify the structured path table.
11. Inspect one Difference category at a time and focus individual patches.
12. Confirm the accepted A–G behavior or record any regression found while
   reviewing the external audit bundle.
