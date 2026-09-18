# Minimal Model templates

Each Python file is independently copyable. Pick the role in the
[Model contract/navigation table](../../docs/development/model-interfaces.md), then follow the
[injection guide](../../docs/user-guides/model-extension.md). The examples are intentionally
simple; their algorithms are not recommended physical or routing policies.

Default algorithms remain in PhysicalWorld/Planner modules, default assembly in Simulator,
and real S3/S5 replacement Models beside their examples. These templates introduce no
registration or plugin framework. Optional diagnostics/prediction/identity hooks can be omitted.
