"""SEED rendering adapter; local zero-interface GS/Router support."""
from seedemu.compiler import Docker
from re import sub

class _InterfaceLessRuntimeDocker(Docker):
    """Compile runtime-only nodes whose links are established by Runner."""

    def __init__(self, *, internet_map_enabled: bool = True) -> None:
        super().__init__(
            namingScheme="as{asn}{role}-{name}",
            internetMapEnabled=internet_map_enabled,
        )

    def _getComposeNodeName(self, node) -> str:
        if node.getInterfaces():
            return super()._getComposeNodeName(node)
        name = (
            f"as{node.getAsn()}"
            f"{self._nodeRoleToString(node.getRole())}-{node.getName()}"
        )
        return sub(r"[^a-zA-Z0-9_.-]", "_", name)

    def _compileNode(self, node) -> str:
        service = super()._compileNode(node)
        if node.getInterfaces() or "        network_mode: none\n" in service:
            return service
        empty_networks = "        networks:\n"
        if empty_networks not in service:
            raise RuntimeError(
                "upstream Docker service shape changed for an "
                "interface-less runtime node"
            )
        return service.replace(
            empty_networks,
            "        network_mode: none\n",
            1,
        )


def _prepare_internet_map(output):
    """Stage the adapted SEED Map source as an ordinary generated build context."""
    from pathlib import Path
    from shutil import copytree, ignore_patterns
    from yaml import safe_load, safe_dump
    import seedemu
    output = Path(output)
    compose_path = output / 'deployment/docker-compose.yml'
    compose = safe_load(compose_path.read_text())
    service = compose['services'].get('seedemu_internet_map')
    if service is None:
        return
    source = Path(seedemu.__file__).resolve().parent.parent / 'tools/InternetMap2'
    if not (source / 'backend/src/utils/live-connections.ts').is_file():
        raise RuntimeError('SkySEED Map requires the adapted SEED checkout with tools/InternetMap2; use --no-internet-map to omit it')
    copytree(source, output / 'deployment/seedemu_internet_map',
             ignore=ignore_patterns('node_modules', 'dist', '.git', '*.tsbuildinfo'))
    handoff = output / '.control/internet-map'
    handoff.mkdir(parents=True, exist_ok=True)
    service['build'] = './seedemu_internet_map'
    service.pop('container_name', None)
    service.setdefault('volumes', []).append({'type': 'bind', 'source': str(handoff),
                                            'target': '/skyseed', 'read_only': True})
    env = service.setdefault('environment', {})
    if isinstance(env, list):
        env.extend(('SKYSEED_LIVE_CONNECTIONS=/skyseed/live.json', 'SKYSEED_COMPOSE_PROJECT=${COMPOSE_PROJECT_NAME}'))
    else:
        env['SKYSEED_LIVE_CONNECTIONS'] = '/skyseed/live.json'
        env['SKYSEED_COMPOSE_PROJECT'] = '${COMPOSE_PROJECT_NAME}'
    compose_path.write_text(safe_dump(compose, sort_keys=False))
