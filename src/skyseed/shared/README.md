# Neutral shared facilities

Shared provides immutable `NodeId`, identifier/time validation, optional performance
recording and private mechanical I/O, ordered evidence and shutdown helpers.
`_console` provides best-effort, flushed stderr feedback without changing operation results.
These facilities carry no Simulator, backend, deployment or experiment authority.
They do not choose Models, paths, resource lifecycles or policy.

Geometry stays in Simulator; artifact semantics stay with their record owner.
[Component boundaries](../../../docs/development/component-boundaries.md) and the real import
boundary checker guard this distinction.
