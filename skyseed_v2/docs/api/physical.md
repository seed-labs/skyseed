# Physical data and queries

## Identities and owners

Model methods receive `physical_world`; scenario code gets the same owner from
`simulation.get_physical_world()` after assembly. Simulator creates PhysicalWorld;
users do not construct it or a PhysicalPositionSnapshot directly.

[`NodeId`](../../src/skyseed_v2/shared/node_id.py) has `asn: int`,
`entity_kind: str` (`"satellite"` or `"ground-station"`) and `entity_id: str`.
Its `value` property renders `asn/kind/id`. Use identities supplied by entities,
candidates or demands; they are domain identities, not container names.

## PhysicalWorld

These methods read immutable scene facts. Entity lookups raise `KeyError` for missing
identities; they do not return `None`. Full signatures:
[`PhysicalWorld`](../../src/skyseed_v2/simulator/physical/world/world.py).

| Access | Input → result |
|---|---|
| `autonomous_systems` | Property → tuple of physical autonomous systems |
| `get_autonomous_system(asn)` | Integer ASN → physical AS |
| `get_node(node_id)` | NodeId → satellite or GS |
| `get_satellite(node_id)` / `get_ground_station(node_id)` | NodeId of the matching kind → typed entity |
| `get_shell(asn, shell_id)` | Integer ASN, string shell ID → shell |
| `get_orbit(asn, shell_id, orbit_id)` | Parent identifiers → orbit |
| `get_zone_layout()` / `get_zone(zone_id)` | Layout or one Zone; `RuntimeError` if no layout was configured |

The [physical entities](../../src/skyseed_v2/simulator/physical/world/entities.py)
support traversal without additional queries:

| Owner/value | Useful public data and local lookup |
|---|---|
| AS | `asn`, `shells`, `ground_stations`; `get_shell(shell_id)`, `get_ground_station(node_id)` |
| Shell | `asn`, `shell_id`, `orbits`; `get_orbit(orbit_id)` |
| Orbit | `orbit_id`, `altitude_km`, `inclination_deg`, `right_ascension_deg`, `epoch_utc`, `satellites`; `get_satellite(node_id)` |
| Satellite | `node_id`, `satellite_id`, `orbit_index`, `mean_anomaly_deg`, `inter_satellite_link_capability`, `max_simultaneous_ground_station_accesses` |
| GS | `node_id`, `ground_station_id`, `latitude_deg`, `longitude_deg`, `elevation_m`, `station_type`, `operator`, `max_simultaneous_satellite_accesses` |

Collections above are tuples. An ISL capability, when present, supplies
`maximum_simultaneous_links`. Optional capacities/capabilities can be `None`; this is
not zero capacity. Handle unspecified values explicitly, as the [GS template](../../templates/models/ground_station.py)
does. These are static limits, not currently free capacity: selections must also account
for [preparing reservations](planning.md#selection-state).

## Position snapshots

`world.frame_at(timestamp)` calls the Position Model and returns a complete
[`PhysicalPositionSnapshot`](../../src/skyseed_v2/simulator/physical/queries/position.py).
It evaluates the requested timezone-aware UTC datetime; it does not advance Planner or
Runner. It requires the World's computation resources to remain open.

| Snapshot access | Result / effect |
|---|---|
| `timestamp` | Exact datetime of this evaluation |
| `positions` | Read-only mapping of all physical NodeIds to EarthFixedPosition |
| `get_position(node_id)` | Existing position; `KeyError` for an unknown ID; no recomputation |

PhysicalLink receives an already computed `physical_frame`: reuse it for current
positions. Calling `frame_at()` for another time performs another Model evaluation;
do not call it from your Position Model to obtain its own output, which would recurse.

## Geometry helpers

Import `geometry` from `skyseed_v2.simulator`. These are optional numerical helpers;
using them does not select a link, enforce a physical policy or advance simulation.

| Value/function | Meaning |
|---|---|
| `EarthFixedPosition(x_m, y_m, z_m)` | Immutable ECEF coordinates in metres |
| `position.latitude_deg`, `longitude_deg`, `ellipsoidal_height_m` | Derived WGS84 geographic coordinates; `position.geographic` returns the three as a tuple |
| `geodetic_to_ecef(latitude_deg, longitude_deg, ellipsoidal_height_m)` | Degrees and metres → EarthFixedPosition |
| `ecef_to_geodetic(position)` | EarthFixedPosition → `(latitude_deg, longitude_deg, ellipsoidal_height_m)` |
| `ecef_distance_m(first, second)` | Straight-line distance in metres |
| `elevation_deg(observer, target)` | Degrees above the observer's local WGS84 horizon; argument order matters |
| `has_earth_line_of_sight(first, second)` | Whether the segment avoids the WGS84 Earth interior |

See [geometry exports](../../src/skyseed_v2/simulator/geometry/__init__.py),
[coordinates](../../src/skyseed_v2/simulator/geometry/coordinates.py) and
[position values](../../src/skyseed_v2/simulator/geometry/position.py).

## Zone queries

World delegates the following queries to its
[`ZoneLayout`](../../src/skyseed_v2/simulator/physical/world/zone.py). Latitude/longitude
are degrees. A layout must have been configured.

| Method | Result / computation |
|---|---|
| `find_zones(latitude_deg, longitude_deg)` | Tuple of matching Zones, sorted by ID; invokes the membership Model |
| `resolve_zone(latitude_deg, longitude_deg)` | One chosen Zone; invokes membership; `LookupError` if none match |
| `find_intersecting_zones(spherical_cap)` | Tuple intersecting the given cap; geometric intersection of the current rectangular Zones |

`ZoneLayout.zones` gives the owned tuple; `allow_overlap` records the layout setting.
A Zone exposes `zone_id`, `priority`, `geometry`, `representative_point` (latitude,
longitude) and `contains(latitude_deg, longitude_deg)`. The last checks its rectangle;
a custom membership Model can choose a different membership policy. Do not implement
membership by recursively calling the layout's membership queries.

`physical.SphericalCap(center=(latitude_deg, longitude_deg), angular_radius_deg=...)`
is the geometric query input. Membership, shape intersection and the Planner's choice
of a serving satellite are different operations. See [Zone-service values](planning.md#service-and-routing).
