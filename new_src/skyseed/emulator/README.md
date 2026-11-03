# Emulator module

- `sky_as.py`: loads Scene nodes into SEED inventories.
- `sky_base.py`: adds the SkyAS factory to SEED Base.
- `sky_emulator.py`: retains SEED's lifecycle and provides global Scene-ID lookup.
- `scene_identity.py`: SEED names, Scene identity labels and ambiguity checks.
- `native_routing.py`: optional daemon configuration through public Node APIs.

Interfaces, services, rendering and deployment compilation remain SEED operations.
This module requires an ExtensionNode-capable SEED installation.
See [Emulator usage](../../../new_docs/emulator.md) and the
[construction example](../../../new_examples/tiny_network/emulator.py).
