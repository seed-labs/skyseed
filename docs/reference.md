# Interface and artifact reference

[中文](reference.zh-CN.md) · [Usage](usage.md)

This page locates public operations. Linked modules own signatures, field tables,
units and minimum contracts; private helpers are not extension requirements.

| Task | Entry | Details |
|---|---|---|
| Construct/query groups, nodes and endpoints | `Scene.create_*`, `get_*`, `nodes`, `groups`, `endpoints` | [Scene](../src/skyseed/scene/README.md) |
| Generate a circular shell from parameters | `Scene.create_shell(..., planes=..., satellites_per_plane=...)` | [Geometry and phasing](../src/skyseed/scene/README.md#build-and-query) |
| Save/load scenario facts | `Scene.dump`, `Scene.load` | [Scene](../src/skyseed/scene/README.md#save-and-load) |
| Load into an AS; customize a SEED node | `SkyAS.loadScene`, `getNode`, `getSceneNodes` | [Emulator](../src/skyseed/emulator/README.md) |
| Calculate positions or candidates | `PhysicalLayer.snapshot_at`, `candidates`, `candidates_at` | [Physical](../src/skyseed/pc/physical/README.md) |
| Read link history; evaluate connections | `LinkLayer.base_plan`, `base_plans`, `execution_plans`, `evaluate_connections` | [Link](../src/skyseed/pc/link/README.md) |
| Read routes or service assignment | `NetworkLayer.base_plans_between`, `delivery_state_at`, `service_assignments_at` | [Network](../src/skyseed/pc/network/README.md) |
| Request finite planning intervals | `PlanningControl.generate_link_plans`, `generate_route_plans` | [P&C](../src/skyseed/pc/README.md) |
| Query a saved logical plan | `state_at`, `states_at`, `slice`, `end_links` / `end_rules` | [Link](../src/skyseed/pc/link/README.md), [Network](../src/skyseed/pc/network/README.md) |
| Save/restore plans | `save_plans`, `load_plans` | [Plan files](../src/skyseed/pc/README.md#plan-files) |
| Compile both streams | `PlanCompiler.compile_plans` | [Compiler](../src/skyseed/pc/compiler/README.md) |
| Start/stop containers; execute plans | `Deployment.up/down`, `Runner.prepare/run/close` | [Runner](../src/skyseed/runner/README.md) |
| Share values or subscribe | `Store.put/get/list/put_many/watch`, `put_user/get_user` | [Store usage](../src/skyseed/runner/README.md#records-and-queries) |
| Export saved data as pages | `tools.observation.plan_views.write_*` | [Observation Tools](../tools/observation/README.md) |

Reading a plan does not recalculate models. Position/candidate/service queries
invoke their model; trial connection evaluation does not adopt its selection.
Generation advances retained planning state. Only deployment/execution calls change
the running network. See each owner's lifecycle before combining calls.

## Models

Setters take a class and optional constructor `model_options`. The layer keeps the
instance. Minimum methods and their arguments are defined in the linked module.

| Owner | Setter | Minimum method | Built-in behavior |
|---|---|---|---|
| [Physical](../src/skyseed/pc/physical/README.md) | `set_position_model` | `compute_positions` | Circular orbit; SGP4 alternative for TLE |
| Physical | `set_candidate_model` | `compute_candidates` | Geometric candidates |
| [Link](../src/skyseed/pc/link/README.md) | `set_selection_hook` | `select_links` | Retain feasible connections, then fill budgets |
| Link | `set_property_hook` | `adjust_properties` | Optional property adjustment |
| Link | `set_plan_model` | `generate_plan` | Periodic link sampling/preparation |
| Link | `set_perturbation_hook` | `perturb` | Optional execution-branch change |
| [Network](../src/skyseed/pc/network/README.md) | `set_service_assignment_model` | `assign_service` | Highest-elevation service satellite |
| Network | `set_routing_hook` | `compute_routes` | GZ routing, MPLS-like first; optional IP |
| Network | `set_plan_model` | `generate_plans` | Periodic route updates |
| Network | `set_delivery_scheduling_hook` | `schedule_plans` | Optional delivery timing; fixed-delay implementation available |

Common route organization and built-in forwarding rules are distinct. Custom
research metadata may be attached; new match/action semantics require a matching
Compiler and executor. Arbitrary `data` does not change packet processing.

## Artifacts

Names below illustrate the S4 output layout; core save APIs accept caller paths.

| Artifact | Contents | Reuse |
|---|---|---|
| `scene/scene.scene` | Scene identities, groups, inputs and endpoints | Load without P&C/SEED |
| `seed/emulator.bin` | Unrendered SEED description | Load/customize before rendering |
| `seed/deployment/` | SEED Compose, Dockerfiles and node configuration | Compile targets and deploy |
| `plans/logical/*.plan` | Base/execution links; base/Delivery routes | Query, compile and compare |
| `plans/physical/*.plan` | Backend targets and timed instructions | Run against a matching deployment |
| `observations/plans.json` | Sampled drawing data and optional model decisions | Render plans without models |
| `views/` | Generated standalone pages | Open offline |
| `runs/<run>/` | Frozen inputs, actual execution and example measurements | Analyze that run |

Physical **snapshots** are model results; Physical**Plans** are compiled instructions.
Store shares live state; exported JSON/JSONL retains offline records. Field-level
formats belong to [P&C](../src/skyseed/pc/README.md),
[Compiler](../src/skyseed/pc/compiler/README.md) and
[Runner](../src/skyseed/runner/README.md).
