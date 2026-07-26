"""Write Topology projection, summary, and rendered UI artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .._html import write_json, write_text
def write_topology_artifacts(
    data: dict[str, Any],
    page: str,
    directory: Path,
) -> dict[str, object]:
    if not isinstance(page, str):
        raise TypeError("page must be rendered text")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "topology.json", data)
    write_text(directory / "index.html", page)
    counts = data["counts"]
    consistency = data["consistency"]
    assert isinstance(counts, dict)
    assert isinstance(consistency, dict)
    write_text(
        directory / "summary.txt",
        "\n".join(
            (
                f"status={data['status']}",
                "topology_created=true",
                f"zone_layout_configured={str(data['zone_layout_configured']).lower()}",
                *(f"{key}={value}" for key, value in counts.items()),
                f"consistency_passed={consistency['passed']}",
                f"consistency_failed={consistency['failed']}",
                (
                    "warning=轨道图显示静态轨道平面，"
                    "不表示任一时刻的卫星位置。"
                ),
                "",
            )
        ),
    )
    return {
        "status": data["status"],
        "message": (
            f"{counts['autonomous_systems']} AS / "
            f"{counts['orbits']} Orbit / {counts['satellites']} Satellite"
        ),
        "counts": counts,
    }


__all__ = ["write_topology_artifacts"]
