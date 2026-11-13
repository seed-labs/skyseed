"""Build identical flat SEED node contexts once; emit a ready-to-run Compose file."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

import yaml


def build_images(directory):
    directory = Path(directory).resolve()
    compose = yaml.safe_load((directory / "docker-compose.yml").read_text())
    started = time.monotonic()
    bases = {}
    for service in compose["services"].values():
        build = service.get("build")
        if not isinstance(build, dict):
            continue
        subprocess.run(["docker", "build", "-f", str(directory / build["dockerfile"]),
                        "-t", service["image"], str(directory / build["context"])], check=True)
        result = subprocess.run(["docker", "image", "inspect", service["image"], "--format", "{{.Id}}"],
                                check=True, capture_output=True, text=True)
        bases[service["image"]] = result.stdout.strip()
        del service["build"]
    images = {}
    nodes = 0
    for service in compose["services"].values():
        context = service.get("build")
        if not isinstance(context, str):
            continue
        nodes += 1
        directory_node = directory / context
        digest = hashlib.sha256()
        # SEED emits flat node contexts. Fail clearly on another layout instead
        # of silently excluding files or claiming generic Docker build support.
        for path in sorted(directory_node.iterdir()):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        parent = (directory_node / "Dockerfile").read_text().splitlines()[0].split()[1]
        digest.update(bases[parent].encode())
        identity = digest.hexdigest()
        if identity not in images:
            image = "skyseed-build:" + identity[:24]
            subprocess.run(["docker", "build", "-t", image, str(directory_node)], check=True)
            images[identity] = image
        service["image"] = images[identity]
        service["init"] = True
        del service["build"]
    target = directory / "docker-compose.runtime.yml"
    target.write_text(yaml.safe_dump(compose, sort_keys=False))
    report = {"node_services": nodes, "distinct_node_images": len(images),
              "build_seconds": time.monotonic() - started, "compose": str(target)}
    (directory / "image-build.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_images(args.directory), indent=2))
