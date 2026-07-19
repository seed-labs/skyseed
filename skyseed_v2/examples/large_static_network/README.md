# Large Static Network

This is a pure v2 scale-validation input. Its local files describe one
6-orbit/348-Satellite Shell, 12 GroundStations, two ordinary ASes, two IXes and
two explicit eBGP peerings. The size was historically informed by an older
experiment, but neither `S4` nor v1 is part of the scenario's identity,
contract or runtime path.

Its visible construction path is
`load_static_emulator_config(path) -> create_static_emulator(config) ->
create_simulator_static_input(creation.skyseed_autonomous_systems)`.
The creation function directly calls the same public AS/Shell/Orbit entity
creation methods shown by the handwritten examples; it does not introduce a
population entity or nested builder chain. Simulator input is a separate
explicit step and receives only the constructed Skyseed AS values.
No NodeId-to-entity mapping is created. Any future dynamic-execution lookup
remains a separate, owner-directed concern and is not Simulator input.

This is the only YAML example because the scale input is large data rather
than teaching code. Loading, creation and Simulator-input generation are
direct functions, not an Emulator entity or mandatory facade. The resulting
`SimulatorStaticInput` is an immutable Simulator input snapshot; it is not the
Config, a SEED Registry, a container inventory or a Compose directory.

Generate and validate without starting 365 services:

```bash
python3 -m examples.large_static_network.main --override
docker compose --parallel 16 \
  -p skyseed_v2_large_static_network \
  -f examples/large_static_network/output/docker-compose.yml config --quiet
docker compose --parallel 16 \
  -p skyseed_v2_large_static_network \
  -f examples/large_static_network/output/docker-compose.yml config --services \
  | wc -l
```

The expected service count is 365. A full build/up is intentionally not part
of the migration baseline. To deliberately build and start the complete
experiment, run the generated control:

```bash
examples/large_static_network/output/start.sh
```

`operator: Starlink` is display metadata only and does not select node type,
ASN, routing or `SkyseedDockerCompiler` behavior.

Cleanup removes only this project's state, if present, and this example's
ignored output:

```bash
examples/large_static_network/output/cleanup.sh
```
