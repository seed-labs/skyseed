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
SEED, the Etcd client libraries and the optional pytest test dependency. pip installs their transitive dependencies.
No Docker or privileged execution is needed for calculation or local browser viewing.

SkySEED requires **ExtensionNode support** from
[`EMLionHeart/seed-emulator`, branch `development`](https://github.com/EMLionHeart/seed-emulator/tree/development).
An arbitrary upstream checkout or the metadata version `seedemu==0.0.7` does not
establish that capability. The tested dependency commit is
[`05ad40bd22374496691d4b4fd516b4ee9b36eb4b`](https://github.com/EMLionHeart/seed-emulator/commit/05ad40bd22374496691d4b4fd516b4ee9b36eb4b):
one generic ExtensionNode commit on upstream `seed-labs/seed-emulator` development
[`97ead673aaaa4cd65f28cebc6719390913e8fdb7`](https://github.com/seed-labs/seed-emulator/commit/97ead673aaaa4cd65f28cebc6719390913e8fdb7).
This dependency relationship was checked on 18 September 2026; it is not a claim
about today's upstream branch head.

The native software adapter uses ordinary Node methods and requires no local Routing
convenience APIs. **Dynamic Internet Map support is a separate local adaptation** of
`tools/InternetMap2`: applied-connection handoff, project scoping and capture/terminal
fixes are not supplied by the ExtensionNode commit alone or guaranteed in unmodified
upstream. Deployer builds the Map source from the installed checkout. To reproduce the
recorded Map behavior, use the separately reviewed local Map changes; use
`--no-internet-map` when that optional support is unavailable. SEED changes are maintained
and reviewed separately from SkySEED.

```bash
# From your workspace directory, alongside the cloned skyseed source tree:
git clone --branch development --single-branch https://github.com/EMLionHeart/seed-emulator.git
cd skyseed
bash scripts/setup_dev_environment.sh
conda activate skyseed_dev
python scripts/check_dev_environment.py
python -m pip check
python scripts/setup_interaction.py
```

The script installs the sibling SEED source and SkySEED in editable mode, including
pytest. For a different dependency location, run
`SEED_EMULATOR_CHECKOUT=/path/to/seed-emulator bash scripts/setup_dev_environment.sh`.
It checks the actual ExtensionNode interface after installation. The branch may evolve;
the tested commit is a reproducibility reference, not a whole-source fingerprint rule.
For exact reproduction, fetch the fork and `git checkout --detach
05ad40bd22374496691d4b4fd516b4ee9b36eb4b` in its checkout before running setup.

The separate `setup_interaction.py` command installs a checksum-verified, pinned Etcd
binary under your user directory; pip alone does not install that service executable.
Generation starts an output-owned member even at partial cutoffs. An explicitly
configured external endpoint is the alternative. See [Etcd setup, discovery and
ownership](computation-and-controls.md#interaction-setup-and-independent-programs).

Verify which source Python imports; keep the environment and editable source tree together:

```bash
python -c 'import sys, seedemu, skyseed; from seedemu.core import ExtensionNode; print(sys.executable); print(seedemu.__file__); print(skyseed.__file__); print(ExtensionNode)'
git -C ../seed-emulator rev-parse HEAD   # use your chosen checkout path if different
```

Select that interpreter in your editor. Examples use the caller's interpreter; they
do not auto-switch environments. The environment check reports Conda identity,
Python version and import locations, as well as required SEED capability.

## Migrating an existing development installation

The application now lives at the repository root, with Python code in `src/skyseed`.
The distribution and import package are both `skyseed`; update authored imports from
`skyseed_v2` to `skyseed`. The distribution version remains 0.1.0 and the Conda
environment remains `skyseed_dev`. In an existing environment with dependencies
already installed, replace the old editable distribution through pip:

```bash
conda activate skyseed_dev
# From the migrated repository root:
python -m pip uninstall skyseed-v2
python -m pip install --no-deps --no-build-isolation --editable .
python scripts/check_dev_environment.py
```

There is no old-import compatibility package or path shim. Existing output trees
and retained experiment evidence stay at their original locations, including local
`skyseed_v2/examples/*/output` directories. Their static results remain available for
inspection. Old generated execution scripts can depend on the old package and source
layout and are not promised to run with the renamed installation. Regenerate new
experiments with the migrated code in fresh output directories. Historical reports
retain their original commands and measurement paths.

## Installation smoke test: Tiny

```bash
# Terminal 1, in skyseed with skyseed_dev active:
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

Wait for Terminal 1 to return, inspect the cancellation result or closed owner outcome,
then run `python -m skyseed.experiment.interaction_service stop .` in the output
directory if you no longer need online result queries. Keep the output and database.
For a finite calculation, explicitly use
`--operation generate` instead. Output defaults to the entry's directory even when
invoked from elsewhere. Existing inactive output requires explicit `--override`;
use `--output PATH` to preserve an earlier result.

To verify live Tiny, complete [Linux host setup](linux-host.md), leave the computation
retained, and use a second terminal for `./deploy.sh` then `./run.sh` in its output.
Use a third activated terminal for `python experiment_control.py status`. Runner
reaches the requested coverage at ACTIVE `2026-01-01T00:00:30+00:00` and four activations.
With Traffic enabled, Tiny's default workload has **two fixed communications**: one
GS/Zone pair in both directions. Read the actual IDs, interval and intended wall-time
duration from `results/traffic/workload.json`; changing the scene or options can
change that workload. Delivery boundaries do not schedule new probe episodes.

Use a fresh, available runtime observation; stale status cannot confirm completion.
In the status JSON, `runtime.traffic` reports `communications` (configured count),
`senders` (currently active segments, **not** cumulative launches), `records` (all
written evidence rows, not packets sent), `complete` and `error`. Source migration
can start a new segment under the same communication ID. Zero senders alone proves
neither completion nor connectivity. For full Traffic-duration coverage, leave the
retained runtime running until `complete` is true with `error` null, without an early
stop/reset; then inspect the evidence. Completion means the Traffic worker ended,
even after cancellation or failure. The ACTIVE coverage boundary alone does not prove
Traffic completion, successful sending, or replies.

From the output directory, with `skyseed_dev` active, this read-only inspection pins
`runs/latest.json` to one invocation and shows each configured communication's actual
launches, replies and terminal sender summaries. It reads complete JSONL rows; rerun
after drainage for final evidence. To inspect an older run, set `run` to its directory.

```bash
python - <<'PY'
from collections import Counter
import json
from pathlib import Path

run = Path("runs") / json.loads(Path("runs/latest.json").read_text())["invocation_id"]
workload = json.loads(Path("results/traffic/workload.json").read_text())
print("invocation:", run, "workload:", workload)
counts, stopped = Counter(), {}
with (run / "traffic/probe-execution.jsonl").open() as stream:
    for line in stream:
        if not line.endswith("\n"):
            continue  # In-progress trailing row; inspect again after drainage.
        row = json.loads(line)
        identity = row.get("communication_id")
        counts[identity, row["record_type"], row.get("observation")] += 1
        if row["record_type"] == "generator_stopped":
            stopped.setdefault(identity, []).append({key: row.get(key) for key in
                ("segment_index", "sent", "received", "loss", "stop_reason", "cleanup_result")})
for communication in workload["communications"]:
    identity = communication["communication_id"]
    print(identity, "launches:", counts[identity, "generator_launched", None],
          "reply observations:", counts[identity, "ping_observation", "reply"],
          "sender summaries:", stopped.get(identity, []))
outcome = run / "runner/runner-invocation.json"
print("runtime outcome:", json.loads(outcome.read_text())["runtime_outcome"]
      if outcome.exists() else "not published yet")
PY
```

The printed invocation directory retains raw ping lines, timestamps, ACTIVE frontiers,
source-unavailable/migration records and per-segment identities. A launch only proves
that a sender was started. Terminal `sent`/`received`/`loss` use ping's summary and
remain null when it is missing; reply observations may include duplicates. For a
reply smoke check, require a recorded reply for **each communication ID**, and report
losses and missing replies as observed. This proves only sampled delivery, not
continuous connectivity or intermediate hops. See the [Traffic observation guide](../../tools/observation/traffic/README.md).

Request `python experiment_control.py shutdown`, retain its owner-to-receipt map,
and wait for both foreground processes to return. Query each receipt with
`python experiment_control.py result '<receipt JSON>'` and inspect closed owner
outcomes: runtime `traffic` and `cleanup` must report successful drainage, and
computation must close without errors. An accepted shutdown request, Traffic
`complete`, or a generated page alone does not establish this. Run `./cleanup.sh`
and check its separate deployment completion. Then explicitly stop the output-owned
service with `python -m skyseed.experiment.interaction_service stop .`; retain its
database and results. Runtime views under `visualizations/` are generated after the
invocation and may lag live status. The [control guide](computation-and-controls.md)
describes these independent owners and continuation, pause/resume and reset.

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
and the [example index](../../examples/README.md) give the next steps.

## Verify a candidate in isolation

Use a fresh source directory containing the **complete candidate**, including intended
uncommitted files; a checkout of an older HEAD alone does not test that candidate.
Retrieve SEED independently over the public HTTPS path above. Choose a new prefix:

```bash
# In the fresh candidate's skyseed directory:
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

C/eBPF execution-material preparation also requires Clang/LLVM BPF support, Linux
UAPI and libbpf development headers, plus `libbpf.so.1`. See the
[build versus execution requirements](../development/dataplane.md#dependencies-and-validation-boundary).
Existing Example configuration remains unchanged.
