"""Private output protection, backend packaging and generation measurements."""
import json
from pathlib import Path
import re
import shutil
import subprocess
from time import perf_counter


_PROJECT_NAME_FILE = "deployment/compose-project-name"


def _guard_existing_output(output: Path) -> None:
    """Never replace controls while their Compose project exists."""

    if not output.exists() and not output.is_symlink():
        return
    discovery = output / '.control/interaction.json'
    if discovery.is_file() and not json.loads(discovery.read_text()).get('stopped'):
        raise RuntimeError('existing interaction service must be explicitly stopped before output replacement')
    project_file = output / _PROJECT_NAME_FILE
    if not project_file.is_file():
        legacy = output / ".compose-project-name"
        if legacy.is_file():
            project_file = legacy
        elif (output / "deployment/docker-compose.yml").is_file():
            raise RuntimeError(
                "existing generated output has no verifiable Compose identity; "
                "remove it explicitly before regenerating"
            )
        else:
            return
    project_name = project_file.read_text(encoding="utf-8").strip()
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_name) is None:
        raise RuntimeError("existing output generation identity is invalid")
    try:
        result = subprocess.run(
            (
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise RuntimeError("cannot verify existing generated deployment") from error
    if result.returncode != 0:
        raise RuntimeError(
            "cannot verify existing generated deployment: "
            + result.stderr.strip()
        )
    if result.stdout.strip():
        raise RuntimeError(
            "the existing generated deployment still has containers; run "
            f"{output / 'cleanup.sh'} before regenerating"
        )


def _append_phase(
    records: list[dict[str, object]] | None,
    name: str,
    category: str,
    seconds: float,
    *,
    additive: bool,
    interval_kind: str = "span",
    started_monotonic: float | None = None,
    ended_monotonic: float | None = None,
) -> None:
    if records is not None:
        if interval_kind not in {"span", "aggregate"}:
            raise ValueError("interval_kind must be span or aggregate")
        ended = perf_counter() if ended_monotonic is None else ended_monotonic
        started = (
            ended - seconds
            if started_monotonic is None and interval_kind == "span"
            else ended
            if started_monotonic is None
            else started_monotonic
        )
        records.append(
            {
                "phase": name,
                "category": category,
                "wall_seconds": seconds,
                "additive": additive,
                "interval_kind": interval_kind,
                "started_monotonic": started,
                "ended_monotonic": ended,
            }
        )


def _write_orchestration_timing(
    inspection: Path,
    records: list[dict[str, object]],
    *,
    total_wall_seconds: float,
    run_origin_monotonic: float,
) -> None:
    categories: dict[str, float] = {}
    attributed = 0.0
    for record in records:
        if not record["additive"]:
            continue
        seconds = float(record["wall_seconds"])
        category = str(record["category"])
        categories[category] = categories.get(category, 0.0) + seconds
        attributed += seconds
    payload = {
        "format_version": 2,
        "artifact_type": "example-orchestration-timing",
        "clock": "time.perf_counter wall seconds",
        "records": [
            {
                **record,
                "run_relative_start_seconds": max(
                    0.0,
                    float(record["started_monotonic"])
                    - run_origin_monotonic,
                ),
                "run_relative_end_seconds": max(
                    0.0,
                    float(record["ended_monotonic"])
                    - run_origin_monotonic,
                ),
            }
            for record in records
        ],
        "category_totals": categories,
        "total_wall_seconds": total_wall_seconds,
        "attributed_additive_seconds": attributed,
        "unattributed_residual_seconds": max(
            0.0, total_wall_seconds - attributed
        ),
        "interpretation": {
            "semantic-core": "authoritative production work",
            "inspection-evidence-capture": (
                "queue submission/wait; serialization runs in a bounded Tool worker"
            ),
            "persistence": "serialization, packaging, and actual writes; see separate operation timings",
            "detached-inspection": "Tool-owned side work outside semantics",
            "sealing-finalization": "terminal sealing, manifests, and hashes",
            "other": "assembly and orchestration not in the other categories",
            "non_additive": (
                "per-Tool worker timings may overlap the detached batch wall"
            ),
            "span": (
                "measured monotonic start/end; eligible for timeline geometry"
            ),
            "aggregate": (
                "interleaved accumulated duration; reported numerically and "
                "never drawn as a fabricated contiguous interval"
            ),
        },
    }
    inspection.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    (inspection / "orchestration-timing.json").write_text(
        text, encoding="utf-8"
    )


def _prepare_generated_output(output_directory: Path, *, replace: bool) -> Path:
    """Create one clean generated output root and return its runtime path."""

    output = Path(output_directory)
    if output.exists() or output.is_symlink():
        if not replace:
            raise FileExistsError(f"example output already exists: {output}")
        if output.is_symlink() or output.is_file():
            output.unlink()
        else:
            shutil.rmtree(output)
    output.mkdir(parents=True)
    return output / "deployment"
