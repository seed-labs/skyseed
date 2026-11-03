# Tests by responsibility

| Area | Behavior checked |
|---|---|
| `unit/scene` | Geometry, identity, groups, selection, custom data and persistence |
| `unit/emulator` | AS inventory, Scene loading, conflicts and GS router participation |
| `unit/environment` | Source selection, retired entry rejection and clean wheel contents |
| `connections` | Scene → real SEED objects → saved description and deployment files; services, routing software and examples |

Tests use small fixtures. Connection tests compile to temporary directories without
starting containers. The [tiny example](../new_examples/tiny_network/README.md)
provides the separate live deployment and connectivity check.

Run affected modules and direct consumers; broaden checks for shared-contract
changes. Add regression tests for concrete failures, with expectations that can
catch a wrong result independently of the implementation.
See [development commands](../new_docs/development.md).
