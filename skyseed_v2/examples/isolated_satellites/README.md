# Isolated Satellites

This handwritten example directly constructs the approved
`SkyseedAutonomousSystem -> Shell -> Orbit -> Satellite` hierarchy and compiles
two no-network Satellite services. It uses no YAML, file-configuration facade,
population helper or dynamic Simulator input.

From `skyseed_v2/examples/isolated_satellites/`, generate the complete output
directly:

```bash
python3 main.py
```

Direct-file execution uses the caller's selected Python and never switches
environments. The recommended local interpreter is the canonical
`skyseed_dev` Conda environment described in the top-level README. The
equivalent module command from `skyseed_v2/` remains available:

```bash
python3 -m examples.isolated_satellites.main
```

The canonical command enables the standard upstream InternetMap image. Pass
`--no-internet-map` only to isolate a suspected Map failure during diagnosis.
The example never stages a local Map source tree or infers the SEED checkout
layout.

Existing default output is replaced automatically. Pass `--no-override` only
when an existing output directory should make generation fail.

The generated `output/runtime/` contains `docker-compose.yml` and build
contexts. The scenario-owned controls are at the output root:

```text
output/
├── start.sh
├── cleanup.sh
└── runtime/
    ├── docker-compose.yml
    └── compiler-generated build contexts
```

Start the experiment with:

```bash
./output/start.sh
```

The generated controls use the exact Compose project
`skyseed_v2_isolated_satellites`.

The generated start script uses plugin-based Compose v2. Cleanup stops only
this exact project and keeps the generated output:

```bash
./output/cleanup.sh
```
