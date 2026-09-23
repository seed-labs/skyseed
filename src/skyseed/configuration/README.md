# Configuration declarations

[Emulator configuration](emulator/README.md) loads static scene declarations;
[Simulator configuration](simulator/README.md) loads or generates geography.
`forwarding.py` owns only the shared forwarding enum and validator, preserving
`backend.ForwardingBackend` as its existing public export. It supplies no execution
policy or resources. Configuration does not construct runtime resources or own calculation state.
Use the [input guide](../../../docs/user-guides/inputs-and-scenes.md) to combine
separate Satellite and GroundStation sources.
