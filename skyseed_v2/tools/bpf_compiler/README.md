# BPF Compiler Inspection Tool

The repository-local facade consumes consecutive immutable `Plan` values and
their already-produced `BpfCompiledRealization` values. It writes one primary
offline HTML page plus deterministic private JSON/audit files.

Legend: `A --> B` means the output of stage A is the only input of stage B.

```mermaid
flowchart LR
    F["Plans + compiled realizations"] --> R["reader: detached snapshot"]
    R --> P["projection: summaries + exact supported mappings"]
    P --> U["renderer: offline human-review page"]
    U --> W["writer: files + checksums"]
```

`write_bpf_compiler_tool(...)` composes this pipeline. It never calls
`compile_bpf`, queries Simulator/Slicer/Planner, invokes a Model, or derives a
runtime/kernel binding. Compilation belongs to the caller before the Tool.

The default page view is a compiled-result-first symbolic operation inventory:
resource establish/retire, bidirectional attachment requirements, directional
qdisc targets and updates, forwarding operations with resolved symbolic
egress, Zone-edge operations, and explicit dependency relationships. It does
not present these values as concrete runtime commands or bindings.

The continuous multi-Plan timeline and interval switcher also provide a
secondary Plan-to-symbolic provenance comparison, focused resource/qdisc/
forwarding/Zone views, ordering constraints, and intended end state. Exact
origin links are shown for Connection, Parameter, and Forwarding values where
current fields support the relationship. Zone edges are clearly marked as
mechanical Compiler output; no nonexistent Plan transition provenance is
invented.

Canonical identifiers remain in JSON and expandable page details. Private
display aliases shorten node and Zone labels without changing identity. A
same-next-hop `CHANGED` is highlighted as a formal change with equal old/new
next hops, never collapsed into a no-op.

Browser smoke consumes the written HTML from `scripts/` and remains outside
the Tool pipeline. The complex human-review entry is:

```bash
python -m examples.mini_satellite_internet.main
```
