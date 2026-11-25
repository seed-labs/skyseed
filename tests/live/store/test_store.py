"""Real etcd gateway contract; opt in with SKYSEED_ETCD pointing to a binary."""
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.error import URLError, HTTPError

import pytest

from skyseed import Store


def free_port():
    with socket.socket() as channel:
        channel.bind(("127.0.0.1", 0))
        return channel.getsockname()[1]


def test_json_values_namespace_and_revisioned_user_watch(tmp_path):
    binary = os.environ.get("SKYSEED_ETCD")
    if not binary:
        pytest.skip("set SKYSEED_ETCD for the live Store test")
    client, peer = free_port(), free_port()
    endpoint, peer_url = f"http://127.0.0.1:{client}", f"http://127.0.0.1:{peer}"
    with (tmp_path / "etcd.log").open("w") as log:
        process = subprocess.Popen([binary, "--data-dir", str(tmp_path / "database"),
            "--listen-client-urls", endpoint, "--advertise-client-urls", endpoint,
            "--listen-peer-urls", peer_url, "--initial-advertise-peer-urls", peer_url,
            "--initial-cluster", "default=" + peer_url], stdout=log, stderr=subprocess.STDOUT)
        try:
            store = Store(endpoint, namespace="first")
            while True:
                try:
                    assert store.get("missing") is None
                    break
                except HTTPError:
                    raise
                except URLError:
                    assert process.poll() is None, (tmp_path / "etcd.log").read_text()
                    time.sleep(.05)
            revision = store.put_user("tcp", {"sequence": 1, "open": True})
            store.put_user("tcp", {"sequence": 2, "open": True})
            with store.watch("user/", revision=revision) as changes:
                first, second = next(changes), next(changes)
            assert first["value"]["sequence"] == 1
            assert second["value"]["sequence"] == 2
            assert second["revision"] > first["revision"]
            store.put_many({"runner/status": "ready", "runner/origin": 1.5})
            assert store.list("runner/") == {"runner/status": "ready", "runner/origin": 1.5}
            assert Store(endpoint, namespace="second").get_user("tcp") is None
            assert store.get_user("tcp") == {"sequence": 2, "open": True}
        finally:
            process.terminate()
            process.wait()
