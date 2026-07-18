# Complete Static Emulator

This handwritten example shows the complete public composition: two isolated
Satellites, one GroundStation, one ordinary SEED AS/Network/Router, one IX, one
explicit eBGP peering, immutable Simulator static input and the Compiler.
Static input is produced explicitly with
`create_simulator_static_input((skyseed_as,))`. The same Skyseed AS tuple is
passed to `create_emulator_node_index((skyseed_as,))`, then the example checks
that both `NodeId` sets are equal before compiling. The ordinary AS is not
passed to either fail-closed boundary, and the entity index is not passed to a
Simulator. `operator` is descriptive metadata only. No YAML facade,
GS–Satellite link or dynamic Simulator field exists.

Generate the complete output from `skyseed_v2/`:

```bash
python3 -m examples.complete_static_emulator.main --override
```

The Compiler expresses locally built SEED base images as Compose `service:`
additional build contexts, so a maintained Docker installation can use its
default BuildKit builder directly. Start the generated project with:

```bash
examples/complete_static_emulator/output/start.sh
```

The generated controls use the exact Compose project
`skyseed_v2_complete_static_emulator`.

Cleanup is project-scoped, idempotent and retains the generated output:

```bash
examples/complete_static_emulator/output/cleanup.sh
```
