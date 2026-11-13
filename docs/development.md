# Development

[中文](development.zh-CN.md)

Use the existing `skyseed_dev` environment. [Installation](../README.md#installation)
sets up a new environment when needed. `pyproject.toml` selects `src` as the only
package root and `tests` for default collection. Examples import the installed
`skyseed` package without editing Python's search path.

## Development scripts and result tools

`scripts/` installs and checks the repository; a simulation does not require
running every script. `tools/` provides optional image building and views of
scenes, plans and execution records for examples and users.

| File in `scripts/` | Purpose |
|---|---|
| `setup_dev_environment.sh` | Install the development environment and adjacent SEED checkout |
| `check_dev_environment.py`, `check_active_source.py` | Verify the interpreter, imported source and SEED capabilities; tests also use the latter |
| `check_import_dependencies.py` | Check Scene and offline P&C dependency boundaries |
| `check_documentation.py` | Check current Markdown links, anchors and code fences |
| `check_mermaid_render.py` | Render document diagrams using a supplied Mermaid file and Firefox |
| `_webdriver.py` | Private browser helpers for the rendering script |

## Choose checks for the change

| Change | Check |
|---|---|
| Installation or package paths | `python scripts/check_dev_environment.py` |
| Module dependencies | `python scripts/check_import_dependencies.py` |
| Module behavior | `python -m pytest tests/unit/<module>` |
| Adjacent handoff | `python -m pytest tests/connections` |
| Complete offline flow | `python -m pytest tests/integration` |
| Package contents and wheel import | `python -m pytest tests/unit/environment/test_packaging.py` |
| Current documentation links | `python scripts/check_documentation.py` |

Run affected modules and direct consumers first. The default suite is offline;
[live tests](../tests/README.md) require explicit environment settings. Directory
or packaging changes warrant the current offline suite and a real wheel check.
A documentation or display change does not require a long network experiment.
Compiler and wheel checks need the clang/Linux headers from the
[runtime setup](usage.md#runtime-requirements), but do not start Docker.
Node.js on `PATH` enables the JavaScript replay checks; they are skipped when it
is unavailable. Set `SKYSEED_TEST_NODE` to an executable if it is installed elsewhere.
The link checker covers maintained Markdown documentation; generated HTML templates
are checked in their exported output, and historical research notes are not release guides.

Keep operations with their actual owner, use clear names and readable ordinary
control flow. Scene owns facts; models own algorithms/settings; Compiler owns
stable backend instructions; Runner owns live resources. Do not add compatibility
fallbacks, duplicate managers or repeated validation of the same fixed input.
Use small real fixtures and regression tests that detect behavior independently
of implementation spelling. Report duration for expensive work.

Module READMEs own interfaces and key output fields. Keep English and Chinese
versions aligned; guides link to contracts instead of duplicating them. Examples
show necessary calls, with experiment-specific facilities clearly separated.
Explain responsibilities or flow with a diagram, key data with a table, and usage
with a short example. Keep detailed boundaries in expandable sections where helpful.

## Branch collaboration and releases

| Branch | Purpose | Destination |
|---|---|---|
| `feature/ut` | UT development | PR → `development` |
| `feature/perturbation` | Link Perturbation development | PR → `development` |
| `development` | Core development, integration and acceptance | PR → `main` |
| `main` | Releases | Retained stable versions |

Keep using `development` after a release. Prefer a merge commit into `main` for
this long-lived branch, preserving shared ancestry. Then fast-forward local
`development` to `origin/main` when it has not diverged, and push that update.
Approve and Merge are distinct: PR authors cannot approve their own PR, but can
merge it with the necessary permissions once repository rules are satisfied.

After a core-interface update, feature developers save their local work, then
merge `origin/development` into their own branch:

```bash
git fetch origin development
git switch feature/ut
git merge origin/development
```

Use the same steps for `feature/perturbation`. Resolve conflicts against the new
interfaces without restoring retired directories. Use ordinary merges on shared
branches to preserve others' commits; submit completed features to `development`.

Keep research changes separate from a code release. Local workflow notes are not
part of the installed API. `setup.py` clears stale package build files before
building; preserve this behavior when reorganizing modules.
