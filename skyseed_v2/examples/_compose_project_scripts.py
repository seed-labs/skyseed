"""Write human-facing controls after an example finishes its output."""

from __future__ import annotations

from pathlib import Path
from re import fullmatch
from shlex import quote


_PARALLEL_LIMIT = 16


def write_compose_project_scripts(
    output_directory: Path,
    project_name: str,
) -> None:
    """Add executable start/cleanup controls to one complete Compose output."""

    output = Path(output_directory)
    compose_file = output / "docker-compose.yml"
    if not compose_file.is_file():
        raise ValueError(
            "output_directory must contain a compiled docker-compose.yml"
        )
    if not isinstance(project_name, str) or fullmatch(
        r"[a-z0-9][a-z0-9_-]*", project_name
    ) is None:
        raise ValueError(
            "project_name must contain only lowercase letters, digits, "
            "hyphens and underscores"
        )

    shell_project_name = quote(project_name)
    _write_executable(
        output / "start.sh",
        f"""#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
PROJECT_NAME={shell_project_name}
COMPOSE=(
    docker compose
    --parallel {_PARALLEL_LIMIT}
    -p "$PROJECT_NAME"
    -f "$COMPOSE_FILE"
)

"${{COMPOSE[@]}}" config --quiet
"${{COMPOSE[@]}}" up -d --build
""",
    )
    _write_executable(
        output / "cleanup.sh",
        f"""#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
PROJECT_NAME={shell_project_name}
COMPOSE=(
    docker compose
    --parallel {_PARALLEL_LIMIT}
    -p "$PROJECT_NAME"
    -f "$COMPOSE_FILE"
)

"${{COMPOSE[@]}}" down --remove-orphans
""",
    )


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
