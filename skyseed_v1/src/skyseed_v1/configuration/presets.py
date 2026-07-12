"""Reusable constellation and ground-network components."""

from __future__ import annotations

from .models import GroundConfig, ShellConfig


SHELLS = {
    "S4": ShellConfig("S4", 6, 58, 190),
    "S5": ShellConfig("S5", 4, 43, 399),
}


def planned_shell(shell_id: str) -> ShellConfig:
    try:
        return SHELLS[str(shell_id)]
    except KeyError as exc:
        raise ValueError(f"unsupported canonical shell: {shell_id}") from exc


def s4_rich_ground_network() -> GroundConfig:
    return GroundConfig(
        name="rich_28ut_12gw_14zone",
        ut_catalog="data/ground/s4_gz_rich_validation_ut_catalog_32.json",
        ut_gateway_policy_catalog="data/ground/s4_gz_rich_validation_ut_gateway_policy_32.json",
        gateway_catalog="data/ground/starlink_gateways.json",
        gateway_binding_catalog="data/ground/s4_visualization_gateway_binding_12.json",
        zone_catalog="data/ground/s4_gz_rich_validation_zone_catalog_16.json",
        ut_ids=(
            "ut2001", "ut2002", "ut2003", "ut2004", "ut2005", "ut2006",
            "ut2007", "ut2008", "ut2009", "ut2010", "ut2011", "ut2012",
            "ut2013", "ut2014", "ut2017", "ut2018", "ut2019", "ut2020",
            "ut2021", "ut2022", "ut2023", "ut2024", "ut2025", "ut2026",
            "ut2029", "ut2030", "ut2031", "ut2032",
        ),
        gateway_ids=(
            "gw201", "gw202", "gw203", "gw204", "gw205", "gw206",
            "gw207", "gw208", "gw209", "gw210", "gw211", "gw212",
        ),
        zone_ids=(
            "zone-north-america-west-wide",
            "zone-north-america-east-wide",
            "zone-europe-west-wide",
            "zone-middle-east-south-asia-wide",
            "zone-east-asia-pacific-wide",
            "zone-south-america-wide",
            "zone-africa-west-optional",
            "zone-australia-east-optional",
            "zone-central-asia-optional",
            "zone-arctic-atlantic-observation",
            "zone-north-atlantic-observation",
            "zone-north-pacific-observation",
            "zone-indian-ocean-observation",
            "zone-southern-ocean-observation",
        ),
        qualification_start_time_utc="2026-07-09T00:00:00Z",
        qualification_duration_seconds=960,
    )


def s5_operational_ground_network() -> GroundConfig:
    return GroundConfig(
        name="s5_2h_qualified_9ut_5gw_6zone",
        ut_catalog="data/ground/s5_operational_clean_ut_catalog_9.json",
        ut_gateway_policy_catalog=(
            "data/ground/s5_operational_clean_ut_gateway_policy_9.json"
        ),
        gateway_catalog="data/ground/starlink_gateways.json",
        gateway_binding_catalog=(
            "data/ground/s5_operational_clean_gateway_binding_5.json"
        ),
        zone_catalog="data/ground/s5_operational_clean_zone_catalog_6.json",
        ut_ids=(
            "ut2002", "ut2008", "ut2017", "ut2018", "ut2020",
            "ut2021", "ut2022", "ut2023", "ut2024",
        ),
        gateway_ids=("gw201", "gw202", "gw204", "gw206", "gw211"),
        zone_ids=(
            "zone-north-america-west-wide",
            "zone-middle-east-south-asia-wide",
            "zone-australia-east-optional",
            "zone-central-asia-optional",
            "zone-arctic-atlantic-observation",
            "zone-north-atlantic-observation",
        ),
        qualification_start_time_utc="2026-07-09T00:00:00Z",
        qualification_duration_seconds=7200,
    )
