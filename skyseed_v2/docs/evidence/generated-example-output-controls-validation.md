# Generated Example Output Controls Validation

## Scope

This record validates the approved two-stage human execution path:

```text
Python example entrypoint
→ complete generated output
→ output/start.sh
→ Docker Compose project
```

The change does not implement Simulator or freeze future Emulator/Simulator
generator filenames. It keeps final control generation after all currently
implemented output producers finish.

## Implemented contract

- All three formal Python entrypoints generate executable `output/start.sh`
  and `output/cleanup.sh` after compilation.
- Source-level `start.sh` and `cleanup.sh` wrappers were removed.
- `start.sh` uses the exact generated Compose file and fixed example project
  name, runs `config --quiet`, then `up -d --build`.
- `cleanup.sh` uses the same project/file and runs
  `down --remove-orphans`; it retains the output.
- A shared helper exists only in the private `examples` module. No product
  public class, function, export or Compiler API was added.
- `ComposeProjectLauncher` remains the programmatic state-verifying
  materialization adapter; generated scripts are the minimal human entry.

## Automated validation

```text
compileall: PASS
focused architecture/example/compiler tests: 40 passed in 12.27s
full test suite: 143 passed in 13.25s
git diff --check: PASS
```

Tests verify generated executable modes, Bash syntax, exact project/file
scope, command order, missing Compose failure, invalid project-name failure,
and the absence of prune, image deletion or output deletion.

## Real generation validation

The isolated, complete and large Python entrypoints each rendered and compiled
to separate temporary directories. Every output contained:

```text
docker-compose.yml
start.sh (executable)
cleanup.sh (executable)
```

All six scripts passed `bash -n`, and all three Compose files passed
`docker compose --parallel 16 ... config --quiet`.

The generated complete-example controls were executed with a recording Docker
substitute. The observed command sequence was exactly:

```text
docker compose --parallel 16 -p skyseed_v2_complete_static_emulator \
  -f <output>/docker-compose.yml config --quiet
docker compose --parallel 16 -p skyseed_v2_complete_static_emulator \
  -f <output>/docker-compose.yml up -d --build
docker compose --parallel 16 -p skyseed_v2_complete_static_emulator \
  -f <output>/docker-compose.yml down --remove-orphans
```

No real image build, Compose up or container start was performed. Temporary
validation outputs were removed.

## Architecture Delta

- Public types/functions/methods: none.
- Entity attributes, ownership and parent/child relations: none.
- Root exports and product package dependencies: none.
- Top-level product directories and cross-layer data contracts: none.
- Formal human execution path: changed from source `start.sh → main.py` to
  `main.py → output/start.sh → Docker Compose`.
- Example call chains: all three now finalize output controls after compile.
- Class diagrams: unchanged; no class was introduced.
- Flow/materialization diagrams: updated with generated controls and the
  separate programmatic launcher path.
