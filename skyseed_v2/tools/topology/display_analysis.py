"""Attach non-authoritative static-orbit display explanations."""

from __future__ import annotations


def add_topology_display_analysis(data: dict[str, object]) -> None:
    data["display_notes"] = {
        "orbit": "轨道图显示静态轨道平面，不表示任一时刻的卫星位置。",
        "earth_radius": (
            "6371 km 只用于把 altitude_km 映射为相对轨道环半径，"
            "不属于 Simulator 轨道传播合同。"
        ),
    }


__all__ = ["add_topology_display_analysis"]
