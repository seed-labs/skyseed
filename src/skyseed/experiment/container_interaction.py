"""Provision the same etcd client in isolated generated network namespaces.

The etcd listener is mounted, not an owner RPC or fallback transport. This does
not add a route, host interface, container network, or an interaction proxy.
"""
import json
from pathlib import Path
import shutil
from yaml import safe_load, safe_dump


def _provision_container_interaction(output):
    output = Path(output)
    config = json.loads((output / '.control/interaction.json').read_text())
    endpoint = config.get('container_endpoint')
    if not endpoint:
        return  # External etcd requires an explicitly routable endpoint.
    client = output / 'inputs/interaction-client'
    client.mkdir(exist_ok=True)
    shutil.copyfile(Path(__file__).with_name('interaction.py'), client / 'skyseed_interaction.py')
    (client / 'requirements.txt').write_text('etcd3gw>=2.7,<3\nrequests-unixsocket>=0.4,<0.5\n')
    (client / 'README.md').write_text('''# Container interaction client

Compose mounts this directory read-only at `/opt/skyseed-interaction` and supplies
`SKYSEED_INTERACTION_ENDPOINT` and `SKYSEED_EXPERIMENT_ID`. This is the same public
Etcd client used by host programs; it needs no installed SkySEED package.

Provide Python 3.10+ and install the dependencies in `requirements.txt` in your
chosen image. For a build, copy that file into the build context or install its
listed versions directly: the bind mount exists only at container runtime.
Provide your own program and startup command. Client provisioning supplies neither.

Run inside that configured experiment container:

```python
import sys
sys.path.insert(0, "/opt/skyseed-interaction")
from skyseed_interaction import InteractionClient

client = InteractionClient.from_environment()
try:
    print(client.state("runtime"))
    print(client.user_put("node/report", {"observed": True}))
finally:
    client.close()
```

Host programs use `from skyseed.experiment import InteractionClient` and
`InteractionClient.from_output(output_path)`, then `user_get("node/report")`.
User values are opaque JSON: writing one does not interpret a fault, run a Model
or revise materials. Models choose when/how to read and use them.

With a live runtime owner, `client.submit("runtime", "pause")` returns a receipt.
Save `receipt.artifact()`, then use `client.result(receipt)` or
`client.wait(receipt, timeout=10)` and inspect `status`. Timeout is indeterminate,
not cancellation. `resume` is a separate request. Queries such as
`client.query("materials", limit=10, timeout=10)` also return a result envelope.
State and retained results need the service, not a live owner. Closed state may
contain failed cleanup; an unavailable/stale observation does not prove closure.

The default service uses Etcd's native Unix socket mounted at
`/run/skyseed-interaction`; it adds no network route or proxy. Docker must be local
to the output host and the program must have socket access (ordinary SEED root
programs do). External endpoints need author-provided reachability; the helper
does not expose credentials or client-certificate configuration.
`close()` only closes this client. Service stop/restart belongs on the host after
owners close; restart preserves records, not Models, containers or kernel state.
See the generated output's root README for control and evidence entry points.
''')
    path = output / 'deployment/docker-compose.yml'
    compose = safe_load(path.read_text())
    for service in compose['services'].values():
        if 'container_name' not in service:
            continue
        environment = service.setdefault('environment', {})
        if isinstance(environment, list):
            environment = dict(item.split('=', 1) if '=' in item else (item, None) for item in environment)
            service['environment'] = environment
        environment.update(SKYSEED_INTERACTION_ENDPOINT=endpoint, SKYSEED_EXPERIMENT_ID=config['experiment_id'])
        mounts = service.setdefault('volumes', [])
        mounts.append({'type': 'bind', 'source': str(client.resolve()), 'target': '/opt/skyseed-interaction', 'read_only': True})
        if endpoint.startswith('unix:') and config.get('client_socket'):
            mounts.append({'type': 'bind', 'source': str(Path(config['client_socket']).parent),
                           'target': '/run/skyseed-interaction', 'read_only': True})
    path.write_text(safe_dump(compose, sort_keys=False))
