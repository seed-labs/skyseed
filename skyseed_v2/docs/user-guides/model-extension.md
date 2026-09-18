# Replace a Model

Choose the role in the [minimum-contract navigation](../development/model-interfaces.md), copy its template
into your own scenario, and inject an instance at Simulator assembly. The table links each
role to its minimum contract, template, default and a working example. Templates stay in
`templates/models/`; default algorithms stay in their owning modules.

```python
from skyseed_v2 import simulator
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
algorithm. Native routing protocols/Layer integration and AI assistance remain
[prospective](../plans/roadmap.md).
