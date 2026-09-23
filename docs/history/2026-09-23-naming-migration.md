# SkySEED package and repository-layout migration — 2026-09-23

This records migration implementation and its completed independent review. The user reported
independent acceptance of the preceding Feedback implementation, lifecycle repairs
and ordinary/satellite-failure Linux/BPF experiments. This increment does not repeat
those live campaigns or establish new live qualification. In the later scoped repair
request, the user reported migration and the main Feedback/Etcd documentation passed
review, subject to the two findings addressed [below](#scoped-regression-and-tiny-guide-repair).
The user subsequently accepted the final independent review of that repair and
explicitly authorized commit and fast-forward publication on 23 September 2026.
The implementation/repair stages below performed no staging, commit, push or merge;
publication is recorded separately in the closeout at the end of this report.

## Source, mapping and release scope

Incoming branch: `feature/skyseed-v2`; HEAD:
`e7a7c9bb53655fe85b5eb6c4ba2398a04ef0a7c1`. Feedback and repairs were already dirty.
The baseline captures 1,116 application files, Git state, installation metadata,
the separate SEED checkout and protected output hashes. Evidence root (`E` below):

```text
/home/lionheart/workspace/skyseed-local/implementation-baselines/naming-migration-20260922T220841Z
```

| Incoming path | Final authored path |
|---|---|
| `skyseed_v2/src/skyseed_v2/` | `src/skyseed/` |
| `skyseed_v2/{docs,examples,templates,tools,scripts,data,tests,research}/` | Corresponding repository-root directories |
| `skyseed_v2/{AGENTS.md,pyproject.toml,environment.yml,.vscode/}` | Repository root |
| Root and application README/ignore files | Merged root `README.md` and `.gitignore` |
| Existing `skyseed_v2/examples/*/output/` | Original paths, unchanged |

`path-mapping.json` maps every incoming authored file. `migration-renames.diff`
records explicit old/new paths and content hunks; `migration-text-only.diff` removes
relocation noise. `migration-files.json` records before/after hashes and categories.
These compare against the incoming dirty tree, not HEAD. `baseline/skyseed/diff`
separately retains the already accepted incoming implementation.
The initial migration audit recorded 806 pure relocations and 312 content-change records
(including merges/deletions and two new files). The rename-aware diff passes
`git apply --check` against the captured incoming tree without applying or staging it.

`release-file-scope.json` separates incoming application changes, migration changes,
and **27 independent incoming research changes**. The later proposed sequence is an
accepted Feedback/repair commit in the old layout, followed by the reviewed naming
migration, preserving existing development history and branch. Research relocation
and 46 navigation-link target edits in five research Markdown files are recorded
separately in `research-link-adjustments.json`; research prose and downloaded bytes
were preserved. Independent research additions/edits are not silently included in
the release proposal. Their inclusion needs separate scope review before staging.
SEED, local outputs, temporary harnesses and retained experiment evidence are excluded.

Read-only `ls-remote` checks again found formal
`git@github.com:seed-labs/skyseed.git` main at
`fe63af322f18206f236f4ffbfce0c1a1362a5406`, an ancestor of development HEAD. Both local
remotes still target `EMLionHeart/skyseed-dev-backup`; its main is
`06b19eb718a79a9113ee95bc045f10953c1d8ab7`. See `formal-main.txt`, `backup-main.txt`
and captured remote configuration. Recheck before any later publication; backup
main is not the formal release target.

## Implementation and installation

Production imports, lazy imports, subprocess modules, generated deploy/run/cleanup/
control/preparation commands, Tool paths, Examples, Templates, checks and current
documentation now use the root layout and `skyseed` package. The installer defaults
to `../seed-emulator` relative to the repository root. Package data still includes
the BPF C source and header. The independently mounted `skyseed_interaction` client
remains self-contained. No compatibility package, alias layer or console script was
added. Version `0.1.0`, environment `skyseed_dev` and the development branch remain.
The [installation guide](../user-guides/getting-started.md#migrating-an-existing-development-installation)
owns the import migration and old-output boundary.

Normal pip operations removed `skyseed-v2` and installed `skyseed` editable from the
repository root, with `--no-deps --no-build-isolation`. No dependency upgrade, Conda
update/pruning, manual site-packages edit or SEED reinstallation occurred. From
`/tmp`, with `PYTHONPATH` unset and Python isolated mode, the new import resolves to
`src/skyseed/__init__.py`; the old import/distribution is absent. All other installed
distribution versions/direct URLs and Conda metadata match the baseline. Python is
`/home/lionheart/miniconda3/envs/skyseed_dev/bin/python`, 3.12.13. Metadata is retained
in `baseline/installed.json`, `baseline/skyseed-v2-*`, `installed-after.json`,
`installation-after.json`, `skyseed-*`, and pip logs.

Existing material schemas/codecs, artifact identifiers, format versions, resource
namespaces and producer labels containing `v2` remain unchanged. Historical reports
retain their original paths. Model fingerprints already include fully qualified
Python type names, so this rename changes newly computed world fingerprints and
dependent lineage. Two fixed hash expectations were updated only after reconstructing
the old hashes by replacing the type-name prefix in the otherwise identical canonical
material (`fingerprint-comparison.json`, `single-shell-fingerprint-comparison.json`).
Fingerprint algorithms and serialized schema contracts were not changed.
`old-name-audit.json` classifies remaining occurrences by historical evidence,
research provenance, current migration guidance, or resource/artifact contracts.

## Validation

All commands use the existing environment and fresh E-scoped outputs. Common setup:

```bash
source /home/lionheart/miniconda3/etc/profile.d/conda.sh
conda activate skyseed_dev
E=/home/lionheart/workspace/skyseed-local/implementation-baselines/naming-migration-20260922T220841Z
cd /home/lionheart/workspace/skyseed
python -m pip uninstall -y skyseed-v2
python -m pip install --no-deps --no-build-isolation --editable .
python scripts/check_dev_environment.py
python scripts/check_import_dependencies.py
python -m pip check
```

Installation checking ran `python -I "$E/check_installation.py"` from `/tmp` after
`unset PYTHONPATH`. Build commands used `pip wheel --no-deps --no-build-isolation`
and the installed `setuptools.build_meta.build_sdist` backend. The optional `build`
frontend was absent; it was not installed. `check_package.py` verifies wheel/sdist
contents and compiles the default C program to BPF ELF using the unpacked wheel
from outside the checkout. See build logs, `package-check.json` and
`wheel-resource-runtime.log`.

```bash
python -m pip wheel --no-deps --no-build-isolation --wheel-dir "$E/dist-final" .
python -c "from setuptools.build_meta import build_sdist; print(build_sdist('$E/dist-final'))"
python "$E/check_package.py" dist-final
```

`python "$E/generate_cases.py"` uses Tiny's established builder and explicit
`GatewayHopCountRoutePlanningModel(1)` with the normal Controller for Linux and BPF:
30 seconds, ten-second cadence, three delivery windows, Traffic enabled, one route
worker and Internet Map disabled. Both modes generate sealed materials, renamed
scripts, preparation configuration and normal Tool views; shell syntax and public
control invocation are checked. `generation-check.json` records outputs and results.
This does not execute either kernel dataplane. `check_standalone_client.py` imports
the generated container client in an independent Python process which rejects any
framework import, reads closed owner state and exchanges values over the actual
Etcd Unix endpoint. Its service is stopped normally afterwards.

```bash
python -m pytest -q tests/unit tests/test_package_import.py tests/tools --basetemp="$E/unit-tools-tmp"
unset SKYSEED_BPF_HELPER_IMAGE
python -m pytest -q tests/integration --basetemp="$E/integration-tmp"
python -m pytest -q tests/unit/test_physical_model_identity.py tests/unit/test_documentation_scope.py \
  tests/unit/test_architecture_traceability.py tests/integration/test_development_environment.py \
  --basetemp="$E/migration-focused-tmp"
python -m pytest -q tests/integration/test_development_environment.py --basetemp="$E/installer-final-tmp"
python -m pytest -q tests/integration/test_starlink_gen1_satellite_internet_example.py::test_starlink_gen1_ground_station_allocation_preserves_canonical_identity \
  --basetemp="$E/single-shell-final-tmp"
python -m pytest -q tests/unit/test_experiment_control.py tests/integration/test_control_owner_outcomes.py \
  tests/integration/test_feedback_revision.py tests/integration/test_runtime_reset.py \
  tests/integration/test_shutdown_handoff.py tests/integration/test_initial_readiness.py \
  tests/integration/test_preparation_function.py tests/tools/test_development_tools.py \
  tests/integration/test_tooling_example.py --basetemp="$E/feedback-final-tmp"
```

| Log | Observed result |
|---|---|
| `unit-tools.log` | 1,419 passed, 15 failed, 14 skipped, 1 warning; 128.82 s |
| `integration.log` | 360 passed, 10 failed, 5 skipped, 14 warnings; 414.05 s |
| `incoming-causal.log` | Captured incoming source: the same 13 unrelated unit/Tool failures, 137 passed; 5.26 s |
| `incoming-integration.log` | Captured incoming source: the same 9 unrelated integration failures, 1 passed, 2 warnings; 42.52 s |
| `migration-focused.log` | 36 passed; 2.05 s (hash, documentation scope, architecture navigation and installation) |
| `installer-final.log` | 4 passed; 0.21 s, including actual shell execution with a recording Conda substitute to prove sibling-path resolution |
| `single-shell-final.log` | Renamed S3 fixture fingerprint plus existing allocation assertions: 1 passed; 8.59 s |
| `feedback-final.log` | 141 passed; 144.19 s: shared control, both final lifecycle repairs, selected revision/continuation lineage, reset, shutdown, preparation and Tool paths |
| `environment-check.log`, `import-check.log`, `pip-check.log` | PASS; 170 production modules, 507 internal edges; no broken package requirements |
| `documentation-current-final.log` | 122 current documents PASS, including research indexes |

Counts overlap. The initial unit/Tool run preceded the research navigation fix;
its documentation failure is resolved. Both rename-related world-hash failures
are resolved with all existing semantic/cache/allocation assertions retained.
The unrelated failures are not relabeled PASS or fixed by relaxing assertions.
Incoming-source comparison uses only the captured old tree and a subprocess-local
old-source path; it does not reinstall the old distribution. Exact integration
replay nodes/command are in `incoming-integration-command.json`.

At the migration-stage check, remaining incoming failures concerned stale Runner fake/surface contracts, old Traffic
qualification APIs, old Plan hashes, missing fake `accepts_batch`, stdout/stderr
expectations, Producer snapshot fields, old S4 Traffic options, incomplete fake
Compose services, and outdated compiler stage/encoding expectations. They are
separate release-preparation issues, not evidence of a new naming failure. Warnings
include the existing multithreaded fork deprecation and incomplete fake Runner
methods. Privileged/live and optional infrastructure checks remain skipped.
In total, **22 distinct incoming test failures remained** across those broader runs.
They are addressed by the [later scoped repair](#scoped-regression-and-tiny-guide-repair);
the original run results above remain historical facts.

The current documentation check still validates framework docs, Examples, research
indexes and links/anchors into archives. Only downloaded HTML source snapshots in
`research/**/references/` are excluded as link sources; their original CSS/JS is not
a local framework dependency. Authored research HTML is still checked, and the
workspace parent's unrelated README is no longer collected. Focused checks pass;
`documentation-full.log` separately reports preserved historical source links that
no longer resolve. No archived HTML or historical report was rewritten to obtain a pass.

## Preservation, cleanup and architecture delta

`verify_preservation.py` checks unchanged HEAD, branch, staged diff and remotes in
both checkouts; all 1,817 SEED files and its recorded symlink; every file in all seven
old Example outputs (53,877 files, including 5,553 in accepted Sky06); and both prior
Feedback evidence directories (2,003 and 1,927 files). All 20 incoming history files
retain their bytes. The migration audit detects unexpected paths/content and records
the separate research navigation edits. The residual local `skyseed_v2/` contains
preserved outputs and ignored local build/cache artifacts, not published source or
a compatibility installation.

`verify_cleanup.py` records owned test-service stop flags, recorded PID/start identity
and socket disappearance, E-scoped process absence and Docker inventories. Tests and
temporary generation use normal owner/service closure. No force-kill, new live
campaign, host-wide change, dependency change or SEED edit was performed.
The initial migration cleanup check passed for **320 owned service configs**, with no remaining
E-scoped process; Docker container/network/image identities match the baseline.
Compilation and whitespace checks pass (`compile-final.log`, `git-diff-check.log`).

**Architecture Delta:** the public Python import/distribution name and repository
layout change as authorized. Qualified type descriptions and generated module paths
change accordingly. Public method/type short names, behavior, owners, dependencies,
control semantics, material codecs, freezing, adoption authority and nonblocking
Runner progression remain unchanged. Documentation-check source scope is corrected.
There is no new runtime architecture, old-name shim or permanent Example. Migration
validation and prior live qualification remain distinct. At that stage, broad-suite
incoming failures and historical-link limitations remained review items before release.
The later scoped repair addresses the former; preserved historical-link limits remain.

## Normal Feedback/Etcd capability closeout

This authorized extension continued the migrated working tree. Its separate incoming
snapshot is `E/feedback-closeout/baseline/` (1,116 files); `closeout.diff` and
`closeout-files.json` isolate this increment from both the accepted incoming Feedback
work and the initial migration. The earlier migration diff/manifests are retained as
`before-*`; E's main migration manifests now cover the combined proposed tree.
The closeout changes 24 existing files: 18 documentation files, five production
modules (mostly generated help/docstrings), and one test file. Combined migration
accounting is now 795 pure relocations and 323 content-change records.
HEAD, branch, installation and publication boundaries remain as above.

Normal user entry points are the root [README](../../README.md), generated
`experiment_control.py`/README and deploy/run/cleanup scripts, the
[Feedback guide](../user-guides/computation-and-controls.md#interaction-setup-and-independent-programs),
the [Model reading example](../user-guides/model-extension.md#read-user-data-in-a-model),
and `InteractionClient.from_output` on the host or the generated standalone
`skyseed_interaction.InteractionClient.from_environment` in a configured container.

| Completed change | Usability/ownership reason |
|---|---|
| Existing navigation, setup, control and Model guides | Separate focused reads, custom publication, Model-owned reading, explicit controls, saved receipts, terminal lookup and subsequent observation; supply the missing Etcd binary installation step |
| API map and experiment/output local READMEs | Locate client, service, shared consumers, Controller/execution, catalog and Producer revision owners; distinguish public calls and local diagnostics |
| Output guide and generated root/container READMEs | Distinguish authored programs, captured/generated inputs, `.control` service data, Etcd records, file material versions, actual run evidence and derived views; state container Python/dependency/import/startup/socket prerequisites |
| Existing precise experiment reference | Clarify executable discovery and external-service/helper limits in the existing contract; guides link back to it |
| Execution sequence/lifecycle diagrams, overview, terminology, CURRENT and roadmap | Show the common request route, retained service, closed failed outcomes, preparation freeze, no implicit revision wait and dated validation limits |
| Generated CLI/service help and status summary | Explain receipt/result workflow and revision scheduling; show explicitly closed outcomes and final errors instead of labeling a closed owner unavailable |

Source review found the current ownership split appropriate; no code was relocated
for symmetry and no second documentation system or permanent Example was added.
The existing large system SVG and calculation/Model diagrams were checked against
their current role; the changed sequence/lifecycle diagrams carry the shared-control
and failed-closure correction. Model guidance also removes an outdated statement
that native routing software is only prospective. User writes never imply automatic
fault interpretation, invalidation or recalculation. The guides explicitly distinguish
selection from actual execution, failed cleanup from closure, and stale/indeterminate
results from rejection. Database restart promises records only.

**Scoped code repair:** `_status_summary` previously treated every unavailable owner
as an unavailable endpoint, including `live=false`, and printed runtime errors only
in the live branch. It now displays recorded closed outcomes (including failed or
missing outcomes) and escaping runtime errors while keeping stdout JSON unchanged.
Seven new real-Etcd CLI regressions cover both owners' completed/failed/missing
outcomes and a stale live observation. Request admission, result semantics, owners,
material authority and dependencies are unchanged. Other production edits are
generated documentation/help and docstrings. This is the closeout Architecture Delta.

All following commands used the same activated Python 3.12.13 `skyseed_dev`, repository
root cwd, and `C="$E/feedback-closeout"`. No package/dependency installation occurred
in this extension. Logs and temporary harnesses are under C:

```bash
python -m pytest -q tests/unit/test_experiment_control.py \
  tests/integration/test_control_owner_outcomes.py tests/integration/test_feedback_revision.py \
  tests/integration/test_runtime_reset.py tests/integration/test_shutdown_handoff.py \
  tests/integration/test_retained_workflow.py --basetemp="$C/regression-tmp"
python "$C/check_snippets.py"
python scripts/check_documentation.py --scope focused
python scripts/check_dev_environment.py
python scripts/check_import_dependencies.py
python -m pip wheel --no-deps --no-build-isolation --wheel-dir "$C/dist" .
python -c "from setuptools.build_meta import build_sdist; print(build_sdist('$C/dist'))"
python "$C/check_package.py"
python "$E/verify_preservation.py"
python "$E/verify_cleanup.py"
```

**PASS:** `regression.log`: 112 passed in 114.23 seconds, including final lifecycle
repairs, outage/result handling, revision/rebase/continuation and cleanup. The snippet
harness extracts and executes the actual Python/Bash fences from guides/generated
READMEs. It uses Tiny's established scene (two satellites, one GS, two Zones), explicit
Linux GatewayHopCount routing, one route worker, ten-second cadence, initial three
ten-second deliveries, Traffic/Map disabled. The documented Model reads the author's
delay: initially 100 µs, then 3,000 µs on explicit revision. A custom write alone
changes neither Model calls nor materials. Revision request
`cda60e693db54b2ba58110ad5a983264` selected version
`13016f9cd8cb4a0fa4f761c9279778e1` from initial index zero; continuation request
`79551de6864a4251b348e06e8a613dd8` extended to 330 seconds with the generated CLI's
300-second next-delivery default. All four selected references have compatible lineage.
`revision-and-continuation.json` retains full receipts, results, version references
and Model observations; `snippet-checks.json` records eleven checks and their limits.

Independent host processes execute reads/writes, focused queries, pause receipt/result
lookup and history. The generated standalone client executes both documented container
snippets over the real Etcd Unix listener, with framework imports forbidden. A host
module search path stands in for the Compose mount; **this is not a container run**.
Runtime commands use the normal execution owner/consumer with an explicit offline
Runner resource stand-in; no Docker/kernel/Traffic work is performed. The real retained
Controller/Producer handles revision and continuation before that fixture starts.
The documented stop/restart/status/stop commands preserve closed outcomes. Existing
regressions additionally cover external attachment, lost acknowledgements, late
preparation races and failed cleanup. The initial harness constructor typo (positional
instead of keyword gateway count) is retained in its separate failure log; it was
corrected before generation and did not require a product repair.
The external-endpoint constructor fence separately passes inert assembly checking
(`external-constructor.log`); its placeholder endpoints are not contacted.

Current links/fences, environment/import boundaries and rebuilt wheel/sdist resources
pass. `package-check.json` records current artifact hashes; the unpacked wheel compiles
the packaged default C program to BPF ELF outside the checkout. Five relevant Mermaid
diagrams render under pinned 10.9.1/Firefox 154.0.1; the two changed diagrams were
visually inspected. `diagrams/render.json`, SVG/PNG files and the complete execution
sequence screenshot retain that evidence. This is implementation verification, not
user visual acceptance. All 37 Python/Bash fences in changed documents pass syntax
checks; changed Python sources parse and both incremental/combined diffs pass
`git apply --check` against their captured inputs. Preservation/cleanup logs confirm the
protected outputs, incoming history/research, SEED and Git boundaries and absence of
test-owned processes/sockets. All **430 owned service configurations** across E are
stopped, with recorded PID identities and sockets absent; Docker identities are
unchanged. All 57,807 protected output/evidence files retain their hashes. Installed
dependencies and Conda metadata remain unchanged, checked again from `/tmp` with
isolated Python and no PYTHONPATH. Detailed identities live in the manifests.

**Remaining/unproven at this closeout stage:** the 22 incoming broad-suite failures
and preserved historical link limitations above remained. The later scoped repair
below addresses the failures; it does not change those historical validation results. No new live Linux/BPF or container qualification,
secured/remote Etcd deployment, Python 3.10 container image qualification, process
crash recovery or user visual acceptance is claimed. External credentials/client
certificates and crashed-member takeover have no public helper support and are
documented limits, not silently promised capabilities. No management UI, fault
strategy, mandatory Model hook, publication or independent acceptance was added.


## Scoped regression and Tiny-guide repair

The later user request accepted the migration/main Feedback documentation review but
required repair of all 22 incoming regression failures and Tiny's obsolete Traffic
acceptance instructions. This increment starts from that dirty migrated tree, not
HEAD. `C="$E/regression-repair"` contains a new 1,116-file byte/mode/hash baseline,
Git identities, copies of the preceding audit manifests, and `repair.diff` plus
`repair-files.json`. Branch, HEAD, environment and dependency identities are the same
as above. The normal entry remains `examples/Sky00_tiny_network/tiny_network.py`,
followed by generated `experiment_control.py`, `deploy.sh`, `run.sh`, `cleanup.sh`
and the explicit interaction-service stop command. No migration was restarted.

### All 22 failures accounted for

`reported-nodes.json` records the exact nodes from the two incoming logs.
`failure-accounting.json` maps each one to its repair and affected-test result.

| Nodes | Repair class and restored guarantee |
|---:|---|
| 1 | Closed Runner API assertion includes the approved preparation lead, material selection and Traffic binding/endpoint methods; invalid constructors still reject. |
| 7 | Finite Runner fixture now supplies scoped Zone/address metadata matching its real BPF translation. B0 one-shot behavior, formal pacing, no runtime decode/discovery, failure frontier/cleanup ownership, prepared telemetry and inspection-lock checks execute. |
| 2 | Explicit independently specified canonical JSON replaces opaque older-schema Plan/state/transition goldens. Empty `network_scope` is canonical content; resource → parameter → route precedences and Plan lineage/cadence are included. Oracle replay, codec round trips, scope sensitivity, immutability and invalid lineage/world checks remain. Persisted `skyseed-v2-*` schema strings remain wire identities. |
| 2 | Retired Traffic qualification import and PASS score replaced with a real fixed Tiny workload, current sender parser/reader/projection and per-communication evidence at each actual offline Runner boundary. Existing generation/incarnation, ingress identity, boundary timing and observer measurements remain. Missing ICMP association creates no invented hops; absent summaries keep totals unknown. |
| 3 | Performance/bootstrap finite Runner fakes report `accepts_batch=False`. They no longer fail in delivery setup; performance ownership, original errors/secondary notes and prepare → run → Traffic stop → Runner cleanup order execute. All four checkpoint-failure combinations run, including the two previously passing nodes. |
| 1 | Generated script consumes the fixed workload plus invocation evidence, not a retired Planner-derived probe plan. Its neighboring shell test executes the generated controls with recording executables, verifies exact Traffic arguments and unchanged sealed material hashes. |
| 1 | Tiny CLI lifecycle text asserted on stderr, retaining actual output-file and MATERIALS_READY checks. |
| 1 | Producer's ordinary evidence scope asserted before/after production; lazy bounded counts and terminal idempotence retained. |
| 1 | Native S4 uses current fixed Traffic cadence, two pairs/both directions/four unique IDs, retaining assembly/backend/projection invariants. |
| 1 | Compose fixture contains SEED's base-image prerequisite service. Project image ownership/build metadata, content deduplication and per-node metadata checks execute. |
| 2 | Compiler's shared backend rollup name and prepared-forwarding v8/backend identity replace retired BPF-specific expectations. Exact stage order, result equality, plan/index scope, terminal/lineage/count/no-rich-reconstruction remain; decoded timestamps and mmap are checked too. |

`canonical-content-diagnosis.json` also identifies the exact old digest inputs:
transition v5, state v3 and Plan v5, already including network scopes. Substituting
those older schema identities reproduces all three old goldens. The current
[closed target-union contract](../development/planner-state-and-plans.md#routes)
uses transition/Plan v6 and state v4; even this unchanged
reachable-route sample therefore has a new identity. The tests assert full current
canonical content independently, rather than deriving expected values from the
production codec or copying its hash. Existing scoped fields and precedence edges
are retained content, not newly introduced by this repair.

**Architecture Delta: none.** This repair changes tests and documentation only; no
production implementation, public methods/types, records, ownership, dependencies,
execution paths or Example behavior changes. No skips, xfails or exception catches
were added. Corrected fixtures exposed no directly related production defect.
Controlled ping text and the kernel/helper fixtures in these tests are offline
fixtures; they are not new live packet evidence or a revived qualification system.

### Tiny instructions and executable evidence

[Getting started](../user-guides/getting-started.md#installation-smoke-test-tiny)
now explains the actual two default communications and their IDs; active `senders`
versus cumulative launches; `records` versus sent packets; Traffic worker completion
versus requested duration, error and connectivity; and separate runtime, calculation,
deployment and Etcd drainage. The copyable inspection pins the invocation, reads its
actual sender file and prints reply observations and terminal summaries for every
configured ID. It preserves null totals and explains duplicate replies, missing
replies, unknown intermediate hops and stale views. The Traffic generation README
links to this workflow. A scan of current guides, Tool/Example READMEs and generated
help sources found no other current episode/qualification acceptance prescription;
historical and downloaded research text was preserved.

`check_tiny_inspection.py` extracts the actual Bash fence and executes it read-only
on all three retained ordinary Feedback outputs: `linux`, `linux-all-zones`, `bpf`
under `/home/lionheart/workspace/skyseed-local/experiments/feedback-live-20260923/`.
It verifies unchanged hashes for the workload, invocation pointer, sender evidence
and runtime outcome. `tiny-inspection-checks.json` records full paths, invocation IDs,
counts and hashes; `tiny-inspection-*.stdout` retains exactly what a user sees. Zero
replies/unavailable sources remain visible. The recorded ping terminal totals are
null, so they are not reclassified as known sends or losses. This is read-only replay
of dated evidence, not a new Tiny run or full-duration connectivity acceptance.
The current Tiny builder separately confirms one pair and two directed identities.
The retained two-communication Linux run reports 1/0 launches and 0/0 replies. The
two four-communication runs each report 1/0/1/1 launches and 0/0/133/133 replies,
with unknown terminal sent/received/loss totals. These observations demonstrate why
a completed runtime and a deterministic selected pair cannot promise connectivity.

### Commands and results

Commands below use the unchanged activated `skyseed_dev` Python 3.12.13 from repository
root. Exact expanded commands, original outputs and per-scope exit codes/times are
retained under C. `reported-before-command.txt` lists all 22 node selections;
`affected-{1,2}-command.txt` lists all affected files and neighboring parametrizations.

```bash
source /home/lionheart/miniconda3/etc/profile.d/conda.sh
conda activate skyseed_dev
unset SKYSEED_BPF_HELPER_IMAGE
# Exact focused invocations are retained in the command files above.
python "$C/run_broad_checks.py"
# That harness runs these two scopes, once each, with separate C/*-tmp directories:
python -m pytest -q -ra tests/unit tests/test_package_import.py tests/tools --basetemp="$C/unit-tools-tmp"
python -m pytest -q -ra tests/integration --basetemp="$C/integration-tmp"
PYTHONPATH="$PWD" python "$C/check_tiny_inspection.py"
python scripts/check_documentation.py --scope focused
python scripts/check_dev_environment.py
python scripts/check_import_dependencies.py
python "$C/diagnose_canonical_content.py"
python "$C/audit_repair.py"
python "$E/audit_migration.py"
python "$E/verify_preservation.py"
python "$E/verify_cleanup.py"
python "$C/finish_evidence.py"
# Installation comparison runs separately from /tmp, with PYTHONPATH unset:
(cd /tmp && env -u PYTHONPATH python -I "$E/check_installation.py")
```

The broad commands in that block document the harness's expansion, not duplicate
executions. Audit commands likewise describe the steps in `finish_evidence.py`.
The repair-specific baseline reproduction yielded **22 failed** in
42.88 seconds. `affected-1.log` retained **198 passed, 1 failed** in 104.44 seconds:
the new explicit canonical fixture initially omitted the required precedence edges.
After specifying them, `affected-2.log` records **221 passed**, three existing
multiprocessing/fork warnings, in 100.46 seconds. This includes every reported node,
all affected-file neighbors and fixed Traffic generation, observation and association
checks. The first snippet-harness attempt lacked the repository module search path;
the second incorrectly expected known positive terminal loss where all retained
summaries are null. Both original logs and the initial harness are retained. The
corrected harness uses the documented environment and asserts unknown totals remain
unknown; the unmodified guide command succeeded on all three outputs.

**PASS:** the requested broad scopes each ran once after the affected tests passed:
`unit-tools.log`: **1,441 passed, 14 skipped**, 15 warnings, 139.82 seconds;
`integration.log`: **371 passed, 5 skipped**, 10 warnings, 419.04 seconds. Both exit
codes are zero in `broad-results.json`. The 14 unit/Tools skips require unavailable
Node.js; the five integration skips require an explicitly opted-in privileged helper
or kernel invocation. No dependency was installed to change those limits. Retained
warnings are the existing multithreaded `fork()` deprecation warnings, not suppressed
failures. Every one of the 22 reported failures passes; none remains unresolved.

Current documentation checks pass for 122 documents. Environment, import boundaries,
changed-file/fence syntax and whitespace checks pass. The exact guide snippet ran on
real retained evidence; its verification is distinct from link/format checking.
`obsolete-traffic-instruction-scan.json` inventories the 70 current documentation/help
source files scanned. There are no production or diagram changes requiring another
live campaign, package rebuild or diagram render. The broad integration run includes
the existing Feedback lifecycle/revision/control regressions.

**Preservation and cleanup PASS:** 14 authored files changed in this repair: nine
test files and five documentation files. `repair-files.json` records before/after
hashes; `repair.diff` applies cleanly to C's captured dirty baseline. The updated
combined migration diff also applies cleanly to its original baseline. Final test
ASTs match the source captured at the passing affected run; subsequent test edits
only corrected the explanatory schema-version comment. `final-checks.json` and
`review-artifact-hashes.json` identify these checks and the review artifacts.

All 57,807 protected output/evidence files retain their hashes, including accepted
Sky06 output, ordinary Feedback and satellite-failure evidence. The separate SEED
checkout (1,817 files and one symlink), incoming historical documents, independent
research, installed distributions and Conda metadata are unchanged. Existing
research navigation-only migration edits remain separately recorded; no new research
edits occurred. Git branch/HEAD/index/remotes remain unchanged.

The final cleanup audit checks **642 owned service configurations** across E,
including **212 under this repair**: all marked stopped, recorded PID identities
absent, client sockets absent, no remaining evidence-path processes. Docker
container/network/image identities match the original baseline. The first cleanup
scan ran alongside preservation and correctly listed that still-running audit and
its shell, not an Etcd leak; `cleanup-concurrent-check.json` and `cleanup.log` preserve
that result. After the audit drained, the unchanged checker passed in isolation
(`cleanup-final.log`). No service filter was relaxed or process force-killed.

**Limits:** infrastructure skips retain their original boundaries; no new live
Docker/Linux/BPF campaign, container image validation, visual acceptance, remote
Etcd security validation, performance campaign or independent repair acceptance is
claimed. Earlier historical-link limitations and capability limits above remain.
No staging, commit, push, merge, branch/package rename or publication occurred.


## Accepted release closeout — 2026-09-23

The user accepted the final independent review of the cumulative Feedback/Etcd,
future-material revision, fixed Traffic, naming migration, documentation and
22-regression/Tiny repair. The recorded regression counts overlap and retain their
19 infrastructure skips. Earlier failures and historical/live evidence limits above
remain historical facts; this publication task adds no live or visual acceptance.

The release appends one commit on `feature/skyseed-v2` after the existing `ca861ab`
and `e7a7c9b` ancestry. Publication targets are explicitly
`git@github.com:EMLionHeart/skyseed-dev-backup.git` `refs/heads/feature/skyseed-v2`
and `git@github.com:seed-labs/skyseed.git` `refs/heads/main`; backup `main` is excluded.
The release candidate retains previously committed research under its migrated paths,
while excluding all 27 independent incoming research additions/edits, including the
mixed local research README. That independent content remains byte-for-byte local.
Navigation and documentation checks use an exported candidate tree so excluded local
files cannot hide broken published links.

Architecture Delta for this closeout: none. Only current acceptance/status and necessary
release navigation change; production code, tests, dependencies and version remain the
reviewed source. The compact publication receipt, candidate manifest, source comparison,
checks and final remote verification are retained outside the tracked application tree
under the evidence root's `publication-20260923T001905Z/` directory. Its receipt records
actual commit/tree identities and publication outcomes without a self-referential commit.
