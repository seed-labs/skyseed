"""Example-owned etcd service; retain its data and a portable JSON export."""
import json
import shutil
from pathlib import Path
import subprocess
import time
from urllib.error import URLError, HTTPError
import uuid

from skyseed import Store


class RecordStore:
    def __init__(self, output):
        self.output = Path(output).resolve()
        self.name = "skyseed-records-" + uuid.uuid4().hex[:12]

    def __enter__(self):
        self.output.mkdir(parents=True, exist_ok=False)
        database = self.output / "etcd"
        database.mkdir()
        command = ["docker", "run", "--rm", "-d", "--name", self.name,
                   "--label", "skyseed.example=s4-handover",
                   "-p", "127.0.0.1::2379", "--mount", f"type=bind,source={database},target=/data",
                   "quay.io/coreos/etcd:v3.6.5", "/usr/local/bin/etcd",
                   "--data-dir=/data", "--listen-client-urls=http://0.0.0.0:2379",
                   "--advertise-client-urls=http://127.0.0.1:2379"]
        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
            info = json.loads(subprocess.check_output(["docker", "inspect", self.name]))[0]
            port = info["NetworkSettings"]["Ports"]["2379/tcp"][0]["HostPort"]
            self.store = Store(f"http://127.0.0.1:{port}", namespace=self.output.name)
            while True:
                try:
                    self.store.get("ready")
                    break
                except HTTPError:
                    raise
                except URLError:
                    running = subprocess.check_output([
                        "docker", "inspect", "--format", "{{.State.Running}}", self.name], text=True)
                    if running.strip() != "true":
                        raise RuntimeError("example etcd exited during startup")
                    time.sleep(.05)
            return self.store
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup:
                error.add_note(f"record-store cleanup also failed: {cleanup}")
            raise

    def close(self):
        with (self.output / "etcd.log").open("w") as log:
            subprocess.run(["docker", "logs", self.name], stdout=log, stderr=subprocess.STDOUT)
        subprocess.run(["docker", "stop", self.name], check=True, stdout=subprocess.DEVNULL)

    def __exit__(self, kind, error, traceback):
        try:
            self.close()
        except BaseException as cleanup:
            if error is None:
                raise
            error.add_note(f"record-store cleanup also failed: {cleanup}")


def archive_measurements(store, run_output):
    """Upload completed measurements with original timestamps, without retiming.

    Runner writes its events live. User measurements are archived after draining,
    keeping etcd IO out of the traffic loop. PCAP remains a file artifact.
    """
    values = {}
    for name in ("attachment", "tcp-client", "tcp-server"):
        path = run_output / f"{name}.jsonl"
        if name == "tcp-client" and not path.exists():
            store.put_user("measurement-status", {"tcp_client_started": False})
            continue
        for index, line in enumerate(path.read_text().splitlines()):
            values[f"user/{name}/{index:09d}"] = json.loads(line)
            if len(values) == 64:
                store.put_many(values)
                values.clear()
    if values:
        store.put_many(values)
    snapshot = store.list()
    (run_output / "store.json").write_text(json.dumps(snapshot) + "\n")
    return snapshot


def freeze_inputs(scene, plans, run_output, *, build=None, context=None):
    inputs = run_output / "inputs"
    inputs.mkdir()
    scene.dump(inputs / "scene.scene")
    shutil.copyfile(plans, inputs / "bpf.plan")
    if context is not None:
        shutil.copyfile(context, inputs / "observations.json")
    if build is not None:
        (inputs / "build.json").write_text(json.dumps(build, indent=2) + "\n")
    return inputs / "bpf.plan"
