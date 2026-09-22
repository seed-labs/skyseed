# Performance Tool

Select generation or a runtime invocation, expand component/stage rows, and zoom
or pan through measured spans. Span widths show recorder-relative monotonic duration;
work/resource detail explains the associated operation. Rollups overlap their child
spans and must not be summed as independent work. Sources retain separate time bases;
unrelated recorder zero points are never aligned into a synthetic experiment clock.

Missing instrumentation is not zero cost. The page shows recorded facts rather than
rerunning the experiment or presenting historical timings as hardware requirements.

Shared Compiler measurements use `semantic-to-backend-lowering`. Historical
`semantic-to-bpf-lowering` records retain their original labels and remain included
in the Compiler group; neither label claims kernel execution.

[Open the interactive Performance demo](../../../docs/demos/s5/performance/index.html).
Its [provenance](../../../docs/demos/README.md) identifies the scenario, interval and
historical evidence; it does not validate current live execution.

To browse your own output, run a maintained example with Tools enabled and open
`output/visualizations/index.html` after a completed request. `--operation generate`
selects finite generation. Runner/Traffic views additionally require actual execution
records. See [outputs and Tools](../../../docs/user-guides/outputs-and-tools.md).

Generated `run.sh` uses the completed-range CLI. To render a finished invocation
separately, use `python -m tools.observation.performance --generated OUTPUT --observations OBSERVATIONS --output VIEW`,
where `OBSERVATIONS` is its `runs/<id>/performance/execution-observations.jsonl`.
The CLI pins one completed publication, including the referenced generation records,
while retained calculation may continue appending later observations.
