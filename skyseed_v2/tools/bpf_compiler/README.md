# BPF Compiler Tool

This detached Tool reads self-contained Plans and their already-created
`BpfCompiledRealization` values or the compact Compiler history. It never
recompiles and never queries Planner.

```python
from tools.bpf_compiler import (
    read_bpf_compiler_history,
    write_bpf_compiler_tool,
)

records = read_bpf_compiler_history(path)

# Full detached values are summarized without compiling again.
write_bpf_compiler_tool(plans, realizations, output_directory)
```

`bpf-compiler-inspection.json` is a compact inspection contract. It preserves
Plan/world/state lineage, activation timestamps, per-boundary operation
workload, add/update/remove breakdowns, semantic-precedence folding,
intended-state cardinalities, stable Zone prefixes needed by Traffic, and
optional Compiler stage timing from Observation. It deliberately does not
duplicate every PlanTransition, backend intent, constraint object, and full
intended state already owned by Plan evidence and the sealed Runner artifact.
The human page is a four-chart dashboard for intent workload,
add/update/remove deltas, precedence folding modes, and Compiler timing. One
shared formal-boundary cursor owns all focus. Timeline/chart clicks and the
global slider update the owning realization and compact structured detail; the
realization dropdown is only a synchronized jump shortcut. Machine JSON stays
separate; the default UI has no giant realization
table or raw `<pre>` dump. The compact `compiler.jsonl` reader continues to validate lineage,
fingerprints, timestamps, activation timestamps, and intent counts only.

The generated continued-delivery path supplies sealed manifest metadata from
`runtime/execution.json` alongside the same continuous Compiler history. The
Sealed deliveries table shows each batch's index, coverage and chain identity;
its jump button selects the existing global formal-boundary cursor at the seam.
This metadata read does not decode runtime binary records, reconstruct a
Runner, or introduce a second timeline. Retained disk evidence remains readable
after runtime plans and eligible kernel generations have been reclaimed.

Compiled intended state is not live runtime truth. Runner evidence owns
realized state.

The optional Spatial context drawer lazily embeds the existing sibling Physical
World Tool and follows the Compiler boundary cursor. Its shared Shell → Orbit,
multiple-Shell and GS/Zone controls are the same controls as the other spatial
Tools. The context is explicitly physical evidence, not compiled or observed
passage; no second world or compiled routing table is serialized.
