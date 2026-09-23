# Engineering entry

Read [README](README.md) and the relevant [developer references](docs/development/README.md).
[CURRENT](docs/CURRENT.md) records capability and validation limits;
[development](docs/development/workflow.md) gives environment and focused checks. Read history
only when the task needs a past decision or measurement.

- Use the existing `skyseed_dev` environment for ordinary work. Report the actual Python
  executable, version and Conda identity. Create isolated environments or change dependencies
  only when the task authorizes it; the setup script supports an explicit prefix.
- Preserve incoming tracked and untracked work. Capture an incoming baseline for substantial
  changes; distinguish the task's diff from accumulated changes. Do not stage, commit,
  push or clean Git unless the user requests it. SEED is a separate checkout.
- Follow the task's approved scope. Routine private implementation choices need no new
  approval. Explain substantive ownership or public-interface changes and their effects; resolve
  conflicts with task scope before implementation. Keep Model author freedom and explicit component ownership.
- Use small real offline scenes and affected regression tests. Live Docker/BPF experiments
  need task authorization. Test success does not establish independent review, visual
  acceptance, live qualification or release readiness.
- Wait for owned work and cleanup to finish. Slowness alone is not failure; no arbitrary
  termination timers or automatic forced kills. Preserve the primary error through cleanup.
- Maintain each precise contract in one developer reference topic. Architecture explains mechanisms,
  guides show operations, local READMEs explain useful interfaces, history preserves original
  conditions, and plans state their status. Update affected examples and diagrams; do not
  duplicate exhaustive API inventories or mirror the complete import graph in prose.
- Report the Architecture Delta: changed public methods/types, records, ownership,
  dependencies, execution paths and example behavior, or explicitly state none.
