"""SkySEED deployment metadata and deterministic build-context deduplication."""
from pathlib import Path
from hashlib import sha256
from yaml import safe_load, safe_dump

def _write_compose_build_plan(runtime_directory: Path) -> Path:
    """Share one project image per identical context; retain per-node services."""

    runtime = Path(runtime_directory)
    dummy_directory = runtime / "dummies"
    prerequisites = tuple(
        sorted(path.name for path in dummy_directory.iterdir() if path.is_file())
    )
    if not prerequisites:
        raise RuntimeError("generated runtime must have base-image prerequisites")

    compose_path = runtime / "docker-compose.yml"
    compose = safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose['services']
    for service in services.values():
        labels = service.get('labels', {})
        if (service.get('network_mode') == 'none' and
                labels.get('org.seedsecuritylabs.seedemu.meta.skyseed.entity_type') in {'Satellite', 'GroundStation'}):
            service['x-skyseed-disposable-network'] = True
    groups: dict[str, list[str]] = {}
    for directory in sorted(runtime.iterdir(), key=lambda path: path.name):
        if not directory.is_dir() or not (directory / "Dockerfile").is_file():
            continue
        service = services.get(directory.name)
        if service is None or service.get("build") != f"./{directory.name}":
            raise RuntimeError("generated node build context does not match its service")
        digest = sha256()
        for path in sorted(
            directory.rglob("*"),
            key=lambda path: path.relative_to(directory).as_posix(),
        ):
            relative = path.relative_to(directory).as_posix().encode("utf-8")
            if path.is_symlink():
                raise RuntimeError("generated build context must not contain symlinks")
            digest.update(path.stat().st_mode.to_bytes(4, "big"))
            content = path.read_bytes() if path.is_file() else b""
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        groups.setdefault(digest.hexdigest(), []).append(directory.name)
    if not groups:
        raise RuntimeError("generated runtime has no node build contexts")

    lines = [
        "# skyseed-v2-compose-build-plan/v2",
        f"# contexts={sum(len(group) for group in groups.values())}",
        f"# identical-content-groups={len(groups)}",
        *(f"prerequisite {service}" for service in prerequisites),
    ]
    for fingerprint, group in sorted(groups.items(), key=lambda item: item[1][0]):
        representative = group[0]
        image_suffix = f"skyseed-{fingerprint}"
        lines.append(f"build {representative} {image_suffix}")
        for name in group:
            services[name]["image"] = f"${{COMPOSE_PROJECT_NAME}}-{image_suffix}"
            if name != representative:
                del services[name]["build"]
    compose_path.write_text(safe_dump(compose, sort_keys=False), encoding="utf-8")
    plan = runtime / "skyseed-build-plan.txt"
    plan.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return plan
