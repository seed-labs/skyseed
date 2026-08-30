"""Write one offline Physical Frame inspection bundle."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .._html import write_json, write_text


def write_physical_frame_artifacts(
    data: dict[str, Any],
    page: str,
    directory: Path,
) -> dict[str, object]:
    if not isinstance(page, str):
        raise TypeError("page must be rendered text")
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "physical-frame.json", data)
    write_text(output / "index.html", page)
    inventory = data["inventory"]
    assert isinstance(inventory, dict)
    write_text(
        output / "summary.txt",
        "\n".join(
            (
                "status=PASS",
                "tool=physical-frame",
                "authority=non-authoritative-view",
                f"timestamp={data['timestamp']}",
                *(f"{key}={value}" for key, value in inventory.items()),
                "",
            )
        ),
    )
    return {
        "status": "PASS",
        "message": (
            f"{data['timestamp']} / {inventory['positions']} Positions"
        ),
        "timestamp": data["timestamp"],
        "counts": dict(inventory),
    }


__all__ = ["write_physical_frame_artifacts"]
