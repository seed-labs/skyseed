# Data sources for visualization developers

[中文](README.zh-CN.md)

SkySEED uses the Scene to describe satellites and ground stations (GS), Docker to locate their containers, and Runner execution records to update the live topology. The Scene's `node_id` joins these sources.

| What the frontend displays | Source | Identifier |
|---|---|---|
| Container name, status and terminal | Docker Engine API | Read `node_id` from the container label and open the terminal by Docker `Id` |
| Satellites, orbital groups and GS locations | In-memory `Scene` or the saved `.scene` file for the experiment | Each satellite and GS has a `node_id` |
| Active links, satellites serving a Zone, experiment clock | Topology JSON from the runtime map | Link endpoints and serving satellites refer to node IDs |

## Containers

SEED's Docker queries and terminals can be reused. SkySEED sets the label `org.seedsecuritylabs.seedemu.meta.skyseed.scene_id` to the Scene's `node_id`. Its companion `org.seedsecuritylabs.seedemu.meta.skyseed.scene_kind` is either `satellite` or `ground_station`. Docker lists expose labels in `Labels`. The inspect response exposes them in `Config.Labels`.

The map builds this mapping at startup from the Compose project's containers through `Deployment.containers()`. When the user opens a node, the backend inspects that container and runs `ip -j address` if it is running. The frontend can request this information at `GET /api/v1/skyseed/node/{node_id}`, with the node ID URL-encoded. [Deployment lookup](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/src/skyseed/runner/deployment.py) · [Node details endpoint](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/tools/internet_map/app/backend/skyseed.js)

## Satellites and ground stations

A visualization can take an existing `Scene` or restore its saved state with `Scene.load(...)`. Both expose the same nodes, identifiers, memberships and initial position inputs as of the save. Python consumers use `scene.get_satellites()` and `scene.get_ground_stations()`. S4 writes `examples/s4_handover/output/scene/scene.scene`. JavaScript consumers can parse it as JSON with format `skyseed.scene`, currently version 2. [Scene format](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/src/skyseed/scene/README.md) · [S4 Scene construction and persistence](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/examples/s4_handover/scene.py)

| Entity | Fields in the saved Scene |
|---|---|
| Satellite | Select `kind == "satellite"` in `nodes`. Read `node_id` and `name`. For a grouped satellite, `orbit_id` points to `groups[].group_id`. Follow `parent_id` to the shell and constellation |
| GS | Select `kind == "ground_station"` in `nodes`. Read `node_id`, `name`, `latitude_deg`, `longitude_deg` and `elevation_m`. The optional `group_id` points to a ground station group |

The fixed topology view computes canvas coordinates `x/y` from Scene membership. For geographic views, S4 saves sampled Earth-centered, Earth-fixed (ECEF) positions in meters, keyed by timestamp and node ID, in `output/observations/map-positions.json`. Node details return `position_m` and `position_time` when a sample is available. [Topology layout](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/tools/internet_map/layout.py) · [S4 position sampling](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/examples/s4_handover/simu.py)

## Runtime topology

While S4 runs with the map enabled, `http://127.0.0.1:8080/api/v1/skyseed` serves the current topology on the experiment host over HTTP. SEED has WebSockets for terminals and packet capture, among other uses. Periodic WebSocket delivery of SkySEED topology is a proposed interface. The current JSON payload is available as a reference for frontend development and could be reused if both teams agree. [HTTP endpoint](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/tools/internet_map/app/backend/skyseed.js) · [Existing frontend consumer](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/tools/internet_map/app/frontend/src/skyseed.mjs)

Successful responses have the envelope `{"ok":true,"result":{...}}`. An initial request returns the full graph. About a second after the previous request completes, the current page requests `?run=<run>&since=<revision>` with the previous run ID (string) and topology revision (integer).

| Fields in `result` | Interpretation |
|---|---|
| Snapshot `nodes`, `links`, `zones`, `layout` | Build the entire graph. The keys of `nodes` are node IDs. Each entry in `links[].ends[]` contains the endpoint's node ID (`node`), interface name (`name`) and address list (`addresses`) |
| `delta.links` | Keyed by link ID. A link object adds or replaces that link. A null value removes it |
| `zones[id].ground_stations`, `zones[id].serving` | Associated ground stations and current serving satellites, respectively. In a delta, `delta.zones[id]` replaces the entire list of serving satellites. An empty array means the Zone currently has no service |
| `phase`, `elapsed`, `seconds`, `service` | Experiment phase, total elapsed seconds, seconds within the cycle, and the optional UT's currently attached satellite. `service` can be null |

An empty delta can still advance the clock. After an experiment restart (new `run`) or when the requested revision is too old, the response contains a snapshot that replaces the previous graph. [Snapshot and delta generation](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/tools/internet_map/app/backend/state.js)

**How the backend obtains these values.** Runner writes executed operations to the run's `execution.jsonl` and, when a Store is configured, to sequential etcd keys under `/<namespace>/runner/events/`. The map joins the node IDs in link receipts to interface names and addresses from compiled targets. It updates Zone service from adopted routing operations when the experiment supplies Zone associations. S4 supplies its temporary UT attachment separately. A frontend can consume this graph without interpreting BPF instructions or polling every container. [Runner records](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/src/skyseed/runner/runner.py) · [Receipt reader](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/tools/internet_map/app/backend/receipts.js) · [S4 UT integration](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/examples/s4_handover/experiment/ut/map.py)
