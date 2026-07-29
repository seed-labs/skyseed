# Terminology navigation

This is a short disambiguation index, not a second contract. Follow each link
for exact semantics.

| Term | Meaning here | Contract |
|---|---|---|
| Emulator | upstream SEED composition and the Skyseed entities it owns | [ownership](../codex-reference/ownership-lifecycle.md) |
| Emulator entity | Shell/Orbit/Satellite/GroundStation used by SEED construction | [Emulator boundary](../codex-reference/emulator-simulator-boundary.md) |
| extension-node lifecycle index | AS-held same-object reference used for SEED registration/configuration, not Satellite domain ownership | [ownership](../codex-reference/ownership-lifecycle.md) |
| future functional Layer | unfrozen Skyseed boundary for shared software/files/commands/daemons; no current class or API | [extension points](../codex-reference/extension-points.md) |
| Simulator input | immutable receiver-owned copied scalar DTO; never an Emulator object | [Emulator–Simulator boundary](../codex-reference/emulator-simulator-boundary.md) |
| Simulator | composition root that creates one Topology then one Slicer | [ownership](../codex-reference/ownership-lifecycle.md) |
| Topology | immutable Simulator static aggregate and lookup facade | [Topology/Zone](../codex-reference/topology-zone-contracts.md) |
| Position | one authoritative `EarthFixedPosition` per spatial node and timestamp | [Position/Connection](../codex-reference/position-connection-values.md) |
| Connection | currently effective bidirectional dynamic edge with directional parameters | [Position/Connection](../codex-reference/position-connection-values.md) |
| G target | a `GroundStationRouteTarget` carrying GroundStation `NodeId` | [routing](../codex-reference/routing-path-contracts.md) |
| Z target | a `ZoneRouteTarget` carrying ZoneId; terminal is current service Satellite | [routing](../codex-reference/routing-path-contracts.md) |
| Slice 0 | synthetic empty baseline, not a Timeline instant | [Timeline/Slice/Difference](../codex-reference/timeline-slice-difference-contracts.md) |
| TopologySlice | one atomically published immutable Position/Connection/Route result | [Timeline/Slice/Difference](../codex-reference/timeline-slice-difference-contracts.md) |
| TopologyDifference | on-request sparse change for retained adjacent Slices | [Timeline/Slice/Difference](../codex-reference/timeline-slice-difference-contracts.md) |
| Inspection Tool | repository-local read-only projection/display/artifact pipeline | [user guide](../user-guides/inspection-tools.md) |
| ComposeProjectLauncher | launcher/verifier for an already compiled static Compose project | [system overview](system-overview.md) |

The object names and owner cardinalities are in
[`object-model.md`](object-model.md). Current implementation status appears
only in [`CURRENT.md`](../CURRENT.md).
