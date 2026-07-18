# Generic Static Scenario Construction

## Scope

The large static example needs data-driven construction without introducing a
new entity parent or hiding the public ownership API. The file-driven path is:

```text
versioned YAML
→ StaticEmulatorScenarioLoader
→ immutable specifications
→ StaticEmulatorSpecificationApplier
→ public create/join methods
→ SimulatorStaticInput
→ SkyseedDockerCompiler
```

## Versioned input and serialization

`serialization/static_emulator_yaml_loader.py` owns strict YAML parsing,
duplicate-key rejection, external GroundStation file resolution and schema
version checks. It returns `StaticEmulatorScenarioSpecification`; it does not
construct Emulator entities.

## Specification packages

The immutable backend-independent graph is split for discoverability:

- `specifications/satellite.py`;
- `specifications/ground_station.py`;
- `specifications/network.py`;
- `specifications/scenario.py`.

Cross-record validation remains in the complete scenario specification.
Generated IDs are deterministic and independent of input order.

## Application

`application/static_scenario_application.py` owns one heterogeneous operation:
apply a complete validated specification through the same methods used by
handwritten callers. It creates no alternate Shell/Orbit/Satellite hierarchy
and introduces no population or constellation object.

The result exposes the Emulator, Skyseed AS tuple, ordinary AS tuple and
`SimulatorStaticInput`. No one existing entity truthfully owns all four values,
so the result remains application-only.

## Formal examples

The isolated and complete examples remain handwritten. Only
`large_static_network` uses YAML because hundreds of repeated construction
values are data rather than teaching code. The large example remains
compile/config-only and does not start 365 services as part of this baseline.

## Materialization boundary

Compose materialization consumes Compiler output separately. YAML application
does not launch containers, and materialization does not load specifications or
reconstruct entities.
