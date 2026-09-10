# Documentation map

Repository documentation has three current audiences.

- [`accepted-design.md`](accepted-design.md) is the highest architecture anchor.
- [`CURRENT.md`](CURRENT.md) records the current implementation and
  qualification boundary.
- [`human-readable/`](human-readable/) explains the system through compact
  diagrams and terminology.
- [`codex-reference/`](codex-reference/) maps contracts to owners and files.
- [`user-guides/`](user-guides/) contains runnable entry points and inspection
  workflows.
- [Qualified performance and bottlenecks](human-readable/qualification-performance.md)
  separates work, pacing, overlapping spans and resource scopes across the three conditions.
- [Framework construction, customization and execution](human-readable/framework-construction-execution.md)
  follows the actual Example inputs through Models, Plans and real Runner/BPF forwarding.
- [已采用优化的中文说明](human-readable/optimization-decisions.md) explains
  mechanisms and tradeoffs by responsibility; its
  [decision history](provenance/optimization-decision-history.md) links source
  checkpoints, retained measurements and host-configuration observations.
  These records explain adopted choices without adding architecture authority.

The current production chain is:

```text
SimulationTimeline
→ PhysicalWorld / PhysicalFrame
→ PhysicalLinkObservation
→ Planner / PlannedOperationalState
→ Plan
→ Compiler / BpfCompiledRealization
→ sealed Runner artifact
→ Runner / RealizedRuntimeState
```

Package and example READMEs describe local ownership and maintained entry
points. Source code and tests are the executable contract; documentation must
match them in the same change.

`docs/provenance/` contains labelled historical investigations and validation
records. They are evidence, not current architecture. `docs/candidates/` holds
unaccepted proposals and must not be read as authorization.

Run the documentation contract with:

```bash
python scripts/check_documentation.py
```

The gate validates required documents, links, current public imports, frozen
window/Compiler/producer terminology, Mermaid syntax, and absence of retired
current-contract names from authoritative documentation.
