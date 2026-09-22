"""Write a deterministic Compiler inspection bundle."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from tools.observation._presentation.html import write_json, write_text


def write_bpf_compiler_artifacts(
    data: dict[str, Any], page: str, directory: Path
) -> dict[str, object]:
    """Persist compact exact summaries, HTML, and file accounting."""

    if not isinstance(page, str):
        raise TypeError("page must be rendered text")
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    data_path = output / "bpf-compiler-inspection.json"
    page_path = output / "index.html"
    summary_path = output / "summary.txt"
    inventory_path = output / "inventory.json"
    checksum_path = output / "checksums.sha256"
    write_json(data_path, data)
    write_text(page_path, page)
    inventory = data["inventory"]
    write_text(
        summary_path,
        "\n".join(
            (
                "status=PASS",
                "tool=bpf-compiler-inspection",
                "authority=non-authoritative-view",
                "source=compact-exact-compiler-evidence",
                *(f"{key}={value}" for key, value in inventory.items()),
                "",
            )
        ),
    )
    payloads = (data_path, page_path, summary_path)
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
            f"{inventory['activation_boundaries']} activation boundaries"
        ),
        "directory": output,
        "files": tuple(
            path.relative_to(output).as_posix()
            for path in (*checksum_files, checksum_path)
        ),
        "inventory": dict(inventory),
        "artifact_metrics": {
            "total_bytes": sum(
                path.stat().st_size
                for path in (*checksum_files, checksum_path)
            ),
            "input_bytes": data_path.stat().st_size,
            "output_files": len(checksum_files) + 1,
        },
    }


def _file_record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _digest(path),
    }


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


__all__ = ["write_bpf_compiler_artifacts"]
