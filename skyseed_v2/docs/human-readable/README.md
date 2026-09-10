# Human-readable architecture

These short documents are the graphical human review surface. Each global
diagram has exactly one maintenance home:

- [qualification 性能与工作量视图](qualification-performance.md)
- [从配置构建、定制和执行卫星网络](framework-construction-execution.md)
- [system overview](system-overview.md)
- [Emulator chain](emulator-chain.md)
- [Simulator chain](simulator-chain.md)
- [object/class relationships](object-model.md)
- [terminology navigation](terminology.md)
- [repository/import map](repository-map.md)
- [examples/Tools/tests traceability](traceability-map.md)
- [已采用优化与宿主机配置](optimization-decisions.md), with links to the
  [detailed decision history](../provenance/optimization-decision-history.md)

`object-model.md` is the only global object/class diagram. Complete local
class/member diagrams live only in each production owner package README under
`../../src/skyseed_v2/`. Detailed normative prose belongs in
`../codex-reference/`, not here.
Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
