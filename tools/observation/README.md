# Observation tools

[中文](README.zh-CN.md)

Current tools read saved artifacts independently of Runner. They require no live
P&C instance, Docker, etcd or traffic processes.

```python
from tools.observation.plan_views import (
    load_context, write_scene, write_physical, write_link, write_network, write_compiler,
)

write_scene(scene_file, scene_output)
context = load_context(context_file)
write_physical(context, physical_output)
write_link(scene_file, base_link_file, execution_link_file,
           context=context, output=link_output)
write_network(scene_file, base_route_file, delivery_route_file, base_link_file,
              context=context, output=network_output)
del context
write_compiler(compiled_plan_file, compiler_output)
```

Each function reads files and returns the exported `index.html`. Link/Network pages
follow base-plan windows while preserving differently split execution histories,
overlapping Delivery and its final tail. Detail tables retain original batches.
See [S4 view.py](../../examples/s4_handover/view.py). Pages are self-contained.

Context inputs accept either a file path or the mapping returned by `load_context`.
When generating several views, load it once and share it: writers only read the
mapping. The caller owns its lifetime; release it after the last consumer. There
is no process-wide cache, and loading a file again reads its current contents.

| Input | Contents |
|---|---|
| Scene | Organization, initial orbits, GS and Zone/endpoint facts |
| LinkPlan | Base/execution connections, directional properties, operation times |
| RoutePlan | Base/Delivery node rules, labels or IP actions |
| PhysicalPlan | Compiled backend instructions, distinct from physical snapshots |
| Drawing context JSON | Saved positions, Physical snapshots/candidates, optional model observations |

The calculation side explicitly samples the configured Physical model. Readers
never recalculate geometry, candidates, routes or service assignments. Missing
model observations remain unknown. Missing geometry for a required timestamp
raises an error instead of silently using default orbital models. Custom display
fields must be JSON-serializable.

```python
from tools.observation.plan_views import save_context

save_context(
    context_file, physical,
    link_plans=all_link_plans, route_plans=all_route_plans,
    physical_times=sample_times,
    link_evaluations=link_evaluations, route_evaluations=route_evaluations,
)
```

`physical_times` selects candidate samples for the Physical page. Positions at all
plan boundaries, operations and observations are included. `position_times` adds
position-only samples. Each timestamp's positions are exported once. This step
saves data; `write_*` generates pages later.

Plans remain the formal calculation products; context is optional Tool data, not
a new core plan format. Evaluation adapters in `save_context` target the existing
Link/GZ defaults. Custom tools can use layer `inspection_data`/`write_inspection`
without reproducing default-model diagnostic behavior.

Page guides: [Scene](scene/README.md), [Physical](physical/README.md),
[Link](link/README.md), [Network](network/README.md), [Compiler](compiler/README.md).
[Execution](execution/README.md) consumes actual receipts. S4's UT, outage and TCP
replay are example-specific overlays.
