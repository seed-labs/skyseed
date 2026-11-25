"""Explicit lifecycle and container lookup for a SEED Compose deployment."""
import json
from pathlib import Path
import subprocess


class Deployment:
    def __init__(self, directory, *, project=None, compose_file="docker-compose.yml"):
        self.directory = Path(directory).resolve()
        self.command = ["docker", "compose", "-f", str(self.directory / compose_file)]
        if project:
            self.command.extend(["-p", project])

    def up(self, *, build=False):
        command = [*self.command, "up", "-d"]
        if build:
            command.append("--build")
        subprocess.run(command, check=True)

    def down(self):
        subprocess.run([*self.command, "down"], check=True)

    def containers(self):
        result = subprocess.run([*self.command, "ps", "-q"], check=True, capture_output=True, text=True)
        ids = result.stdout.split()
        if not ids:
            raise RuntimeError(f"no running deployment containers: {self.directory}")
        facts = json.loads(subprocess.run(["docker", "inspect", *ids], check=True,
                                         capture_output=True, text=True).stdout)
        return {item["Config"]["Labels"]["com.docker.compose.service"]: item for item in facts}


    def __enter__(self):
        try:
            self.up()
        except BaseException as error:
            try:
                self.down()
            except BaseException as cleanup:
                error.add_note(f"deployment cleanup also failed: {cleanup}")
            raise
        return self

    def __exit__(self, kind, error, traceback):
        try:
            self.down()
        except BaseException as cleanup:
            if error is None:
                raise
            error.add_note(f"deployment cleanup also failed: {cleanup}")
