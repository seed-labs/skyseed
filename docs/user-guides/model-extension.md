# Replace a Model

Choose the role in the [minimum-contract navigation](../development/model-interfaces.md), copy its template
into your own scenario, and inject an instance at Simulator assembly. The table links each
role to its minimum contract, template, default and a working example. Templates stay in
`templates/models/`; default algorithms stay in their owning modules.

```python
from skyseed import simulator
from my_routing import MyRouteModel

simulation = simulator.create_default_simulator(
    definition,
    route_model=MyRouteModel(),
)
```

`definition` is the finite Timeline and pure input assembled in the
[scene guide](inputs-and-scenes.md). Factory keywords independently replace Position,
physical-link, ISL, GS, Zone-service, parameter and route roles. Geographic membership
is a [lower-level optional capability](../development/model-interfaces.md#lower-level-optional-zone-membership).
Omitted roles receive fresh defaults; supplied objects are preserved. Configuring an existing
Model's constructor differs from replacing its algorithm: [Tiny](../../examples/Sky00_tiny_network/tiny_network.py)
demonstrates the first, [S5](../../examples/Sky01_s5_custom_models/model.py) the second.

## Data available to each Model

The default Models already use public queries and fields to read their inputs. Custom
Models can use the same facilities without subclassing a default or copying its private
caches. This table maps each role to useful data; the
[contract table](../development/model-interfaces.md) links its template and default implementation.

| Role | Useful inputs and helpers | Reference |
|---|---|---|
| Position | World AS → shells → orbits → satellites; orbit elements, GS coordinates; `geometry.geodetic_to_ecef` | [Entities](../api/physical.md#physicalworld), [geometry](../api/physical.md#geometry-helpers) |
| PhysicalLink | Supplied frame's `timestamp`, `positions`, `get_position`; request pairs/flags; distance, elevation and line-of-sight helpers | [Snapshots](../api/physical.md#position-snapshots), [candidates](../api/planning.md#physical-candidates) |
| ISL | `world.get_satellite`, orbit traversal, endpoint capacities; candidate plane/distance data; `state.selected_since` | [Entities](../api/physical.md#physicalworld), [selection state](../api/planning.md#selection-state) |
| GS | `world.get_ground_station`, `get_satellite`, access capacities; supplied candidates and preparing/operational selections | [Entities](../api/physical.md#physicalworld), [selection state](../api/planning.md#selection-state) |
| Zone service | `world.get_zone_layout().zones`, candidate satellite IDs/elevation, previous service assignments | [Zones](../api/physical.md#zone-queries), [service values](../api/planning.md#service-and-routing) |
| Parameters | Observation's connection ID, optional physical delay/candidate, previous Model memory; no World argument | [Parameters](../api/planning.md#effective-parameters) |
| Routing | Operational connections and directional parameters, service assignments, demands, previous outcomes; World entity/Zone lookups | [Service and routes](../api/planning.md#service-and-routing) |

Optional ZoneMembership receives owned Zones or matches. It can use `zone.contains`,
`priority` and `geometry`; see [Zone queries](../api/physical.md#zone-queries). It does
not select the serving satellite.

For example, inside a PhysicalLink Model, read the supplied snapshot for the requested
ISL pairs and compute only the quantity your algorithm needs:

```python
from skyseed.simulator import geometry

for a_id, b_id in request.inter_satellite_pairs:
    a = physical_frame.get_position(a_id)
    b = physical_frame.get_position(b_id)
    distance_m = geometry.ecef_distance_m(a, b)
```

Inside GS selection, look up the actual owners of static limits (`candidate` is a GS
access candidate):

```python
station = physical_world.get_ground_station(candidate.ground_station_id)
satellite = physical_world.get_satellite(candidate.satellite_id)
station_limit = station.max_simultaneous_satellite_accesses
satellite_limit = satellite.max_simultaneous_ground_station_accesses
```

These limits may be unspecified; the [GS template](../../templates/models/ground_station.py)
shows an explicit policy and capacity accounting. In routing, resolve a Zone target
within its scope from the supplied assignments:

```python
service = {item.service_key: item.satellite_id for item in zone_service_assignments}
terminal = service[demand.target.service_key]  # demand.target is a ZoneRouteDemandTarget
```

`terminal=None` means unserved, not a graph node. A GS target instead exposes
`ground_station_id`. The [routing template](../../templates/models/routing.py) handles both.

Reading a snapshot does not rerun Position. `world.frame_at(other_timestamp)` does:
use it only when your algorithm actually needs another evaluation, never recursively
from Position itself. Selection/route hooks should use their supplied candidates and
state rather than repeat the preceding calculation or advance Planner themselves.
This does not require default prediction, caching or optimization policies.

## Read user data in a Model

A Model can use `InteractionClient` without a special framework hook. This complete
ParameterModel reads the key written in the [Feedback guide](computation-and-controls.md#publish-custom-data-let-the-model-choose-when-to-read-it)
on each ordinary call. It deliberately uses fixed symmetric bandwidth/loss and an
author-selected delay; it is a teaching policy, not a fault algorithm:

```python
from pathlib import Path
from skyseed.experiment import InteractionClient
from skyseed.simulator import (
    EffectiveConnectionParameters, OperationalConnection, ParameterPlanningResult,
)

class FeedbackParameterModel:
    def __init__(self, output):
        self.output = Path(output).resolve()

    def plan_effective_parameters(self, timestamp, observations, previous_memory):
        client = InteractionClient.from_output(self.output)
        try:
            row = client.user_get("research/link-parameters")
        finally:
            client.close()
        delay_us = 100 if row is None else int(row["value"]["delay_us"])
        parameters = EffectiveConnectionParameters(1_000_000, delay_us, 0)
        return ParameterPlanningResult(tuple(
            OperationalConnection(item.connection_id, parameters, parameters)
            for item in observations
        ))
```

Prerequisites: the normal host environment and output-owned/external service from
the [setup guide](computation-and-controls.md#interaction-setup-and-independent-programs).
Save the class in an authored module, import it in your scene, and inject
`parameter_model=FeedbackParameterModel(output)` into `create_default_simulator`.
Give Controller that same output path and capture the module with `input_files`.
The constructor does not connect; Controller starts discovery before Producer invokes
the method. Missing data uses the stated default. An unavailable service or malformed
value raises here; choose a different explicit policy in your Model if appropriate.

A write alone does not call this method. Use explicit continuation or revision when
you want more calculation. Models choose reads, watches, caches and algorithms;
`user_read` is available for an optional consistent multi-key read. Revision may
call this method at earlier timestamps again. Framework continuation restores its
own state, not the Model's private caches or external user values. Do not drive
Controller/Runner controls recursively from a Model invocation. See the
[revision and continuation contract](../development/experiment-control.md#revision-and-reusable-continuation).

## Verify a replacement

Begin with `--operation generate --through planner` on a small scene, examine explicit
route/service outcomes, then compile through the normal chain. To test state retention,
run retained computation and append one short range. Compare transition semantics where
prediction and Model assumptions permit equivalence; Plan grouping may differ at flushes.

The framework checks identity, cadence, capacities and output correspondence. It does not
require circular geometry, future prediction, default caches, optimal choices or serializable
private state. Missing optional measurements stay unavailable in Tools. If you mix a simple
physical Model with defaults that need elevation/delay/plane metadata, either provide those
inputs or replace the dependent roles. The [optional-capability contract](../development/model-interfaces.md)
explains routing reuse, unknown future contacts, diagnostics, retry and resource cleanup.

S3's [OSPF Model](../../examples/Sky03_s3_ospf/ospf_model.py) is a local route calculation with
explicit Area metadata for explanation. It is not a live routing protocol or mandatory core
algorithm. Native BIRD/FRR software is separately configured through the
[node software adapter](../api/assembly-and-control.md#native-routing-software);
AI assistance remains [prospective](../plans/roadmap.md).

## Independent authoring checks

Use the [development functions](../../tools/development/README.md) with explicit Model instances and a fresh small-scene factory. Checks can stop at Position, PhysicalLink, Planner, Compiler or sealed materials; a short continuation is optional. For structured routing: define data, run the [Python driver](../../templates/dataplane/threshold/develop.py) to generate headers, include them and write C, run desired checks, then prepare normally. The [DataPlane contract](../development/dataplane.md) is authoritative; RouteModels keep logical values and no BPF dependencies.
