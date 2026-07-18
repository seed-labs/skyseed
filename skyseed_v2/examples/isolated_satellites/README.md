# Isolated Satellites

This handwritten example directly constructs the approved
`SkyseedAutonomousSystem -> Shell -> Orbit -> Satellite` hierarchy and compiles
two no-network Satellite services. It uses no YAML, file-configuration facade,
population helper or dynamic Simulator input.

From `skyseed_v2/`, generate the complete output first:

```bash
python3 -m examples.isolated_satellites.main --override
```

The generated output contains `docker-compose.yml`, build contexts and its own
controls. Start the experiment with:

```bash
examples/isolated_satellites/output/start.sh
```

The generated controls use the exact Compose project
`skyseed_v2_isolated_satellites`.

`SkyseedDockerCompiler` declares each locally built SEED base image as a
Compose `service:` additional build context. The generated start script uses
the maintained BuildKit/Compose v2 path. Cleanup stops only this exact project
and keeps the generated output:

```bash
examples/isolated_satellites/output/cleanup.sh
```
