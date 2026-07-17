"""Docker Compiler extension for first-class, explicitly isolated Satellites."""

from __future__ import annotations

from hashlib import md5
from importlib.resources import files
from os import chdir, getcwd, mkdir
from pathlib import Path
from re import compile as compile_pattern
from re import sub
from shutil import copyfileobj
from typing import Dict, Iterable

from yaml import safe_dump, safe_load

from seedemu.compiler import Docker, Platform
from seedemu.compiler.Docker import DockerCompilerFileTemplates
from seedemu.core import Emulator

from skyseed_v2.domain import (
    GROUND_STATION_REGISTRY_TYPE,
    GROUND_STATION_ROLE_LABEL,
    SATELLITE_REGISTRY_TYPE,
    SATELLITE_ROLE_LABEL,
    GroundStation,
    Satellite,
)


NO_NETWORK_COMPOSE_SERVICE = """\
    {nodeId}:
        build: ./{nodeId}
        container_name: {nodeName}
        depends_on:
            - {dependsOn}
        cap_add:
            - ALL
{sysctls}
        privileged: true
        network_mode: none
{ports}{volumes}
        labels:
{labelList}
        environment:
        {environment}
"""

INTERNET_MAP_SERVICE = """\
    seedemu_internet_map:
        build: ./skyseed-internet-map
        image: skyseed/seedemu-internetmap:2.0-no-network
        container_name: seedemu_internet_map
        volumes:
            - /var/run/docker.sock:/var/run/docker.sock
        privileged: true
"""

INTERNET_MAP_DOCKERFILE = """\
FROM handsonsecurity/seedemu-internetmap:2.0
COPY internet_map_none_network_patch.sh /tmp/internet_map_none_network_patch.sh
RUN sh /tmp/internet_map_none_network_patch.sh \\
    && rm /tmp/internet_map_none_network_patch.sh
"""

SEED_METADATA_PREFIX = "org.seedsecuritylabs.seedemu.meta."
_METADATA_LINE = compile_pattern(
    r'^\s+(?P<key>org\.seedsecuritylabs\.seedemu\.meta\.[^:]+): "(?P<value>.*)"$'
)


def _unique_node_metadata(
    compiler: Docker,
    node: object,
    stable_values: dict[str, str],
) -> str:
    """Replace upstream metadata by key and serialize every label once."""

    upstream_labels = Docker._getNodeMeta(compiler, node)
    labels: dict[str, str] = {}
    for line in upstream_labels.splitlines():
        if not line.strip():
            continue
        match = _METADATA_LINE.fullmatch(line)
        if match is None:
            raise RuntimeError(
                f"unsupported upstream Docker metadata line: {line!r}"
            )
        labels[match.group("key")] = match.group("value")

    for key, value in stable_values.items():
        labels[f"{SEED_METADATA_PREFIX}{key}"] = value

    return "".join(
        DockerCompilerFileTemplates["compose_label_meta"].format(
            key=key.removeprefix(SEED_METADATA_PREFIX),
            value=value,
        )
        for key, value in labels.items()
    )


