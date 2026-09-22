# Development checks

These Python functions run only when called. They are not observation Tools and
are never automatically registered with an Example or its presentation callback.
Copy [the driver](../../templates/dataplane/threshold/develop.py), supply explicit
imports, constructed Model instances or a fresh scene factory, and your paths.
No class discovery, shell wrapper, daemon or configuration framework is involved.

BPF header/program checks serve C/eBPF development; native Linux material generation
does not require a header, BPF program or these development checks.

- `definition.generate_headers(directory)` is the core DataPlane operation. It
  requires only a `RouteData` definition, not a Model/Plan/C program or compiler.
- `check_model(instance, Protocol)` binds each required method to the Protocol's
  actual call shape. Pass PositionModel, PhysicalLinkModel, the five Planner role
  Protocols, or ZoneMembershipModel. Constructors, inheritance, annotations and
  optional optimization methods are not requirements. This does not execute the Model.
- `check_scene(factory, stop_at=..., output=..., continuation_end=...)` constructs
  a separate Simulator and uses its normal owners/validators. Cutoffs are `position`,
  `physical-link`, `planner`, `compiler`, and `materials`. Planner-role results use
  the Planner cutoff; no second Model validator is maintained. Materials requires
  an output directory. A continuation reuses the same trial cursor. The factory
  must clean up if it fails before returning; returned owners are always closed.
- `check_program(BpfProgram(...))` reuses normal offline C compilation and ELF/map
  inspection. It does not load kernel BPF or establish forwarding correctness.
- `check_diagnostic(name, callable)` reports failure of an explicitly requested
  optional diagnostic as a warning. Required executed failures remain errors.

`show(checks)` prints concise pass/error/warning/not checked lines. A scene with
no route demands/arrangements warns that it did not exercise demand logic. If a
Planner-only trial supplies complete paths while its selected structured program
requires local data, it warns about that concrete downstream mismatch; actual
material preparation rejects it. Optional diagnostics do not invalidate a usable
core result. No elapsed-time, file-size or algorithm-quality thresholds apply.

See [Model interfaces](../../docs/development/model-interfaces.md) and the
[DataPlane contract](../../docs/development/dataplane.md). Normal preparation is
independent of these checks and needs no helper approval/certificate.
