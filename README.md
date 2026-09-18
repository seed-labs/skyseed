# SkySEED

SkySEED builds satellite-network experiments on [SEED](https://seedsecuritylabs.org/emulator/).
An Emulator constructs the network scene; replaceable Models compute physical
conditions, connections, Zone service and routes. Computation publishes Plans and
sealed execution materials. A separate Deployer and Runner deploy the SEED containers
and apply network changes through Linux/BPF. Optional interactive views explain the
physical world, planned network and observed execution without becoming their owners.

The active application is [skyseed_v2](skyseed_v2/README.md). The official repository is
[seed-labs/skyseed](https://github.com/seed-labs/skyseed), branch `main`; access is required.

## Start here

| I want to… | Start with |
|---|---|
| Understand the system | [Architecture and system diagram](skyseed_v2/docs/architecture/overview.md) |
| Explore without running an experiment | [Download/local interactive demos](skyseed_v2/docs/demos/README.md) |
| Run the default assembly example | [S4](skyseed_v2/examples/Sky02_s4_default/README.md) |
| Try a smaller scene and customize Models | [S5 and its local algorithms](skyseed_v2/examples/Sky01_s5_custom_models/README.md) |
| Develop or review changes | [Developer references and workflow](skyseed_v2/docs/development/README.md) |

## Install, calculate and run

Follow [getting started](skyseed_v2/docs/user-guides/getting-started.md) to obtain the
required SEED fork and install Conda/Python dependencies. A browser can open the
retained demos locally; they are not an already hosted website. Actual network
execution additionally needs the [Linux/Docker/BPF host](skyseed_v2/docs/user-guides/linux-host.md).

```bash
# Terminal 1, from this repository; SEED checkout prepared by the installation guide.
cd skyseed_v2
bash scripts/setup_dev_environment.sh
conda activate skyseed_dev
python scripts/check_dev_environment.py
python examples/Sky02_s4_default/s4.py
```

The example constructs the scene, computes 0–600 seconds, publishes results and
optional views, then keeps the computation process waiting for continuation. Open
`skyseed_v2/examples/Sky02_s4_default/output/index.html` after publication. This does
not automatically deploy or run a network. Add `--operation generate` for finite
generation and exit. [Tiny](skyseed_v2/examples/Sky00_tiny_network/README.md) is the
installation smoke test.

```bash
# Terminal 2, from this repository, after results are published and host setup is complete.
conda activate skyseed_dev
cd skyseed_v2/examples/Sky02_s4_default/output
./deploy.sh
./run.sh
```

```bash
# Terminal 3, from this repository; run.sh retains the last ACTIVE network at range end.
conda activate skyseed_dev
cd skyseed_v2/examples/Sky02_s4_default/output
python experiment_control.py status
# After the intended ACTIVE coverage has completed:
python experiment_control.py shutdown
# Wait for Terminal 1's computation and Terminal 2's run.sh to return, then:
./cleanup.sh
```

Cancellation acceptance is distinct from completed drainage. The
[control guide](skyseed_v2/docs/user-guides/computation-and-controls.md) explains
continuation, pause/resume, reset and separate cancellation. Generated controls and
`output/index.html` are the normal entry points.

## Choose a starting scale

| Example | Satellites / GSs / Zones | Initial range | Reference first material / deployment |
|---|---:|---:|---:|
| S5: smaller customization example | 172 / 10 / 184 | 600 s | 16.4 s / 21.0 s |
| S4: default assembly example | 348 / 355 / 732 | 600 s | 62.2 s / 70.9 s |

These measurements used a shared 32-logical-CPU, approximately 60.5 GiB Ubuntu host;
they are not minimum requirements. S5 is the smaller starting point, with no verified
8 GiB support guarantee. [Scale and resources](skyseed_v2/docs/user-guides/scale-and-resources.md)
gives S3/five-shell comparisons, concurrency, memory caveats and Traffic WARN outcomes.

[Documentation](skyseed_v2/docs/README.md) · [Current capability and validation](skyseed_v2/docs/CURRENT.md).
The retired v1 implementation remains in Git history at `375501cfba5ee385486105f382324729914dc3b1`.
