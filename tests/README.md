# Tests by responsibility

[中文](README.zh-CN.md)

| Area | Behavior checked |
|---|---|
| `unit/scene` | Geometry, identity, groups, selection, custom data and persistence |
| `unit/emulator` | AS inventory, Scene loading, conflicts and GS router participation |
| `unit/physical` | ECEF motion/velocity, GP input, visibility, range and spatial search |
| `unit/link` | Connection budgets, directional properties, timed plans and continuous generation |
| `unit/network` | Service assignment, local label handoff, directional paths, RoutePlans and delivery tails |
| `unit/pc` | Independent plan-file restoration, directional/custom data, delivery context and failed writes |
| `unit/compiler` | Stable targets, two-stream lowering, label C program/ABI, IP fallback intents and physical files |
| `unit/environment` | Installed source selection, mixed-source rejection, clean wheel contents and development scripts |
| `unit/runner` | Scheduling, plan merging, resource cleanup and BPF version ownership |
| `unit/examples` | UT routing/cleanup, regional outage and measurement records |
| `unit/tools` | Saved-plan views and actual-execution replay |
| `connections` | Scene → real SEED objects → saved description and deployment files; services, routing software and examples |
| `integration` | Scene → Emu deployment + P&C → saved/restored logical plans → BPF/Linux physical plans; independent requests, lookahead, segmented equivalence and delivery tails |
| `live/runner`, `live/store` | Explicitly enabled Docker/BPF/Linux forwarding, UT and etcd checks |

`connections/test_scene_physical.py` also covers Scene persistence → real
Physical queries, model reuse and custom field/velocity handoff.
`connections/test_physical_link.py` cover real Physical → Link plans and model
queries without accidental adoption. Tests under
`unit/tools` check inspection data, including plan-detail archives, directional values and delivery context,
without starting network execution.
`connections/test_link_network.py` checks real geometric Link input, GZ labels
and base-route independence from an execution-link outage.

Tests use small fixtures. Connection tests compile to temporary directories without
starting containers. The [tiny example](../examples/tiny_network/README.md)
provides the separate live deployment and connectivity check.

Run affected modules and direct consumers; broaden checks for shared-contract
changes. Add regression tests for concrete failures, with expectations that can
catch a wrong result independently of the implementation.
See [development commands](../docs/development.md).
