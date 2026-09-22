# Development workflow

Follow [getting started](../user-guides/getting-started.md) to install the required SEED
fork and Python dependencies. The ordinary environment is `skyseed_dev`; use the documented
prefix option for isolated verification. In an established workspace, preserve its existing
environment unless the task authorizes dependency changes. From `skyseed_v2/`:

```bash
conda activate skyseed_dev
python scripts/check_dev_environment.py
python -m pip --version
python scripts/check_import_dependencies.py
python -m pytest tests/integration/test_completed_ranges.py tests/integration/test_retained_workflow.py
python -m pytest tests/integration/test_shutdown_handoff.py tests/unit/test_runner_batches.py
python -m pytest tests/integration/test_closing_review.py
python scripts/check_documentation.py --scope full
```

[pyproject.toml](../../pyproject.toml) owns Python dependency metadata;
[environment.yml](../../environment.yml) selects Python 3.12. Examples use the caller’s
interpreter. Record its executable, version, Conda identity and actual dependency import paths.
The SEED package version alone does not establish ExtensionNode support.

Use small real scenes and affected controls, material, Model and Tool regressions. Large
scenarios need not be calculated to verify CLI defaults or input paths. Linux/BPF tests that
need live infrastructure are separate qualification work. `tests/integration/test_completed_ranges.py`
checks retained cutoffs, a stateful Model, old published-prefix reading and slow/failed Tools.
`tests/integration/test_closing_review.py` sends real subprocess SIGINT during initial
calculation, continuation and presentation, including repeated interruption before
accepted work returns. It also exercises the generated Performance entry with an
unfinished generation suffix, and network export across an advancing publication
with a stale PhysicalWorld view.

For diagrams, keep Mermaid source in the relevant Markdown topic. The existing renderer uses
pinned Mermaid 10.9.1 plus Firefox/geckodriver:

```bash
python scripts/check_mermaid_render.py --scope full --mermaid-js /path/to/mermaid.min.js --mermaid-version 10.9.1
```

When an outbound proxy is configured, include `127.0.0.1,localhost` in `NO_PROXY`
(and `no_proxy` where used) for the local WebDriver connection. This is process
environment configuration, not a Docker or host-service change.

Link/anchor checks and syntax rendering catch structural errors; inspect the diagrams and
[portable demos](../demos/README.md) in a browser for readability and interaction. Browser checks
are implementation verification, not user visual acceptance. No minimum diagram count or
exhaustive API/import-graph mirror is required.

For a completed Tiny output, current view interactions can be checked with
`python scripts/check_inspection_visuals_browser.py examples/Sky00_tiny_network/output/visualizations`.
This exercises linked physical/Planner views, Performance filters and Traffic flow
selection, lazy packet details, seeking and playback using retained evidence.

The complete [architecture vector](../architecture/system-architecture.svg) is generated
from its single [Python source](../architecture/system_architecture.py). Regenerate with
`python docs/architecture/system_architecture.py`, then inspect the full-size SVG and
its embedded presentation for text overflow, line crossings and legibility.

Debug calculation using `results/experiment/generation-status.json`, the live control `status`,
and producer frontiers. Debug execution using the invocation's Runner events, Traffic records
and separate performance origin, plus Deployer records in `deployment/`. Preserve the primary
error and inspect real completed frontiers; do not infer execution success from a Plan.

For substantial work, capture an incoming baseline before edits and retain a phase-only diff.
Respect accumulated work and separate SEED ownership. [Engineering rules](../../AGENTS.md) summarize
scope, cleanup and reporting requirements.

For the ordinary authoring loop, use the [standalone development driver](../../templates/dataplane/threshold/develop.py): generate headers from a definition, implement the C handler, run selected Model/scene/C checks, and prepare normally. See [development helpers](../../tools/development/README.md). These checks neither deploy nor start observation/measurement.
