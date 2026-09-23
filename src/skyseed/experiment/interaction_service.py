"""Explicit, output-scoped single-member etcd startup and shutdown.

The service survives computation exit. It is stopped explicitly after owners have
closed; an external endpoint is never stopped. Persistent KV is not process recovery.
Start after explicit stop restores records only, not Models or running resources.
Stop preserves a closed owner's failed outcome; it does not certify resource cleanup.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
from time import sleep
from uuid import uuid4
from urllib.parse import urlparse
from .interaction import InteractionClient, InteractionUnavailable


def _free_port(host='127.0.0.1'):
    with socket.socket() as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def _process_identity(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except OSError:
        return None


def start_interaction(output, *, endpoint=None, container_endpoint=None, executable=None,
                      listen_host='127.0.0.1'):
    """Start one owned service, or attach an explicitly supplied external endpoint.

    Call after output preparation. No global service or host networking is changed.
    Owned services provide a native Unix listener for local Docker containers.
    External services require an explicitly reachable container endpoint.
    """
    root = Path(output).resolve() / '.control'
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'interaction.json'
    with (root / 'interaction-start.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = json.loads(path.read_text()) if path.exists() else None
        if previous and not previous.get('stopped'):
            client = InteractionClient(previous['endpoint'], previous['experiment_id'])
            client._call('status')
            return client
        # Restart only explicitly stopped owned services; persistent records do
        # not recreate Models, consumers, or kernel state.
        if previous and previous['owned']:
            experiment_id = previous['experiment_id']
            listen_host = previous.get('listen_host', listen_host)
            endpoint = None
            container_endpoint = previous.get('container_endpoint_template', previous.get('container_endpoint'))
        else:
            experiment_id = previous['experiment_id'] if previous else uuid4().hex
            endpoint = endpoint or (previous['endpoint'] if previous else None)
        owned = endpoint is None
        container_endpoint_template = container_endpoint
        process = None
        if owned:
            binary = executable or os.environ.get('SKYSEED_ETCD_EXECUTABLE') or shutil.which('etcd')
            if binary is None:
                cached = Path.home() / '.local/share/skyseed/etcd/etcd'
                binary = str(cached) if cached.is_file() else None
            if binary is None:
                raise RuntimeError('etcd executable unavailable; run scripts/setup_interaction.py or set SKYSEED_ETCD_EXECUTABLE')
            port = (urlparse(previous['endpoint']).port if previous and previous['owned']
                    else _free_port(listen_host))
            peer = _free_port()
            endpoint = f'http://127.0.0.1:{port}'
            if container_endpoint:
                container_endpoint = container_endpoint.format(port=port)
            peer_url = f'http://127.0.0.1:{peer}'
            # Mount the directory (not the socket inode) so restart remains usable.
            import tempfile
            socket_dir = Path(tempfile.gettempdir()) / ('skyseed-etcd-' + experiment_id)
            socket_dir.mkdir(mode=0o700, exist_ok=True)
            alias = root / 'etcd-client'
            if not alias.is_symlink():
                alias.symlink_to(socket_dir, target_is_directory=True)
            client_socket = socket_dir / 'etcd.sock'
            client_socket.unlink(missing_ok=True)
            container_endpoint = container_endpoint or 'unix:///run/skyseed-interaction/etcd.sock'
            with (root / 'etcd.log').open('ab') as log:
                process = subprocess.Popen([binary, '--name', experiment_id,
                    '--data-dir', str(root / 'etcd-data'),
                    '--auto-compaction-mode', 'periodic', '--auto-compaction-retention', '1h',
                    '--listen-client-urls', f'http://{listen_host}:{port},unix://{client_socket}',
                    '--advertise-client-urls', endpoint,
                    '--listen-peer-urls', peer_url, '--initial-advertise-peer-urls', peer_url,
                    '--initial-cluster', f'{experiment_id}={peer_url}',
                    '--initial-cluster-token', experiment_id],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                    env={key: value for key, value in os.environ.items() if key.lower() not in {'http_proxy', 'https_proxy', 'all_proxy'}})
        client = InteractionClient(endpoint, experiment_id)
        try:
            while True:
                try:
                    client._call('status')
                    break
                except InteractionUnavailable:
                    if process is None or process.poll() is not None:
                        raise
                    sleep(.05)
        except BaseException:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait()
            raise
        value = {'format_version': 1, 'experiment_id': experiment_id, 'endpoint': endpoint,
                 'container_endpoint': container_endpoint, 'owned': owned, 'listen_host': listen_host,
                 'container_endpoint_template': container_endpoint_template,
                 'client_socket': None if not owned else str(client_socket),
                 'pid': None if process is None else process.pid,
                 'process_start': None if process is None else _process_identity(process.pid),
                 'mvcc_retention': {'mode': 'periodic', 'retention': '1h'} if owned else None,
                 'persistence': '.control/etcd-data', 'recovery': 'records-only'}
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, indent=2) + '\n')
        os.replace(temporary, path)
        return client


def stop_interaction(output):
    """Stop only our service, after both owner publications explicitly close.

    Stale live owners are indeterminate and require manual investigation; their
    disappearance is not evidence that Linux resources or children were drained.
    Explicit closure can carry a failed outcome. This stops only Etcd, preserving
    its records; it neither certifies nor repairs Runner/Traffic/deployment cleanup.
    """
    from .output.occupancy import claim_output, claim_runtime
    # Exclude owners that are starting or still cleaning up, including the gap
    # before their first publication. The same locks protect actual ownership.
    with claim_output(output, replace=True), claim_runtime(output):
        with (Path(output) / '.control/interaction-start.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            _stop_interaction(output)


def _stop_interaction(output):
    path = Path(output) / '.control/interaction.json'
    value = json.loads(path.read_text())
    if value.get('stopped'):
        return
    client = InteractionClient.from_output(output)
    try:
        for owner in ('computation', 'runtime'):
            if client.state(owner).get('live'):
                raise RuntimeError(f'{owner} has not confirmed closure; service retained')
    finally:
        client.close()
    if value['owned']:
        pid = value['pid']
        if _process_identity(pid) == value['process_start']:
            os.kill(pid, signal.SIGTERM)
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                while _process_identity(pid) == value['process_start']:
                    # A reparented zombie has already released sockets and files.
                    if Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                        break
                    sleep(.05)
        if value.get('client_socket'):
            socket_path = Path(value['client_socket'])
            socket_path.unlink(missing_ok=True)
            socket_path.parent.rmdir()
    value['stopped'] = True
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop'))
    parser.add_argument('output', type=Path)
    parser.add_argument('--endpoint', help='attach an external Etcd v3 HTTP gateway; its lifecycle remains external')
    parser.add_argument('--listen-host', default='127.0.0.1')
    parser.add_argument('--container-endpoint', help='reachable container URL; owned service defaults to a mounted Unix listener, optional {port} substitution')
    args = parser.parse_args()
    if args.action == 'stop':
        stop_interaction(args.output)
    else:
        start_interaction(args.output, endpoint=args.endpoint, listen_host=args.listen_host,
                          container_endpoint=args.container_endpoint)


if __name__ == '__main__':
    main()
