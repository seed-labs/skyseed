# Link plan inspection

**English** · [中文](README.zh-CN.md)

Inspect actual [Link](../../../src/skyseed/pc/link/README.md) decisions,
setup timing, operational connections and base/execution branch differences.

Use `write_link` from the [saved-artifact tools](../README.md), passing Scene,
both LinkPlan files and drawing context. The tool no longer builds a demo scene
or replans; open the exported `index.html` directly.

- Drag the globe; choose a node, connection or exact event to inspect it.
- Selection highlights; **Focus** isolates it. **Overview** restores the whole scene.
- Enable candidate lines to compare opportunities against adopted selections.
- Switch base / execution / comparison; pink identifies availability or property differences.
  Single-branch views show that branch's operational connections; the candidate
  overlay adds only recorded physical opportunities, without treating them as operational.
- The timeline includes actual operation times. Details retain both directional
  reports, effective properties and model fields. Missing evaluation history
  remains unknown. Candidate evidence is explicitly labelled with its latest
  recorded evaluation time, not falsely presented as a fresh intermediate query.
- **Plan details** opens `plans/index.html` with the current connection/node, time
  and branch selected (comparison opens execution). Switch batches or filter initial
  entries and new operations; expand full fields or follow a row back to the graph.
  The graph shows state at that time, after any operations at the interval start.
  Download `base_link.plan` or `execution_link.plan` to reuse the original batches.

`inspection_data(scene, base_plans, execution_plans, positions=..., evaluations=..., timestamps=...)`
reads plans, a saved position mapping and optional JSON observations without
calling Physical. Extra timestamps need saved positions; actual events always
remain visible. `write_inspection` exports a self-contained page and `link.json`.
The standard file entry also writes original-batch details and window navigation.
