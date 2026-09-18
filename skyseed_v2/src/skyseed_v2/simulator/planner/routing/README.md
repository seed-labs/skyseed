# Route planning

The default gateway hop-count Model builds per-scope operational graphs and
requested destination tables. It ranks geographic gateway candidates, preserves
one deterministic forwarding next hop and reports explicit unreachable outcomes.
GSs are terminals; Zone destinations use independently selected service satellites.

`model.py` defines the role and results; `gateway.py` provides default policy;
private graph, demand and computation helpers implement that algorithm. Graph and
destination reuse avoids repeating unchanged work without retaining all history.
[S3](../../../../../examples/Sky03_s3_ospf/README.md) replaces this role locally with
Area-based OSPF computation. [Planner reference](../../../../../docs/development/planner-state-and-plans.md)
owns the common route and Plan guarantees.
