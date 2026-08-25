"""Write the offline chunked Slice / Difference inspection bundle."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .._html import write_json, write_text


def write_slice_difference_artifacts(
    data: dict[str, Any],
    page: str,
    directory: Path,
) -> dict[str, object]:
    """Write one dependency-free, lazily loaded inspection bundle."""

    if not isinstance(page, str):
        raise TypeError("page must be rendered text")
    output = Path(directory)
    chunks_directory = output / "chunks"
    chunks_directory.mkdir(parents=True, exist_ok=True)
    page_path = output / "index.html"
    manifest_path = output / "slice-manifest.json"
    summary_path = output / "summary.txt"
    inventory_path = output / "inventory.json"
    checksum_path = output / "checksums.sha256"

    # Keep repeat writes deterministic when a shorter history replaces a
    # longer one, and retire the former monolithic representation.
    (output / "slice-difference.json").unlink(missing_ok=True)
    for stale_chunk in chunks_directory.glob("slice-*.js"):
        stale_chunk.unlink()

    slices = data["slices"]
    inventory = data["inventory"]
    behavior = data["behavior"]
    assert isinstance(slices, list)
    assert isinstance(inventory, dict)
    assert isinstance(behavior, dict)
    manifest = {key: value for key, value in data.items() if key != "slices"}
    write_json(manifest_path, manifest)
    write_text(page_path, page)
    chunk_paths = []
    for slice_record in slices:
        slice_id = int(slice_record["slice_id"])
        chunk_path = chunks_directory / f"slice-{slice_id:06d}.js"
        compact = json.dumps(
            slice_record,
            ensure_ascii=False,
            separators=(",", ":"),
        ).replace("</", "<\\/")
        write_text(
            chunk_path,
            "window.__SKYSEED_SLICE_CHUNKS__="
            "window.__SKYSEED_SLICE_CHUNKS__||{};"
            f"window.__SKYSEED_SLICE_CHUNKS__[{slice_id}]={compact};\n",
        )
        chunk_paths.append(chunk_path)

    write_text(
        summary_path,
        "\n".join(
            (
                "status=PASS",
                "tool=slice-difference-inspection",
                "authority=non-authoritative-view",
                "representation=offline-lazy-slice-chunks",
                *(f"{key}={value}" for key, value in inventory.items()),
                *(f"behavior_{key}={value}" for key, value in behavior.items()),
                "",
            )
        ),
    )
    payload_files = (
        page_path,
        manifest_path,
        summary_path,
        *chunk_paths,
    )
    bundle_inventory = {
        "format_version": 2,
        "representation": "offline-lazy-slice-chunks",
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
            f"{inventory['slices']} Slices / "
            f"{inventory['differences']} Differences / "
            f"{inventory['route_entries']} compact Route entries"
        ),
        "directory": output,
        "files": tuple(
            path.relative_to(output).as_posix()
            for path in (*checksum_files, checksum_path)
        ),
        "inventory": inventory,
        "behavior": behavior,
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


__all__ = ["write_slice_difference_artifacts"]