class _SatelliteCompilerBoundary:
    """All Satellite-specific Docker compilation behavior."""

    def __init__(
        self,
        compiler: "SatelliteDockerCompiler",
        naming_scheme: str,
    ) -> None:
        self._compiler = compiler
        self._naming_scheme = naming_scheme

    def accepts(self, registry_type: str, obj: object) -> bool:
        """Recognize one valid Satellite registry entry or fail on drift."""

        if registry_type == SATELLITE_REGISTRY_TYPE:
            if not isinstance(obj, Satellite):
                raise TypeError(
                    "satnode Registry entries must contain Satellite objects"
                )
            return True
        if isinstance(obj, Satellite):
            raise ValueError(
                "Satellite must be registered with type "
                f"{SATELLITE_REGISTRY_TYPE!r}, not {registry_type!r}"
            )
        return False

    def group_software(self, nodes: Iterable[Satellite]) -> None:
        """Apply upstream-compatible Docker cache tiers to Satellite nodes."""

        software_groups: Dict[str, Dict[str, list[Satellite]]] = {}
        image_nodes: Dict[str, list[Satellite]] = {}
        for node in nodes:
            image, _ = self._compiler._selectImageFor(node)
            image_name = image.getName()
            image_nodes.setdefault(image_name, []).append(node)
            group = software_groups.setdefault(image_name, {})
            for software in node.getSoftware():
                group.setdefault(software, []).append(node)

        for image_name, image_group_nodes in image_nodes.items():
            group = software_groups[image_name]
            for reference_count in range(len(image_group_nodes), 0, -1):
                tier = {
                    software
                    for software, references in group.items()
                    if len(references) == reference_count
                }
                if not tier:
                    continue
                tier_nodes = {
                    node
                    for software in tier
                    for node in group[software]
                }
                for node in tier_nodes:
                    if not node.hasAttribute("__soft_install_tiers"):
                        node.setAttribute("__soft_install_tiers", [])
                    node.getAttribute("__soft_install_tiers").append(tier)

    def compile(self, node: Satellite) -> str:
        """Compile one isolated Satellite through inherited Docker helpers."""

        if node.getInterfaces():
            raise ValueError(
                f"isolated Satellite {node.getName()!r} must have no interfaces"
            )

        real_node_name = self._compiler._getRealNodeName(node)
        mkdir(real_node_name)
        previous_directory = getcwd()
        try:
            chdir(real_node_name)
            image, _ = self._compiler._selectImageFor(node)
            dockerfile = self._compiler._computeDockerfile(node)
            with open("Dockerfile", "w", encoding="utf-8") as dockerfile_output:
                dockerfile_output.write(dockerfile)
        finally:
            chdir(previous_directory)

        name = self._compose_name(node)
        return NO_NETWORK_COMPOSE_SERVICE.format(
            nodeId=real_node_name,
            nodeName=name,
            dependsOn=md5(image.getName().encode("utf-8")).hexdigest(),
            sysctls=self._compiler._getNodeSysctls(node),
            ports=self._compiler._getComposeServicePortList(node),
            labelList=self._metadata(node),
            volumes=self._compiler._getComposeNodeVolumes(node),
            environment=(
                f"    - CONTAINER_NAME={name}\n            "
                + self._compiler._computeNodeEnvironment(node)
            ),
        )

    def _compose_name(self, node: Satellite) -> str:
        name = self._naming_scheme.format(
            asn=node.getAsn(),
            role="sat",
            name=node.getName(),
            displayName=node.getDisplayName() or node.getName(),
            primaryIp="",
        )
        return sub(r"[^a-zA-Z0-9_.-]", "_", name)

    def _metadata(self, node: Satellite) -> str:
        """Reuse upstream metadata, then replace values in one keyed map."""
        stable_values = {
            "asn": str(node.getAsn()),
            "nodename": node.getName(),
            "role": SATELLITE_ROLE_LABEL,
            "type": SATELLITE_REGISTRY_TYPE,
            "skyseed.entity_type": SATELLITE_ROLE_LABEL,
            "skyseed.satellite_id": node.satellite_id,
            "skyseed.shell_id": node.shell_id,
            "skyseed.orbit_id": node.orbit_id,
            "skyseed.runtime_node_ref": node.runtime_reference.value,
        }
        return _unique_node_metadata(
            self._compiler,
            node,
            stable_values,
        )


