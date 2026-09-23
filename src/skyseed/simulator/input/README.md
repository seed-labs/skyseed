# Pure Simulator input

`SimulatorStaticInput` contains receiver-owned AS, Shell/Orbit/Satellite and
GroundStation values. Satellite records carry identity, epoch, phase and optional
link/access capacities; station records carry identity, location and optional
access capacity. Collections are immutable and contain no SEED object handles.

The [Emulator adapter](../../adapters/README.md) produces them; callers may also
construct the values directly. `SimulationDefinition` combines them with time.
[Input semantics](../../../../docs/development/component-boundaries.md) is authoritative.
