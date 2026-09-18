# Planner Tool

Browse INPUT → MODEL → RESULT/CHANGE, then select time, Shell, entity or a route
endpoint. Connection lifecycle distinguishes preparation from operational state;
route paths use the selected operational graph. Shell colors identify scope, while
relationship highlights and change markers explain the current selection. Unserved
Zones and unreachable routes remain explicit outcomes.

Use path browsing to inspect hops at the chosen time. S3 additionally exposes Areas,
ABRs and directed cost details from its captured optional explanation. The
[Area demo](../../docs/demos/s3/planner/index.html) uses the ordinary Planner page.
Missing optional diagnostics are displayed as unavailable, not fabricated.

[Open the interactive Planner demo](../../docs/demos/s5/planner/index.html).
Its [provenance](../../docs/demos/README.md) identifies the scenario, interval and
historical evidence; it does not validate current live execution.

To browse your own output, run a maintained example with Tools enabled and open
`output/visualizations/index.html` after a completed request. `--operation generate`
selects finite generation. Runner/Traffic views additionally require actual execution
records. See [outputs and Tools](../../docs/user-guides/outputs-and-tools.md).