class SatelliteDockerCompiler(Docker):
    """The formal Docker compiler for Skyseed Satellite emulations."""

    def __init__(
        self,
        *,
        platform: Platform = Platform.AMD64,
        naming_scheme: str = "as{asn}{role}-{displayName}",
        internet_map_enabled: bool = True,
        internet_map_port: int = 8080,
    ) -> None:
        self._skyseed_internet_map_enabled = internet_map_enabled
        self._skyseed_internet_map_port = internet_map_port
        super().__init__(
            platform=platform,
            namingScheme=naming_scheme,
            internetMapEnabled=False,
            internetMapPort=internet_map_port,
        )
        self._satellite_compiler = _SatelliteCompilerBoundary(
            self, naming_scheme
        )

    def getName(self) -> str:
        return "SkyseedDocker"

    def _getNodeMeta(self, node) -> str:
        """Extend standard rnode metadata only for a GroundStation instance."""

        if not isinstance(node, GroundStation):
            return super()._getNodeMeta(node)
        location = node.location
        stable_values = {
            "asn": str(node.getAsn()),
            "nodename": node.getName(),
            "role": GROUND_STATION_ROLE_LABEL,
            "type": GROUND_STATION_REGISTRY_TYPE,
            "skyseed.entity_type": GROUND_STATION_ROLE_LABEL,
            "skyseed.ground_station_id": node.ground_station_id,
            "skyseed.latitude_deg": str(location.latitude_deg),
            "skyseed.longitude_deg": str(location.longitude_deg),
            "skyseed.station_type": node.station_type,
            "skyseed.operator": node.operator,
            "skyseed.runtime_node_ref": node.runtime_reference.value,
        }
        if location.elevation_m is not None:
            stable_values["skyseed.elevation_m"] = str(location.elevation_m)
        return _unique_node_metadata(self, node, stable_values)

    def _doCompile(self, emulator: Emulator) -> None:
        if self._skyseed_internet_map_enabled:
            self._attach_skyseed_internet_map()

        satellite_entries: list[tuple[str, str, Satellite]] = []
        for (scope, registry_type, name), obj in emulator.getRegistry().getAll().items():
            if not self._satellite_compiler.accepts(registry_type, obj):
                continue
            satellite_entries.append((scope, name, obj))

        self._satellite_compiler.group_software(
            node for _, _, node in satellite_entries
        )
        for scope, name, node in satellite_entries:
            self._log(f"compiling Satellite node {name} for as{scope}...")
            service = self._satellite_compiler.compile(node)
            self._append_upstream_service(service)
        super()._doCompile(emulator)
        self._remove_empty_top_level_networks()

    def _append_upstream_service(self, service: str) -> None:
        """The sole fail-closed access to SEED Docker's private accumulator."""

        attribute = "_Docker__services"
        if not hasattr(self, attribute):
            raise RuntimeError(
                "SEED Docker private service accumulator is unavailable; "
                "custom-node dispatch cannot continue"
            )
        services = getattr(self, attribute)
        if not isinstance(services, str):
            raise TypeError(
                "SEED Docker private service accumulator must be a string, "
                f"got {type(services).__name__}"
            )
        if not isinstance(service, str):
            raise TypeError("compiled Docker service must be a string")
        setattr(self, attribute, services + service)

    def _attach_skyseed_internet_map(self) -> None:
        """Build the pinned InternetMap with generic ``network_mode:none`` normalization."""

        context = Path("skyseed-internet-map")
        context.mkdir()
        (context / "Dockerfile").write_text(
            INTERNET_MAP_DOCKERFILE, encoding="utf-8"
        )
        patch_resource = files("skyseed_v2.seed").joinpath(
            "assets/internet_map_none_network_patch.sh"
        )
        with patch_resource.open("rb") as patch_input:
            with (context / "internet_map_none_network_patch.sh").open(
                "wb"
            ) as patch_output:
                copyfileobj(patch_input, patch_output)
        self.attachCustomContainer(
            INTERNET_MAP_SERVICE,
            asn=-1,
            port_forwarding=f"{self._skyseed_internet_map_port}:8080/tcp",
        )

    def _remove_empty_top_level_networks(self) -> None:
        """Remove upstream's invalid ``networks: null`` without inventing a net."""

        compose_path = "docker-compose.yml"
        with open(compose_path, encoding="utf-8") as compose_input:
            compose = safe_load(compose_input)
        if compose.get("networks") is not None:
            return
        compose.pop("networks", None)
        with open(compose_path, "w", encoding="utf-8") as compose_output:
            safe_dump(compose, compose_output, sort_keys=False)
