# Compiler inspector

**English** · [中文](README.zh-CN.md)

Inspect saved PhysicalPlans: select MPLS-like BPF or IP Linux, Link/Route,
batch, node and initial/new/context records. Links show endpoint interfaces and
separate directional delay, bandwidth and loss. Routes show match → action →
output, local labels, one-hop bindings and the missing-exit policy. They do not
infer live connectivity or reconstruct a full path from partial plans.

Select a backend step to see its effects and readable command equivalents;
select an endpoint to inspect its service/MAC mapping. Commands are explanatory,
not a complete shell script or an execution log: Runner binds live namespaces,
interfaces and BPF handles. Withdrawals show what is removed. Initial/context
records remain distinct from new operations. Raw fields and encoded bytes are
available in collapsed details.

From the repository root, using the active environment:

```bash
python -m tools.observation.compiler.inspection output/bpf.plan output/linux.plan --output output/compiler
```

Open the generated `index.html`. The standalone page and `compilation.json`
contain only supplied plans; no models, geometry or missing routes are recomputed.
An absent backend or empty selection displays no records.
`instruction_display.py` interprets the built-in instruction ABI solely for
display; unfamiliar opcodes retain their raw records without invented actions.
The compiler API and instruction contract are in the
[module README](../../../new_src/skyseed/pc/compiler/README.md).
