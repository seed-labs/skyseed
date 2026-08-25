"""Write Zone projection, summary, and rendered UI artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .._html import write_json, write_text
def write_zone_artifacts(
    data: dict[str, Any],
    page: str,
    directory: Path,
) -> dict[str, object]:
    if not isinstance(page, str):
        raise TypeError("page must be rendered text")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "zones.json", data)
    write_text(directory / "index.html", page)
    counts = data["counts"]
    assert isinstance(counts, dict)
    write_text(
        directory / "summary.txt",
        "\n".join(
            (
                f"status={data['status']}",
                (
                    "zone_layout_configured="
                    f"{str(data['zone_layout_configured']).lower()}"
                ),
                f"allow_overlap={data['allow_overlap']}",
                *(f"{key}={value}" for key, value in counts.items()),
                "",
            )
        ),
    )
    return {
        "status": data["status"],
        "message": f"{counts['zones']} Zones / static layout geometry",
        "counts": counts,
    }


__all__ = ["write_zone_artifacts"]
