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
