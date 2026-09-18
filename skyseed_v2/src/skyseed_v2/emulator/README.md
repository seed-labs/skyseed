# Emulator scene construction

[Entities](entities/README.md) represent authored Shells, Orbits, Satellites and
GroundStations. [SEED integration](seedemu/README.md) registers them in the upstream
Emulator. Examples show construction directly; configuration loaders are optional.

The external [adapter](../adapters/README.md) projects the scene into immutable
Simulator input. Emulator neither advances simulation time nor owns Plans or BPF.
[Ownership and identity](../../../docs/development/component-boundaries.md) is the contract.
