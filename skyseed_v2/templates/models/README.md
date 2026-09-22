# Minimal Model templates

Each Python file is independently copyable. Pick the role in the
[Model contract/navigation table](../../docs/development/model-interfaces.md), then follow the
[injection guide](../../docs/user-guides/model-extension.md). The examples are intentionally
simple; their algorithms are not recommended physical or routing policies.

Default algorithms remain in PhysicalWorld/Planner modules, default assembly in Simulator,
and real S3/S5 replacement Models beside their examples. These templates introduce no
registration or plugin framework. Optional diagnostics/prediction/identity hooks can be omitted.

Each template's header points to relevant data and queries. The
[per-role data map](../../docs/user-guides/model-extension.md#data-available-to-each-model)
explains which inputs a Model receives; the [API reference](../../docs/api/README.md)
covers public fields, lookups, geometry and control. The contract table above links
directly to each default implementation for algorithm examples. Private default helpers
are not required when writing your own Model.

For independent checks, copy the [development driver](../dataplane/threshold/develop.py) and use the [development helpers](../../tools/development/README.md). Structured routing and generated C authoring follow the [DataPlane contract](../../docs/development/dataplane.md).
