"""Write one deterministic offline System Observation bundle."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from tools._html import write_json, write_text

from .reader import ObservationRead


def write_observation_artifacts(
    observations: ObservationRead,
    data: dict[str, Any],
    page: str,
    directory: Path,
) -> dict[str, object]:
    """Persist machine evidence, compact aggregates, and browser bridge."""

    if not isinstance(observations, ObservationRead):
        raise TypeError("observations must be an ObservationRead")
    if not isinstance(page, str):
        raise TypeError("page must be rendered text")
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    records_path = output / "execution-observations.jsonl"
    browser_path = output / "observation-records.js"
    summary_data_path = output / "observation-summary.json"
    page_path = output / "index.html"
    summary_path = output / "summary.txt"
    inventory_path = output / "inventory.json"
    checksum_path = output / "checksums.sha256"

    values = (observations.header, *observations.records)
    write_text(
        records_path,
        "".join(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
            for value in values
        ),
    )
    browser_payload = json.dumps(
        {"header": observations.header, "records": observations.records},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).replace("</", "<\\/")
    write_text(
        browser_path,
        f"window.__SKYSEED_EXECUTION_OBSERVATIONS__={browser_payload};\n",
    )
    write_json(summary_data_path, data)
    write_text(page_path, page)
    inventory = data["inventory"]
    write_text(
        summary_path,
        "\n".join(
            (
                "status=PASS",
                "tool=system-observation",
                "authority=non-authoritative-view",
                "representation=detached-jsonl-plus-external-browser-records",
                *(f"{key}={value}" for key, value in inventory.items()),
                "",
            )
        ),
    )
    payload_files = (
        page_path,
        records_path,
        browser_path,
        summary_data_path,
        summary_path,
    )
    bundle_inventory = {
        "format_version": 1,
        "representation": "detached-jsonl-plus-external-browser-records",
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
            f"{inventory['observations']} observations / "
            f"{inventory['components']} components / "
            f"{inventory['stages']} stages"
        ),
        "directory": output,
        "files": tuple(
            path.relative_to(output).as_posix()
            for path in (*checksum_files, checksum_path)
        ),
        "inventory": inventory,
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


__all__ = ["write_observation_artifacts"]
