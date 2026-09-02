"""Write a deterministic detached Planner inspection bundle."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from tools._html import write_json, write_text


def write_planner_artifacts(
    data: dict[str, Any], page: str, directory: Path
) -> dict[str, object]:
    """Persist machine-readable evidence, HTML, inventory, and checksums."""

    if not isinstance(page, str):
        raise TypeError("page must be rendered text")
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    chunk_paths = _write_chunks(data, output)
    data_path = output / "planner-inspection.json"
    page_path = output / "index.html"
    summary_path = output / "summary.txt"
    inventory_path = output / "inventory.json"
    checksum_path = output / "checksums.sha256"
    write_json(data_path, _public_projection(data))
    write_text(page_path, page)
    inventory = data["inventory"]
    write_text(
        summary_path,
        "\n".join(
            (
                "status=PASS",
                "tool=planner-inspection",
                "authority=non-authoritative-view",
                "source=published-planning-evidence",
                *(f"{key}={value}" for key, value in inventory.items()),
                "",
            )
        ),
    )
    payloads = (data_path, page_path, summary_path, *chunk_paths)
    write_json(
        inventory_path,
        {
            "format_version": 1,
            "files": [_file_record(path, output) for path in payloads],
        },
    )
    checksum_files = (*payloads, inventory_path)
    write_text(
        checksum_path,
        "".join(
            f"{_digest(path)}  {path.relative_to(output).as_posix()}\n"
            for path in checksum_files
        ),
    )
    return {
        "status": "PASS",
        "message": (
            f"{inventory['plans']} Plans / "
            f"{inventory['formal_transitions']} formal transitions"
        ),
        "directory": output,
        "files": tuple(
            path.relative_to(output).as_posix()
            for path in (*checksum_files, checksum_path)
        ),
        "inventory": dict(inventory),
    }


def _public_projection(data: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: value
        for key, value in data.items()
        if not key.startswith("_")
        and key not in {"frames", "final_state", "publications"}
    }
    public["publications"] = tuple(
        {
            key: value
            for key, value in publication.items()
            if key != "semantic_event_precedences"
        }
        | {
            "semantic_event_precedence_count": publication.get(
                "semantic_event_precedence_count",
                len(publication.get("semantic_event_precedences", ())),
            )
        }
        for publication in data["publications"]
    )
    return public


def _write_chunks(data: dict[str, Any], output: Path) -> tuple[Path, ...]:
    directory = output / "data"
    directory.mkdir(parents=True, exist_ok=True)
    paths = list(data.get("_streamed_chunk_paths", ()))
    for value in data.get("_tool_frame_chunks", ()):
        path = directory / f"frame-{value['frame_index']:03d}.js"
        write_text(
            path,
            "window.SKYSEED_PLANNER_FRAME_CHUNK="
            + json.dumps(value, sort_keys=True, separators=(",", ":"))
            + ";\n",
        )
        paths.append(path)
    shards: dict[int, dict[str, Any]] = {}
    for key, value in data.get("_route_timelines", {}).items():
        shards.setdefault(int(value["shard"]), {})[key] = value
    for shard, values in sorted(shards.items()):
        path = directory / f"routes-{shard:02d}.js"
        write_text(
            path,
            "window.SKYSEED_PLANNER_ROUTE_SHARDS="
            "window.SKYSEED_PLANNER_ROUTE_SHARDS||{};"
            f"window.SKYSEED_PLANNER_ROUTE_SHARDS[{shard}]="
            + json.dumps(values, sort_keys=True, separators=(",", ":"))
            + ";\n",
        )
        paths.append(path)
    return tuple(paths)


def _file_record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _digest(path),
    }


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


__all__ = ["write_planner_artifacts"]
