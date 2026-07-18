# SEED Emulator Integration Boundary

## Current contents

Every implementation in `src/skyseed_v2/seed/` is Skyseed-owned extension or
adapter code that imports the separately installed `seedemu` package.

| Current item | Definition/function | Actual responsibility | Upstream interaction |
| --- | --- | --- | --- |
| `autonomous_system.py` | `SkyseedAutonomousSystem` | Owns approved Shell/GS entities while participating in one ordinary SEED AS lifecycle | subclasses `seedemu.core.AutonomousSystem`; registers Satellite/GS instances and extends Router lookup/configuration |
| `base.py` | `SatelliteBase` | Adds first-class Satellite handling to Base prepare/render hooks | subclasses upstream `Base`; delegates to `super`, then handles `satnode` entries |
| `compiler.py` | `SkyseedDockerCompiler`, private `_SatelliteCompilerBoundary` | Extends Docker compilation for isolated Satellite services and stable GS/Satellite metadata | subclasses upstream `Docker`; imports upstream templates; delegates ordinary/GS compilation to `super`; has one fail-closed access to the upstream private service accumulator |
| `metadata.py` | metadata prefixes and `skyseed_metadata_key` | Namespaces Skyseed metadata keys without duplicating entity logic | no upstream implementation copied; only produces key strings compatible with SEED metadata labels |
| `assets/internet_map_none_network_patch.sh` | packaged shell patch | Adjusts the InternetMap image so containers with `network_mode: none` remain representable | copied into a Skyseed-generated image by Compiler; it is a Skyseed package resource, not an upstream Python module tree |
| `__init__.py` | AS/Base/Compiler and constants facade | Stable convenient import surface used by all formal examples | imports Skyseed modules and re-exports selected domain constants |

## Vendoring/copying finding

The repository contains **no vendored `seedemu` package tree**, no copied
`seedemu/core`, `seedemu/layers` or `seedemu/compiler` modules, and no patched
installation of upstream source under this directory. `pyproject.toml` declares
`seedemu==0.0.7` as an external dependency.

The integration is nevertheless coupled to upstream implementation details in
two narrow places:

1. `SkyseedDockerCompiler` imports `DockerCompilerFileTemplates` and uses a
   single fail-closed path to Docker's private service accumulator because
   upstream has no public Satellite dispatch hook.
2. `SatelliteBase` imports `BaseFileTemplates` and extends protected lifecycle
   behavior.

Those are extension dependencies, not vendoring. The custom Compose templates
and InternetMap patch are Skyseed additions for the no-network Satellite case.

## Why `seed/` is ambiguous

To an unfamiliar reader, `seed` may mean:

- vendored upstream SEED Emulator source;
- seed data or database fixtures;
- cryptographic/random seed helpers;
- a generic bootstrap package;
- the actual meaning: Skyseed extensions for the external `seedemu` package.

The short name also differs from the installed import name, `seedemu`, making
it harder to connect `skyseed_v2.seed.SatelliteBase` to upstream SEED Emulator.

## Directory candidates

No candidate is selected here.

| Candidate | Accuracy/readability | Cost and residual ambiguity |
| --- | --- | --- |
| `seedemu` | Matches the external package's known name; concise imports such as `skyseed_v2.seedemu.SatelliteBase` | Can still look like a bundled copy or namespace shadow of upstream `seedemu`; documentation must say “extensions” |
| `seedemu_integration` | Most explicit flat name: every current file is integration code | Longer imports; mixes the framework name with an architectural suffix; moving the public `skyseed_v2.seed` surface affects all examples |
| `integration/seedemu` | Makes ownership direction unmistakable and provides a home for future external-system adapters | Adds a generic `integration` layer and another path segment; may encourage unrelated adapters to collect in one broad directory; current project has only this framework integration plus materialization |
| `seedemu_extensions` | Accurately says subclasses/hooks are Skyseed additions | “extension” may sound like a plugin mechanism supported by upstream, which it is not |
| keep `seed` | No path migration; current terminology already documents it | Remains the least discoverable and easiest to mistake for vendoring |

## Filename candidates

| Current file | Primary public class | More explicit candidate | Discovery improvement | Migration impact |
| --- | --- | --- | --- | --- |
| `autonomous_system.py` | `SkyseedAutonomousSystem` | `skyseed_autonomous_system.py` | class name maps directly to file | subpackage import can remain conceptually identical after an export update, but direct module users and `catalog_builder.py` currently reference the old module path |
| `base.py` | `SatelliteBase` | `satellite_base.py` | removes generic “Base” ambiguity | direct defining-module imports change; all examples use the subpackage facade |
| `compiler.py` | `SkyseedDockerCompiler` | `skyseed_docker_compiler.py` | communicates both owner and backend | direct imports/tests/docs and packaged-resource resolution must be checked; facade imports can be changed mechanically |
| `metadata.py` | key helper/constants | `metadata.py` | already accurate within a clearly named SEED integration directory | little benefit from a longer filename |

Renaming only the three files is lower impact than renaming the `seed`
subpackage. Renaming the subpackage changes all three formal examples and the
application service. Maintaining old aliases would reduce breakage but is
explicitly outside this review and should not be assumed necessary: the API is
still pending user review and no repository release promise was found.
