# Cross-owner input adapters

`create_simulator_static_input(emulator)` projects the authored scene into
`SimulatorStaticInput`. It copies identities, orbital data, site coordinates and
capabilities; it does not give Simulator live SEED entities or callbacks.

Receiver schemas live under [simulator/input](../simulator/input/README.md).
[Ownership and input](../../../docs/development/component-boundaries.md) defines the
one-way boundary; examples invoke it explicitly before declaring time.
