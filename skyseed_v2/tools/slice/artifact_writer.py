"""Write Slice / Difference projection, UI, inventory, and checksums."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from .._html import write_json, write_text
def write_slice_difference_artifacts(
    data: dict[str, Any],
    page: str,
    directory: Path,
) -> dict[str, object]:
    """Write one dependency-free Slice / Difference inspection bundle."""

    if not isinstance(page, str):
        raise TypeError("page must be rendered text")
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    data_path = output / "slice-difference.json"
    page_path = output / "index.html"
    summary_path = output / "summary.txt"
    inventory_path = output / "inventory.json"
    checksum_path = output / "checksums.sha256"

    write_json(data_path, data)
    write_text(page_path, page)
    inventory = data["inventory"]
    behavior = data["behavior"]
    assert isinstance(inventory, dict)
    assert isinstance(behavior, dict)
    write_text(
        summary_path,
        "\n".join(
            (
                "status=PASS",
                "tool=slice-difference-inspection",
                "authority=non-authoritative-view",
                *(f"{key}={value}" for key, value in inventory.items()),
                *(
                    f"behavior_{key}={value}"
                    for key, value in behavior.items()
                ),
                "",
            )
        ),
    )
    payload_files = (page_path, data_path, summary_path)
    bundle_inventory = {
        "format_version": 1,
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
            f"{inventory['paths']} resolved paths"
        ),
        "directory": output,
        "files": tuple(path.name for path in (*checksum_files, checksum_path)),
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
