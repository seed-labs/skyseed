# GZ routing inspection example

**English** · [中文](README.zh-CN.md)

Use `write_network` from the [saved-artifact tools](../README.md), passing Scene,
both RoutePlan files, the base LinkPlan and drawing context. Open the exported
`index.html`. The display targets default GZ routing and can seed a custom tool.

| Interaction | What it shows |
|---|---|
| Select GS, Zone and direction | Keep the logical pair fixed while tracking service and backbone paths through time |
| Planning scope / View associated GS path | Explain pairs outside the recorded GS associations; switch GS only when clicked |
| Zone / service / link / both path layers | Overlay them together; Focus isolates the selected path and inspected object |
| Click a node, Zone marker or boundary | Inspect details without changing the pair; nodes show all managed rules in the selected branch |
| Use in query | Explicitly use the inspected GS or Zone as a query endpoint |
| Path-node buttons | Inspect that hop's complete rule table |
| Update-event buttons | Jump to exact effective times, including short 200 ms windows |
| Plan details | Open a subpage for the inspected node or Zone, with the current branch and time; filter records, expand full fields or return to the graph |

Scene's optional endpoints provide identity, boundaries and representative points. Regions without
bounds show only their point. Dashed service lines are recorded assignments, not actual last-hop
links. The reverse-path ingress follows the recorded service satellite; it does not infer executed
terminal migration. Delivery may still lead to a different egress. Assignment evaluation times are
shown separately. Unknown, unserved, missing match, loop and unavailable exit remain distinct.
An unassociated GS is outside this GZ computation's demand set; this does not imply that no
link path exists. With no matched ingress rule, the path reports "Not planned" or "Unserved"
when the corresponding fact is recorded. Existing forwarding paths remain visible, including
delayed rules after a service change. Missing association records leave the scope unknown.

`inspection_data(scene, base_plans, delivery_plans, link_plans, positions=..., evaluations=...)`
reads rules from plans and geometry/optional GZ observations from saved data,
without model calls. Paths follow actual rules. Overlapping Delivery context is
counted once; the delayed tail retains and marks final base/link state.
`write_network` splits views by base-plan windows; detail tables retain original
batches, initial state, new operations and context. These are plans, not packet
observations or Runner execution.
