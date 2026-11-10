# Link plan inspection

**English** · [中文](README.zh-CN.md)

Inspect actual [Link](../../../new_src/skyseed/pc/link/README.md) decisions,
setup timing, operational connections and base/execution branch differences.

```bash
python -m tools.observation.link.inspection --output /tmp/skyseed-link-view
```

Run from the repository in `skyseed_dev`; open the generated `index.html` offline.
The synthetic 64-satellite / three-city-point scene is shared with the
[Scene inspector](../scene/README.md). The demo samples every five seconds and
uses **7.5-second setup** to make events between samples visible; that setting
is illustrative, not a new core default. Both branches match without a perturbation.
Use `--scene`, `--position-model sgp4`, `--start`, `--seconds` or `--setup-seconds`
to change inputs. Custom models are configured through Python.

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

`inspection_data(physical, base_plans, execution_plans, evaluations=..., timestamps=...)`
reads real contiguous plans and optional `LinkEvaluation` observations. It queries
Physical only for display positions; it never calls selection, generation or
Perturbation. `timestamps` adds display samples; the interval start, actual operations
and recorded evaluations are always retained. Ascending state queries sweep plan
operations once. `write_inspection`
writes `link.json` and a self-contained page using the shared globe assets.
Custom exported fields must be JSON serializable. The demo also calls the shared
helper in `tools.observation._presentation.plan_details`:
`write_plan_details(output, kind="link", streams={"base": ..., "execution": ...})`
and sets `data["plan_details"] = "plans/index.html"` before writing the main page.
The detail tables use saved-and-restored plans, compared with the originals.
Plan files contain logical plans; geometry and evaluation observations remain in
the parent page's display data. Planning, display sampling, plan files/details and
main-page writing costs are reported separately. Global Tool integration is deferred.
