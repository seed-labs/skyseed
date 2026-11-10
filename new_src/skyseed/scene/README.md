# Scene module

**English** · [中文](README.zh-CN.md)

- `scene.py`: Scene ownership, construction, queries and save/load.
- `scene_entities.py`: constellation, shell, orbit, satellite and GS types,
  position inputs and their local checks.
- `scene_endpoints.py`: stable access destinations and the default geographic
  partition; no service assignment or forwarding rules.

`Scene.endpoints`, `get_endpoint` and `get_endpoints(kind)` expose logical access
destinations separately from physical nodes. `create_zone` supplies a representative
latitude/longitude and optional rectangular bounds; `create_default_zones` adds
the fixed cosine-scaled global partition. `create_endpoint` supports other kinds
with model-defined JSON data. Endpoint identity/kind are fixed; editable data is
validated when saved. Endpoints persist in the optional v2 `endpoints` field;
existing v2 scenes without that field load with an empty collection.

The module uses only the standard library. Algorithms and AS assignment belong
to its consumers. See [Scene usage and structure](../../../new_docs/scene.md)
and the [authoring example](../../../new_examples/tiny_network/scene.py).
