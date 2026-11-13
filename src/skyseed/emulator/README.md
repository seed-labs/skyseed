# Emulator

[中文](README.zh-CN.md)

Use SkySEED's construction classes alongside ordinary SEED layers, services and
compilers. Given a [Scene](../scene/README.md), the construction pattern is:

```python
from seedemu import Routing, Ospf
from skyseed.emulator import SkyEmulator, SkyBase

emulator = SkyEmulator()
base = SkyBase()
routing = Routing()
ospf = Ospf()
ospf.maskAsn(150)

satellite_as = base.createSkyAutonomousSystem(150)
satellite_as.loadScene(scene)
ground_as = base.createAutonomousSystem(151)

emulator.addLayer(base)
emulator.addLayer(routing)
emulator.addLayer(ospf)
```

Layers are added explicitly. Before rendering, connect GS routers to a network or
IX as required by SEED's Routing layer. The
[complete example](../../../examples/tiny_network/emulator.py) supplies those connections.

## Load and customize

`loadScene(scene)` creates all Scene nodes: satellites become ExtensionNodes and
GS nodes become ordinary SEED Routers. `routingBackend="bird"` (default) or
`"frr"` chooses GS software, independently of the plan forwarding backend.

To load only selected nodes:

```python
selected = scene.get_node_ids(groups=["demo", "ground"])
satellite_as.loadScene(scene, node_ids=selected)
```

Use this instead of the all-node load above. One Scene can supply several ASs;
one AS can load several disjoint Scene inputs. Node IDs must be unique across the
assembled Emulator. Loading must precede SEED node registration and leaves Scene
unchanged; later Scene edits are not synchronized into the created nodes.

`getNode(scene_id)` returns the editable SEED node, locally on SkyAS or globally
on SkyEmulator. `getSceneNodes()` returns a dictionary of these same objects.
Unknown IDs raise `KeyError`; ambiguous identities are rejected. Use these APIs
instead of reconstructing generated SEED names. The reserved `skyseed.scene_id`
and `skyseed.scene_kind` labels carry identity into deployment files.

Ordinary Node methods such as `addSoftware`, `setFile`, `importFile` and
`appendStartCommand` remain available. The optional `configure_native_software`
helper from `skyseed.emulator` accepts BIRD text (`daemon="bird"`) or an FRR
configuration dictionary including `zebra` (`daemon="frr"`). Protocol policy stays
with the caller; use the helper only where SEED's Routing layer does not already
manage that node's software.

## Reuse and deploy

| Operation | Object or output |
|---|---|
| `loadScene`, node configuration and adding layers | Editable SEED construction description |
| `dump` / `load` | Save or restore the unrendered description |
| `render()` | Configure and bind layers |
| `compile(Docker(...), output)` | Write Compose, Dockerfiles and node configuration |
| Docker Compose | Build images and start nodes and static networks |

SEED's `dump` and `load` save and restore the **unrendered construction description**,
including Scene-ID lookup. This is a pickle format for trusted descriptions, not
a `.scene` file; loading replaces the current description. Service-only descriptions
can be combined with a Base later.

<details>
<summary>Merging descriptions and sharing Base objects</summary>

`merge` takes explicit SEED merger policies and returns SkyEmulator. The default
Base merger returns ordinary Base and shares selected AS objects, so the result
is not a deep copy and does not retain the SkyBase factory. To extend ordinary
Base, add a `SkyAS` with `setAutonomousSystem` after checking for an ASN conflict,
or merge a separately built SkyEmulator description.

</details>

Follow the [example commands](../../../examples/tiny_network/README.md#build-and-start).

## Files

- `sky_as.py`: loads Scene nodes into SEED inventories.
- `sky_base.py`: adds the SkyAS factory to SEED Base.
- `sky_emulator.py`: retains SEED's lifecycle and provides global Scene-ID lookup.
- `scene_identity.py`: SEED names, Scene identity labels and ambiguity checks.
- `native_routing.py`: optional daemon configuration through public Node APIs.
