# BPF Compiler Inspection Tool

The repository-local facade consumes consecutive immutable `Plan` values and
their already-produced `BpfCompiledRealization` values. It writes one primary
offline HTML page, a compact file-compatible manifest, paged exact-evidence
chunks, and deterministic audit files.

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

The primary page answers what each Plan became: logical delta input, interval
operations, complete intended end-state baseline, adjacent-baseline churn, and
serialized family size. Four run-wide views remain: logical Plan input
cardinality, complete realization baseline size, adjacent realization churn,
and whole-run backend-family bytes. With many Plans, the first three use at
most twelve contiguous readable buckets. Input work is summed; baseline size
uses median plus min/max rather than summing complete baselines; churn uses
group medians and ratio ranges. Clicking a bucket exposes its exact Pn/Cn/Rn
members. Compile-time and synthetic density charts are intentionally absent.

The Plan scrubber drives secondary lazy drill-down for exact Plan transitions,
resource/qdisc/forwarding/Zone operations, ordering constraints, complete
baseline records, and exact adjacent before/after keys. Exact origin links are
shown where retained fields prove the relationship. Zone edges are marked as
mechanical Compiler output; no nonexistent transition provenance is invented.
Each realization also has a canonical JSON SHA-256 change detector in the
manifest and qualification inventory.

Canonical identifiers remain in JSON and expandable page details. Private
display aliases shorten node and Zone labels without changing identity. A
same-next-hop `CHANGED` is highlighted as a formal change with equal old/new
next hops, never collapsed into a no-op.

The HTML embeds neither the manifest nor exact payload. `manifest.js` supports
direct `file://` opening, details are split into bounded chunks, and stale-load
tokens plus registry cleanup bound active browser state. Browser smoke consumes
the written HTML from `scripts/` and remains outside the Tool pipeline. The
exact chunks use deterministic `gzip-base64-json` envelopes carrying the
uncompressed byte count and SHA-256; only the requested page is decompressed.
The
complex human-review entry is:

```bash
python -m examples.mini_satellite_internet.main
```
