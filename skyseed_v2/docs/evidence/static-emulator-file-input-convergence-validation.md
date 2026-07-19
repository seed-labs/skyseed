# Static Emulator File-Input Convergence Validation

Date: 2026-07-19

## Accepted contract

```text
YAML
→ load_static_emulator_config(path)
→ StaticEmulatorConfig
→ create_static_emulator(config)
→ upstream seedemu.core.Emulator
→ Emulator.getLayer("Base")
→ SatelliteBase.get_skyseed_autonomous_systems()
→ create_simulator_static_input(...)
```

There is no result wrapper, Emulator subclass, public Loader class, fragment
schema or second creation path.

## Upstream SEED ground-network file investigation

The installed distribution is seedemu 0.0.7 from the workspace checkout at
commit `61acdf4c9699b25b06843ec4aad747108b05924e`.

1. **Declarative ordinary topology reader:** none was found for AS, Router,
   Network, IX and eBGP topology. Their formal APIs are imperative methods on
   `Base`, `AutonomousSystem`, `Node` and `Ebgp`.
2. **Nature of file-related capabilities:** `Emulator.dump/load` serializes
   the complete Registry with pickle. Example YAML is a test-runner manifest;
   Kubernetes YAML configures deployment infrastructure. Neither is a topology
   declaration API.
3. **Composition into the same Emulator/Base:** imperative public methods can
   add objects to the same Base. `Emulator.load` replaces the target Registry
   and layers; `Emulator.merge` returns a new Emulator and needs explicit
   merger support for overlapping layer types.
4. **Post-load lookup and GroundStation attachment:** a complete dump can
   restore its own layers, but it is not a ground-network fragment contract.
   The supported path constructs one `SatelliteBase`, then uses its inherited
   `getAsns()`, `getAutonomousSystem()` and IX APIs before `joinNetwork()`.
5. **Cross-file references:** no formal declarative cross-file topology
   reference mechanism exists.
6. **Lifecycle:** dump is forbidden after render; load itself does not render
   or compile, but it restores a whole Emulator state rather than an open
   declaration fragment. Test-runner manifests orchestrate lifecycle and are
   not reusable library topology input.
7. **Pinned-commit usability:** the imperative APIs used here and the Base
   query methods are present at the pinned commit. No stable reusable upstream
   file reader satisfies the approved Skyseed contract.

Conclusion: `configuration/ground_network.py` remains a deliberately thin
adapter for the complete static example. It is not a general SEED configuration
framework. A future split into separate physical files remains a later design
choice.

## Name and module convergence

- Config roots retain direct `*Config` names.
- `OrbitGenerationConfig` became `GeneratedOrbitLayout`.
- distribution, attachment and eBGP endpoint helper values lost misleading
  `Config` suffixes.
- Python Shell fields are `generated_orbit_layout` and `explicit_orbits`.
- generated-layout fields are `right_ascension_distribution` and
  `in_orbit_phase_distribution`.
- symmetric unfiltered eBGP endpoints are one canonicalized `peer_routers`
  pair. YAML still uses `first` and `second`.
- `configuration/network.py` split into `ground_network.py` and
  `attachments.py`.
- top-level `static_emulator_creation.py` became `static_emulator.py`.
- no compatibility aliases exist; YAML schema versions and keys are unchanged.

## Real-owner AS query

`SatelliteBase.get_skyseed_autonomous_systems()` is the only new public query.
It returns only `SkyseedAutonomousSystem` instances owned by that Base, sorted
by ASN through existing upstream `getAsns()` and `getAutonomousSystem()` calls.
It stores no parallel collection and imports no Config, Simulator, compiler or
runtime type.

## Validation

The final verification produced these results:

| Scope | Command or check | Outcome |
| --- | --- | --- |
| Python sources | `../.venv/bin/python -m compileall -q src tests examples` | passed |
| Complete suite | `PYTHONPATH=. ../.venv/bin/pytest -q` | 151 passed |
| Patch hygiene | `git diff --check` | passed |
| Config paths | programmatic Config, unchanged YAML fixture and handwritten construction | equivalent Simulator input, Registry membership and Compose semantics |
| Public contract | import/annotation/absence tests | direct upstream `Emulator`; no result wrapper, old module, Emulator subclass or fragment loader |
| Wheel | clean build, content inspection and non-editable isolated install | passed against the pinned upstream source checkout |
| Documentation | relative-link scan and all Mermaid blocks through the official Mermaid renderer | links passed; 21 diagrams rendered |

The package index does not provide the pinned `seedemu==0.0.7`. A wheel built
from the pinned upstream checkout also omits an Ethereum template asset, which
is an upstream packaging defect. The non-editable Skyseed wheel was therefore
validated in a fresh environment against an editable install of that exact
upstream commit; Skyseed itself was not installed editable. Its contents
include `skyseed_v2/static_emulator.py`, `configuration/attachments.py` and
`configuration/ground_network.py`, and exclude the two replaced modules.

Three real, non-test construction and compilation runs were also completed:

- isolated static example: render and Docker/Compose compile passed; Compose
  validation found 3 services;
- complete static example: render and Docker/Compose compile passed; Compose
  validation found 6 services;
- large YAML example: 348 Satellites, 12 GroundStations and 360 Simulator
  static-input records were created; Docker/Compose compile passed and Compose
  validation found 364 services with InternetMap intentionally disabled.

The complete example was additionally built and started. Four business
containers ran, both compiler-helper containers exited with status 0, BIRD on
`gs-new-york` established its eBGP session with AS65002, and the station learned
`10.202.0.0/24` through that peer. Three ICMP probes to `10.202.0.254` succeeded
with 0% packet loss. The cleanup script then removed every container and
network belonging to the Compose project.

## Architecture Delta

- **Public types renamed:** six auxiliary configuration values received their
  approved precise names; Config root/entity declaration types retained Config.
- **Public type removed:** `StaticEmulatorCreationResult`.
- **Public function moved:** `create_static_emulator` moved from
  `skyseed_v2.static_emulator_creation` to `skyseed_v2.static_emulator` and now
  returns upstream `Emulator` directly.
- **Public method added:** narrow
  `SatelliteBase.get_skyseed_autonomous_systems()`.
- **Fields renamed:** generated/explicit Shell modes, both distribution fields
  and symmetric eBGP endpoint pair; YAML keys are unchanged.
- **Module boundaries:** `configuration/network.py` split into
  `ground_network.py` and `attachments.py`; top-level creation module renamed.
- **Data ownership:** unchanged. SatelliteBase remains the AS owner; AS, Shell
  and Orbit retain entity ownership.
- **Root exports:** unchanged.
- **Execution path:** result-wrapper hop removed; file input now returns the
  real Emulator and queries ASes from SatelliteBase.
- **Simulator:** static input contract unchanged; algorithms and engine remain
  not started.
