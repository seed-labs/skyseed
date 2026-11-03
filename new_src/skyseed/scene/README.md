# Scene module

- `scene.py`: Scene ownership, construction, queries and save/load.
- `scene_entities.py`: constellation, shell, orbit, satellite and GS types,
  position inputs and their local checks.

The module uses only the standard library. Algorithms and AS assignment belong
to its consumers. See [Scene usage and structure](../../../new_docs/scene.md)
and the [authoring example](../../../new_examples/tiny_network/scene.py).
