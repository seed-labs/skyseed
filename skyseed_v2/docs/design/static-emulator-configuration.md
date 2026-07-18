# Static Emulator Configuration

## Scope

The large static example needs data-driven construction without introducing a
new entity parent or hiding the public ownership API. Its direct path is:

```text
versioned YAML
→ load_static_emulator_config(path)
→ immutable StaticEmulatorConfig
→ create_static_emulator(config)
→ public create/join methods
→ StaticEmulatorCreationResult
→ create_simulator_static_input(creation.skyseed_autonomous_systems)
→ SimulatorStaticInput
→ SkyseedDockerCompiler
```

## Versioned input and configuration

`configuration/yaml.py` owns strict YAML parsing, duplicate-key rejection,
external GroundStation file resolution and schema-version checks. Its direct
function returns `StaticEmulatorConfig`; it does not construct entities.

The frozen backend-independent values are split for discoverability:

- `configuration/satellite.py`;
- `configuration/ground_station.py`;
- `configuration/network.py`;
- `configuration/static_emulator.py`.

Cross-record validation remains in `StaticEmulatorConfig`. Generated IDs are
deterministic and independent of input order. Each former qualified
construction-input type has a one-to-one `*Config` name; no old alias remains.

## Emulator creation

`static_emulator.py` exposes `create_static_emulator(config)`. The function
uses the same public entity and seedemu methods as handwritten callers. It
creates no alternate hierarchy and no population/constellation object.

`StaticEmulatorCreationResult` exposes exactly the Emulator, Skyseed AS tuple
and ordinary AS tuple. It deliberately has no Simulator-input field.

## Simulator input generation

The caller separately invokes
`create_simulator_static_input(creation.skyseed_autonomous_systems)`. This
makes the Emulator → Simulator static-data boundary visible and keeps Config
creation independent of future Simulator data.

No NodeId-to-entity mapping is generated. Future Compiler lookup will follow
the existing Base/AS/Shell/Orbit owner methods on demand and remains outside
this configuration and input-generation flow.

## Formal examples and materialization

The isolated and complete examples remain handwritten. Only
`large_static_network` uses YAML because hundreds of repeated values are data
rather than teaching code. The example compiles and validates Compose output;
it does not build or start 365 services.

Compose materialization consumes Compiler output separately. Configuration
loading and Emulator creation never launch containers, and materialization
does not load Config or reconstruct entities.
