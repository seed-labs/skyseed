# Traffic Tool

Compare recorded probes and packet-path telemetry with planned paths at the
corresponding simulation time. Select an episode/transaction, seek time or play the
replay; Shell filters narrow visible scope. Planned paths, observed hops and missing
observations have distinct legend entries. A missing observation does not prove a
packet was absent, and planned reachability does not prove delivery.

Clock holds/resumes come from recorded runtime segments. The page preserves actual
send/receive timing, loss and qualification evidence. Replay uses relative local
assets and never sends packets. Explicit active probing belongs to `traffic.active`
and is enabled through experiment configuration, not by opening this page.

[Open the interactive Traffic demo](../../docs/demos/s5/traffic/index.html).
Its [provenance](../../docs/demos/README.md) identifies the scenario, interval and
historical evidence; it does not validate current live execution.

To browse your own output, run a maintained example with Tools enabled and open
`output/visualizations/index.html` after a completed request. `--operation generate`
selects finite generation. Runner/Traffic views additionally require actual execution
records. See [outputs and Tools](../../docs/user-guides/outputs-and-tools.md).
