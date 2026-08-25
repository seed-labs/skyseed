"""Attach non-authoritative static-orbit display explanations."""

from __future__ import annotations


def add_topology_display_analysis(data: dict[str, object]) -> None:
    data["display_notes"] = {
        "orbit": "轨道图显示静态轨道平面，不表示任一时刻的卫星位置。",
        "earth_radius": (
            "Tool 私有球面显示使用 IUGG 平均 Earth 半径 6371.0088 km，"
            "把正式 altitude_km 按比例映射为轨道环半径；不属于 "
            "Simulator 轨道传播合同。"
        ),
    }


__all__ = ["add_topology_display_analysis"]
