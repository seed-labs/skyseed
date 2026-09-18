# Current capability and validation

The project name is **SkySEED**. The completed technical review has been accepted;
the official source is the private [seed-labs/skyseed](https://github.com/seed-labs/skyseed)
repository, branch `main`. [Getting started](user-guides/getting-started.md) explains
authenticated retrieval. Existing directories, imports, APIs, environment names and
distribution version `0.1.0` are retained; this publication introduces no release tag
or product-title suffix.

The publication's documentation supplement adds a [public API reference](api/README.md),
per-role data navigation in the [Model guide](user-guides/model-extension.md), and short
query hints in the copyable templates. It documents existing calls and input values;
it adds no interfaces, algorithms or execution behavior. Its checks cover links,
the new usage snippets and unchanged executable Python, without new live qualification.

Phase 4b-2 retains computation by default in all six examples; finite `generate()`
remains available. PhysicalWorld, Planner and Compiler cutoffs publish completed
requests and retain the original cursor for continuation. Tools read one completed
publication while calculation remains alive. Deployment and execution have separate
owners and explicit controls.

The closing pass fixes repeated-interruption drainage, coherent Performance reads
from generated `run.sh`, and concurrent network export with stale presentation.
Coordination injection is removed: PlanTransition derives semantic event dependencies.
The seven primary Model roles remain replaceable; ZoneMembership is an optional
lower-level capability. Plan value formats and normal execution timestamps are unchanged.

- [Controls and requests](user-guides/computation-and-controls.md),
  [outputs and Tools](user-guides/outputs-and-tools.md), [historical demos](demos/index.html).
- [Complete architecture](architecture/overview.md), [developer references](development/README.md),
  [Model roles](development/model-interfaces.md), [prospective work](plans/roadmap.md).
- [Development checks](development/workflow.md), [closing campaign](history/2026-09-18-closing-campaign.md).

All six maintained examples were run sequentially through real Docker/BPF deployment
and execution with their full requested scenes. Tiny covered 30 seconds; S5, S4,
S3 OSPF and Five Shells covered 600; continuation covered 900, including genuine
material exhaustion and late supply. S4 also paused/resumed and reset through 30
seconds on the same deployment. The five-shell campaign found a stale Traffic
frontier during slow Docker launch; the correction passed focused regressions and
was followed by a complete execution replay using unchanged sealed materials.

Traffic results and remaining unexplained/unclassified losses are recorded per case
in the dated campaign. Complete runtime coverage does not mean universal reachability
or packet delivery. Runner/Traffic drained and Deployer removed owned resources for
all attempts. Normal Example outputs retain the calculated records, invocations and
browser views. Earlier-case applicability after the late correction is documented
through source hashes and actual source/ACTIVE correspondence.

That six-example campaign used the existing `skyseed_dev` Python 3.12.13 at
`/home/lionheart/miniconda3/envs/skyseed_dev/bin/python`, with focused regressions,
real subprocess SIGINT, import boundaries, documentation links and actual diagram/browser
inspection. It did not change dependencies, environments, SEED or host configuration.

The closing incoming baseline, phase-only patch and compact evidence are in
`skyseed-local/experiments/phase4b-2-closing-20260917T184610Z/` beside the repository.
Earlier phase evidence remains separate; the accumulated HEAD diff is not this task's diff.

The subsequent 18 September closing-fixes and release-preparation pass refreshes
Traffic's published ACTIVE decision after blocking source-migration drainage. A later
source, an unserved interval, episode completion, a logical hold or cancellation can
supersede the earlier replacement. Reconciliation and migration accounting follow that
decision; returning to the original source is not counted as a source migration. This
slow-stop variant was not observed by the six-example campaign and does not invalidate
its recorded coverage. The complete Traffic plan regression file passes 52 tests.

Developer contracts now live under [development](development/README.md); the superseded
design directory and standalone development guide are removed. The practical entry is
[getting started](user-guides/getting-started.md), with separate [Linux host](user-guides/linux-host.md)
and [scale/resource](user-guides/scale-and-resources.md) references. The accepted overall
architecture diagram and editable source were preserved. The later
[calculation diagram](architecture/calculation.md) explains all seven Model hooks and
framework-owned progression; publication only normalizes the diagrams' displayed name.

SEED upstream development remained at `97ead673aaaa4cd65f28cebc6719390913e8fdb7`.
The existing ExtensionNode candidate `05ad40bd22374496691d4b4fd516b4ee9b36eb4b` was
validated and published to `EMLionHeart/seed-emulator:development`; an anonymous HTTPS
checkout retrieved that commit. The relevant ExtensionNode/FRR subset passed. One
unrelated OSPF assertion also fails against unchanged upstream and is recorded in the
task evidence rather than repaired as part of this integration.

Before publication, a fresh Conda prefix with Python 3.12.14 installed that public SEED
checkout and a clean source tree containing the complete then-current SkySEED candidate,
including incoming uncommitted work. Tiny used its ordinary output inside that tree, retained computation,
published all four frames and views, deployed and reached all four ACTIVE boundaries.
The final run observed replies from all ten probe episodes: Traffic PASS, 146/146
replies, zero infrastructure failures. Supported cancellation drained both retained
processes; Runner confirmed BPF cleanup and pin removal, and Deployer left no owned
containers or networks. Browser interaction checks passed on those final views.
Earlier verification-driver failures and one premature-cancellation Traffic FAIL are
preserved separately; they are not passing qualification evidence.

That pass's baseline, task-only diff, command logs, clean environment and Tiny evidence
are in `skyseed-local/experiments/closing-release-20260918T070707Z/` beside the repository.
The existing environment, main-workspace example outputs and host configuration were
preserved. This was candidate-installation evidence before the official commit, not an
anonymous checkout or installation from the private official repository.

The final calculation-diagram and SIGINT-test pass consolidated the algorithm overview
and moved test synchronization onto a dedicated channel, preserving real repeated SIGINT,
accepted-work ownership and drainage assertions. Subsequent independent review passed
132 tests with one existing Node-unavailable skip; documentation and dependency checks
also passed. The user accepted the closing review and authorized publication. Finalization
changes presentation and documentation only; it does not add live experiments or broaden
the campaign's qualification. Visual acceptance remains with the user.
