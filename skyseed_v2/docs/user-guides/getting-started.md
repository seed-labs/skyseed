# Install and generate your first result

The official SkySEED source is [seed-labs/skyseed](https://github.com/seed-labs/skyseed),
branch `main`. This repository is private: your GitHub account and SSH key must have
access. Install Git, configure that SSH access, then clone from your workspace directory:

```bash
git clone --branch main --single-branch git@github.com:seed-labs/skyseed.git
```

Commands below use `skyseed/` and `seed-emulator/` side by side. You can use other
directories explicitly. The development backup is separate from this official source.

## Python and the required SEED source

Install a Conda distribution using the [official Conda installation guide](https://docs.conda.io/projects/conda/en/latest/user-guide/install/index.html),
then initialize its shell as that guide describes. The setup uses Python 3.12 and pip
from conda-forge. [Package metadata](../../pyproject.toml) declares Astropy 8, PyYAML,
SEED and the optional pytest test dependency. pip installs their transitive dependencies.
No Docker or privileged execution is needed for calculation or local browser viewing.

SkySEED requires **ExtensionNode support** from
[`EMLionHeart/seed-emulator`, branch `development`](https://github.com/EMLionHeart/seed-emulator/tree/development).
An arbitrary upstream checkout or the metadata version `seedemu==0.0.7` does not
establish that capability. The tested dependency commit is
[`05ad40bd22374496691d4b4fd516b4ee9b36eb4b`](https://github.com/EMLionHeart/seed-emulator/commit/05ad40bd22374496691d4b4fd516b4ee9b36eb4b):
one generic ExtensionNode commit on upstream `seed-labs/seed-emulator` development
[`97ead673aaaa4cd65f28cebc6719390913e8fdb7`](https://github.com/seed-labs/seed-emulator/commit/97ead673aaaa4cd65f28cebc6719390913e8fdb7).
Upstream was rechecked on 18 September 2026 without a newer development commit.

```bash
# From your workspace directory, alongside the cloned skyseed source tree:
git clone --branch development --single-branch https://github.com/EMLionHeart/seed-emulator.git
cd skyseed/skyseed_v2
bash scripts/setup_dev_environment.sh
conda activate skyseed_dev
python scripts/check_dev_environment.py
python -m pip check
```

The script installs the sibling SEED source and SkySEED in editable mode, including
pytest. For a different dependency location, run
`SEED_EMULATOR_CHECKOUT=/path/to/seed-emulator bash scripts/setup_dev_environment.sh`.
It checks the actual ExtensionNode interface after installation. The branch may evolve;
the tested commit is a reproducibility reference, not a whole-source fingerprint rule.
For exact reproduction, fetch the fork and `git checkout --detach
05ad40bd22374496691d4b4fd516b4ee9b36eb4b` in its checkout before running setup.

Verify which source Python imports; keep the environment and editable source tree together:

```bash
python -c 'import sys, seedemu, skyseed_v2; from seedemu.core import ExtensionNode; print(sys.executable); print(seedemu.__file__); print(skyseed_v2.__file__); print(ExtensionNode)'
git -C ../../seed-emulator rev-parse HEAD   # use your chosen checkout path if different
```

Select that interpreter in your editor. Examples use the caller's interpreter; they
do not auto-switch environments. The environment check reports Conda identity,
Python version and import locations, as well as required SEED capability.

## Installation smoke test: Tiny

```bash
# Terminal 1, in skyseed_v2 with skyseed_dev active:
python examples/Sky00_tiny_network/tiny_network.py
```

Tiny constructs two satellites, one GS, two Zones and ordinary SEED ground-network
integration nodes. Its normal output is `examples/Sky00_tiny_network/output/`.
It computes 0–30 seconds, publishes results, then waits for continuation. Open
`output/index.html` in a browser; it links the result and visualization entries.
Tools may still be rendering when calculation finishes. From a second activated terminal:

```bash
cd examples/Sky00_tiny_network/output
python experiment_control.py status
# For a calculation-only smoke test, finish by draining the retained process:
python experiment_control.py cancel-computation
```

Wait for Terminal 1 to return. For a finite calculation, explicitly use
`--operation generate` instead. Output defaults to the entry's directory even when
invoked from elsewhere. Existing inactive output requires explicit `--override`;
use `--output PATH` to preserve an earlier result.

To verify live Tiny, complete [Linux host setup](linux-host.md), leave the computation
retained, and use a second terminal for `./deploy.sh` then `./run.sh` in its output.
Use a third activated terminal for `python experiment_control.py status`. Runner
reaches the requested coverage at ACTIVE `2026-01-01T00:00:30+00:00` and four activations.
With Traffic enabled, also let its final scheduled probes launch and produce observations;
reaching that boundary alone does not mean Traffic has caught up. Tiny's Traffic status
should show ten episodes armed and ten generators launched. For a reply-based smoke
check, inspect the invocation's `traffic/probe-execution.jsonl` for a `ping_observation`
with `observation: reply` from each episode before cancellation. Missing replies are a result to investigate,
not a reason to label the run successful.

Request `python experiment_control.py shutdown`, wait for both foreground processes to
return, then run `./cleanup.sh`. Check Traffic's qualification, the invocation's cleanup
outcome and Deployer completion.
The [control guide](computation-and-controls.md) describes those independent owners
and all continuation, pause/resume and reset operations.

## Learn assembly and customization

Run `python examples/Sky02_s4_default/s4.py` for the default S4 assembly: 348 satellites,
355 GSs, 732 Zones, 600 seconds at five-second cadence. Read `build_emulator()` in
[s4.py](../../examples/Sky02_s4_default/s4.py) to see independent input loading,
explicit SEED construction, pure Simulator input and default Model assembly.

[S5](../../examples/Sky01_s5_custom_models/README.md) is the smaller customization
example: 172 satellites, ten GSs and 184 Zones with three local Models. Its normal
entry is `python examples/Sky01_s5_custom_models/s5.py`. Consult the
[scale reference](scale-and-resources.md) before choosing a larger scene.
[Inputs and scenes](inputs-and-scenes.md), [Model authoring](model-extension.md)
and the [six-example index](../../examples/README.md) give the next steps.

## Verify a candidate in isolation

Use a fresh source directory containing the **complete candidate**, including intended
uncommitted files; a checkout of an older HEAD alone does not test that candidate.
Retrieve SEED independently over the public HTTPS path above. Choose a new prefix:

```bash
# In the fresh candidate's skyseed_v2 directory:
export SKYSEED_ENV_PREFIX=/path/to/new/skyseed-check-env
export SEED_EMULATOR_CHECKOUT=/path/to/public-seed-checkout
bash scripts/setup_dev_environment.sh
conda activate "$SKYSEED_ENV_PREFIX"
python scripts/check_dev_environment.py --prefix "$SKYSEED_ENV_PREFIX"
python -m pip check
```

This selects only the explicit prefix, preserving `skyseed_dev`. Do not inherit
`PYTHONPATH` or user-site packages from another installation; use `unset PYTHONPATH`
and `export PYTHONNOUSERSITE=1` before setup and verification. Record commands, Python,
package/import locations, source inventory and SEED commit. Run Tiny at its normal
output location within that tree, including retained waiting and the supported live
controls when authorized. This checks the chosen candidate from a clean tree. Official
source retrieval still requires repository access; SEED's public HTTPS retrieval is a
separate dependency check. See the [development workflow](../development/workflow.md)
for focused checks and [CURRENT](../CURRENT.md) for recorded installation evidence.
