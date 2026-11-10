# GZ routing inspection example

**English** · [中文](README.zh-CN.md)

```bash
python -m tools.observation.network.inspection --output /tmp/skyseed-network
```

Open the generated `index.html`. The demo retains 64 satellites and three approximate city GS
positions, adding six bounded illustrative regions rather than a complete global partition.
Half the satellites receive an extra 200 ms update delay as a Delivery comparison condition.
This optional Ground Station–Zone inspector can serve as a starting point for custom tools.

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

`inspection_data` reads RoutePlans, base LinkPlans and optional GZ observations. Overlapping delivery
batches are projected into non-overlapping time intervals, counting prior context only once.
Paths follow actual rules without recomputing routes or service assignment. Only display positions
query Physical. Actual event times are retained even with additional sparse display samples.
Beyond base coverage, the final base and link states are explicitly held for the delivery tail.

`plans/index.html` holds the longer tables and downloads for `base_route.plan` and
`delivery_route.plan`. It reads the original saved batches, before the display's
overlap projection, and compares restored values with the originals. Initial entries,
new operations and prior context remain separate; context is not another delivery.
Returning to the graph retains the GS/Zone/direction query; a row selects its node and
time. The graph shows state after operations at that time, not the prior initial state.

The demo uses `tools.observation._presentation.plan_details`'s shared
`write_plan_details(output, kind="route", streams={"base": ...,
"delivery": ...})` and sets `data["plan_details"] = "plans/index.html"` before
`write_inspection`. Generation, display sampling, plan files/details and page writing
are timed separately. The JSON is display data; `.plan` files are reusable logical plans.
Scene geometry and model observations stay with the parent page, outside those files.
This tool is not evidence of BPF packet or Runner execution.
