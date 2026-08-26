"""Write the offline lazy Planner inspection bundle."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from tools._html import write_json, write_text
from tools._chunk_encoding import CHUNK_ENCODING, encoded_chunk_json


def write_planner_artifacts(
    data: dict[str, Any],
    page: str,
    directory: Path,
) -> dict[str, object]:
    """Write a compact manifest plus deterministic file-compatible chunks."""

    if not isinstance(page, str):
        raise TypeError("page must be rendered text")
    output = Path(directory)
    chunks_directory = output / "chunks"
    chunks_directory.mkdir(parents=True, exist_ok=True)
    page_path = output / "index.html"
    manifest_path = output / "planner-manifest.json"
    manifest_script_path = output / "planner-manifest.js"
    summary_path = output / "summary.txt"
    inventory_path = output / "inventory.json"
    checksum_path = output / "checksums.sha256"

    (output / "planner-inspection.json").unlink(missing_ok=True)
    for stale_chunk in chunks_directory.glob("*.js"):
        stale_chunk.unlink()

    payloads = data.get("_chunk_payloads")
    if not isinstance(payloads, dict):
        raise TypeError("Planner projection lacks chunk payloads")
    manifest = {
        key: value for key, value in data.items() if key != "_chunk_payloads"
    }
    manifest["chunk_encoding"] = CHUNK_ENCODING
    write_json(manifest_path, manifest)
    compact_manifest = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    write_text(
        manifest_script_path,
        f"window.SKYSEED_PLANNER_MANIFEST={compact_manifest};\n",
    )
    write_text(page_path, page)
    chunk_paths = []
    for relative_path, value in sorted(payloads.items()):
        chunk_path = output / relative_path
        write_text(
            chunk_path,
            "window.__SKYSEED_PLANNER_CHUNKS__="
            "window.__SKYSEED_PLANNER_CHUNKS__||{};"
            f"window.__SKYSEED_PLANNER_CHUNKS__[{json.dumps(relative_path)}]="
            f"{encoded_chunk_json(value)};\n",
        )
        chunk_paths.append(chunk_path)

    inventory = manifest["inventory"]
    behavior = manifest["observed_behavior"]
    write_text(
        summary_path,
        "\n".join(
            (
                "status=PASS",
                "tool=planner-inspection",
                "authority=non-authoritative-view",
                "representation=offline-hierarchical-compressed-planner-chunks",
                f"chunk_encoding={CHUNK_ENCODING}",
                *(f"{key}={value}" for key, value in inventory.items()),
                *(f"behavior_{key}={value}" for key, value in behavior.items()),
                "",
            )
        ),
    )
    payload_files = (
        page_path,
        manifest_path,
        manifest_script_path,
        summary_path,
        *chunk_paths,
    )
    bundle_inventory = {
        "format_version": 4,
        "representation": "offline-hierarchical-compressed-planner-chunks",
        "chunk_encoding": CHUNK_ENCODING,
        "files": [_file_record(path, output) for path in payload_files],
    }
    write_json(inventory_path, bundle_inventory)
    checksum_files = (*payload_files, inventory_path)
    write_text(
        checksum_path,
        "".join(
            f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n"
            for path in checksum_files
        ),
    )
    return {
        "status": "PASS",
        "message": (
            f"{inventory['plans']} Plans / "
            f"{inventory['differences']} Differences / "
            f"{inventory['planning_decisions']} decisions"
        ),
        "directory": output,
        "files": tuple(
            path.relative_to(output).as_posix()
            for path in (*checksum_files, checksum_path)
        ),
        "inventory": inventory,
        "observed_behavior": behavior,
    }


def _file_record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["write_planner_artifacts"]
