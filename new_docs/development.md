# Development and migration

Use the existing `skyseed_dev` environment; see [installation](../README.md#installation)
for setup. Select checks for the changed behavior:

| Change | Check |
|---|---|
| Installation or source root | `python scripts/check_dev_environment.py` |
| Import dependencies | `python scripts/check_import_dependencies.py` |
| Scene behavior | `python -m pytest new_tests/unit/scene` |
| Emu or component connections | `python -m pytest new_tests/unit/emulator new_tests/connections` |
| Documentation | `python scripts/check_documentation.py --scope focused` |

Run relevant modules and direct consumers; broaden coverage when a shared contract
changes. [Test organization](../new_tests/README.md) describes each area. Live
Docker deployment is a separate check documented in the example.

## Active source and layout

[pyproject.toml](../pyproject.toml) selects **only `new_src`**, published as `skyseed`,
and collects tests from `new_tests`. Development checks and pytest verify the
resolved source. The retired `src` package and test suite fail explicitly;
unimplemented features never fall back to old code. Examples use normal imports.

Keep related operations together and split files by responsibility. Module READMEs
explain local ownership, topic guides own interface rules, and examples show real
usage. Use specific domain names and diagrams that clarify relationships.
Keep normal execution readable, validate common mistakes at the owning boundary,
and give errors useful entity or field context.

Scene stays independent of SEED and model algorithms. Extend it when a consumer
needs additional scenario facts; keep model settings with their owner.

## Final directory switch

`new_src`, `new_tests`, `new_examples` and `new_docs` are temporary locations.
When replacement is complete, update packaging, build configuration, test/example
entry points and documentation links together with the rename. Reinstall and
verify imports in fresh processes, the built wheel and package resources.
[setup.cfg](../setup.cfg) and [setup.py](../setup.py) keep obsolete build-cache
modules out of the package; retain that protection during the switch.

Preserve research, historical evidence, experiment outputs and unrelated work.
Directory migration, commits and publication follow their own agreed scope.
