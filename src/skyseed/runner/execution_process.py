"""Private JSON-lines protocol used by one persistent privileged process."""
import base64
import json
import resource
import sys
import time
import traceback

from pathlib import Path
import subprocess
import uuid


class CommandChannel:
    def __init__(self, image, backend, program, diagnostics):
        self.name = "skyseed-runner-" + uuid.uuid4().hex[:12]
        self.diagnostics = Path(diagnostics).open("w")
        command = ["docker", "run", "--rm", "-i", "--privileged", "--pid", "host", "--network", "host",
                   "--mount", "type=bind,source=/,target=/host,readonly", "--name", self.name,
                   "--label", "skyseed.runner=" + self.name, "--entrypoint", "/usr/sbin/chroot", image,
                   "/host", sys.executable, "-m", "skyseed.runner.execution_process"]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.diagnostics, text=True, bufsize=1, start_new_session=True)
        try:
            self.request("open", backend=backend,
                         program=base64.b64encode(program).decode() if program else None)
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup:
                error.add_note(f"helper cleanup also failed: {cleanup}")
            raise

    def request(self, command, **parameters):
        self.process.stdin.write(json.dumps({"command": command, **parameters}, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"Runner helper exited; inspect {self.diagnostics.name}")
        response = json.loads(line)
        if "error" in response:
            raise RuntimeError(response["error"])
        return response

    def close(self):
        try:
            if self.process.poll() is None:
                self.request("close")
        finally:
            self.process.stdin.close()
            self.process.wait()
            self.process.stdout.close()
            self.diagnostics.close()


def main():
    from .instruction_executor import InstructionExecutor

    # Retained namespaces, sockets, programs and version maps need more than
    # Docker's small default soft limit. Use this process's granted hard limit;
    # no host or daemon configuration is changed.
    _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    network = None
    jobs = {}
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                command = request.pop("command")
                result = None
                started = time.monotonic()
                if command == "open":
                    program = base64.b64decode(request["program"]) if request.get("program") else None
                    network = InstructionExecutor(request["backend"], program)
                elif command == "nodes":
                    network.add_nodes(request["targets"])
                elif command == "prefixes":
                    network.inject_prefixes(request["prefixes"])
                elif command == "apply":
                    network.apply(request["instructions"])
                elif command == "prepare":
                    jobs[request["job"]] = network.labels.prepare(request["instructions"])
                elif command == "prepare_node":
                    jobs[request["job"]] = network.labels.prepare_node(request["node"], request["instructions"])
                elif command == "generation":
                    jobs[request["job"]] = network.labels.generation(request["updates"])
                elif command == "reclaim":
                    jobs[request["job"]] = network.labels.reclaim(request["generation"])
                elif command == "step":
                    job = request["job"]
                    try:
                        next(jobs[job])
                        result = {"done": False}
                    except StopIteration as done:
                        del jobs[job]
                        result = {"done": True, "value": done.value}
                elif command == "publish":
                    result = network.publish(request["generation"])
                elif command == "close":
                    network.close()
                    network = None
                else:
                    raise ValueError(f"unknown Runner command {command}")
                response = {"result": result, "started": started, "completed": time.monotonic()}
            except Exception:
                response = {"error": traceback.format_exc()}
            print(json.dumps(response, separators=(",", ":")), flush=True)
            if command == "close":
                break
    finally:
        if network is not None:
            network.close()


if __name__ == "__main__":
    main()
