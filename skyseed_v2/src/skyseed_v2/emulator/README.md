# Emulator scene construction

[Entities](entities/README.md) represent authored Shells, Orbits, Satellites and
GroundStations. [SEED integration](seedemu/README.md) registers them in the upstream
Emulator. Examples show construction directly; configuration loaders are optional.

The external [adapter](../adapters/README.md) projects the scene into immutable
Simulator input. Emulator neither advances simulation time nor owns Plans or BPF.
[Ownership and identity](../../../docs/development/component-boundaries.md) is the contract.

Optional `configure_native_software(node, daemon=..., configuration=...)` accepts
concrete daemon configurations, supplies dynamic-environment defaults and delegates
to public SEED Node software/file/startup facilities, without Routing convenience methods. It adds no Layer or runtime owner. The
[native software reference](../../../docs/api/assembly-and-control.md#native-routing-software)
defines validation, startup ordering, include/override behavior and the direct-Node alternative.
