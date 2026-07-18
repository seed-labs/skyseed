"""Compose evidence helper local to the large static example."""

from pathlib import Path

from yaml import safe_load

from skyseed_v2.materialization import ComposeServiceExpectations


def materialization_expectations(
    compose_file: Path,
) -> ComposeServiceExpectations:
    """Classify scenario nodes/map and SEED image-cache helpers."""

    compose = safe_load(Path(compose_file).read_text(encoding="utf-8"))
    if not isinstance(compose, dict) or not isinstance(
        compose.get("services"), dict
    ):
        raise ValueError("compiled Compose must contain a services mapping")
    long_running = []
    successful_exit_helpers = []
    for service_name, service in compose["services"].items():
        if not isinstance(service_name, str) or not isinstance(service, dict):
            raise ValueError("compiled Compose contains an invalid service")
        build = service.get("build")
        dockerfile = build.get("dockerfile") if isinstance(build, dict) else None
        if isinstance(dockerfile, str) and dockerfile.startswith("dummies/"):
            successful_exit_helpers.append(service_name)
        else:
            long_running.append(service_name)
    return ComposeServiceExpectations(
        long_running=tuple(long_running),
        successful_exit_helpers=tuple(successful_exit_helpers),
    )
