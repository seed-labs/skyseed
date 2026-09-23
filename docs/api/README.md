# Public API reference

Use these pages to find data and operations available to scenario and Model authors.
They cover useful public entry points and the values passed between them, rather than
every implementation method. Linked source owns exact signatures and constructor options;
[developer references](../development/README.md) own boundary requirements.

| I need to… | Reference |
|---|---|
| Find a satellite, GS, orbit or Zone; read positions; calculate geometry | [Physical data and queries](physical.md) |
| Read candidates, preparation state, connection parameters, service assignments or routes | [Planning inputs and results](planning.md) |
| Construct a scene, assemble Simulator, advance calculation or control execution | [Assembly and control](assembly-and-control.md) |
| Replace packet processing | [C/eBPF contract](../development/dataplane.md), [copyable template](../../templates/dataplane/README.md) |
| Implement a replacement Model | [Authoring guide and per-role data map](../user-guides/model-extension.md#data-available-to-each-model), [minimum interfaces](../development/model-interfaces.md) |

Public properties matter as much as methods: `world.autonomous_systems`,
`frame.positions` and a candidate's `satellite_id` are already usable inputs. Do not
copy them into a second entity registry or read an owner's private fields to obtain them.
Each page distinguishes reading existing values, invoking a Model, advancing calculation
and changing the deployed network.

Imports follow ownership:

```python
from skyseed import configuration, emulator, adapters, simulator
from skyseed.simulator import physical, geometry, planner
from skyseed.shared import NodeId
```

Start with the input objects already passed to your Model. The default implementations
use these same queries and fields; their private caches, batch geometry and performance
recording helpers are implementation choices, not additional extension requirements.
