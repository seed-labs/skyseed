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

The current production chain is:

```text
PhysicalWorld / PhysicalFrame
→ PhysicalLinkObservation
→ Planner / PlannedOperationalState
→ Plan
→ Compiler / BpfCompiledRealization
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
