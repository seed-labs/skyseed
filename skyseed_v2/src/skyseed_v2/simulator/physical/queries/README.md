# Physical query values

`PhysicalLinkRequest` bounds requested ISL pairs, GS access and Zone service work.
`PhysicalLinkQueryResult` returns candidates at exactly the input snapshot timestamp.
Candidates report feasibility and optional measures/contact windows; they are not
selected operational connections. Unknown future feasibility remains unknown.

```mermaid
flowchart LR
    Snapshot[PhysicalPositionSnapshot] --> Model[PhysicalLinkModel]
    Request[PhysicalLinkRequest] --> Model
    Model --> Result[PhysicalLinkQueryResult]
    Result --> ISL[ISL candidates]
    Result --> GS[GS access candidates]
    Result --> Zone[Zone service candidates]
```

[Physical-link Model](../models/links/README.md) implements the calculation;
[physical contracts](../../../../../docs/development/component-boundaries.md) define
value and timestamp semantics. Optional search limits and prediction are documented
in the [Model contract](../../../../../docs/development/model-interfaces.md).
