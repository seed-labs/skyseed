"""Start the S4 run's private etcd and release it with the experiment."""
from contextlib import contextmanager
import json
import subprocess
import time
from urllib.error import HTTPError, URLError

from skyseed import Store


@contextmanager
def map_store(namespace):
    container = subprocess.check_output([
        "docker", "run", "-d", "--label", "skyseed.example=s4-handover",
        "-p", "127.0.0.1::2379", "quay.io/coreos/etcd:v3.6.5", "/usr/local/bin/etcd",
        "--data-dir=/tmp/skyseed-etcd", "--listen-client-urls=http://0.0.0.0:2379",
        "--advertise-client-urls=http://127.0.0.1:2379",
    ], text=True).strip()
    try:
        info = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]
        port = info["NetworkSettings"]["Ports"]["2379/tcp"][0]["HostPort"]
        store = Store(f"http://127.0.0.1:{port}", namespace=namespace)
        while True:
            try:
                store.get("ready")
                break
            except (URLError, ConnectionError) as error:
                if isinstance(error, HTTPError) and error.code < 500:
                    raise
                running = subprocess.check_output(
                    ["docker", "inspect", "-f", "{{.State.Running}}", container], text=True)
                if running.strip() != "true":
                    logs = subprocess.check_output(["docker", "logs", container], stderr=subprocess.STDOUT, text=True)
                    raise RuntimeError(f"Map etcd exited:\n{logs}")
                time.sleep(.1)
        yield store
    finally:
        subprocess.run(["docker", "rm", "-f", container], check=True, stdout=subprocess.DEVNULL)
