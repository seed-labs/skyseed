"""Convert selected Scene nodes into an AS's ordinary SEED inventories."""
from seedemu.core import AutonomousSystem, ExtensionNode, Node
from skyseed.scene import Scene

from .scene_identity import SCENE_ID, SCENE_KIND, seed_name


class SkyAS(AutonomousSystem):
    """Own construction objects; the caller retains ownership of the Scene."""

    def __init__(self, asn: int):
        super().__init__(asn)
        self._scene_nodes: dict[str, Node] = {}
        self._registered = False

    def loadScene(self, scene: Scene, *, node_ids=None, routingBackend="bird") -> None:
        """Add all nodes, or an explicit subset, without changing scene facts.

        Duplicate/unknown identities and name conflicts fail before adding nodes.
        IDs must be unique across ASs of an emulator; that global check runs before
        rendering (ASs can also be assembled separately and merged with SEED).
        """
        if self._registered:
            raise RuntimeError("cannot load Scene after SEED node registration")
        if not isinstance(scene, Scene):
            raise TypeError("scene must be a Scene")
        scene.validate()
        if routingBackend not in {"bird", "frr"}:
            raise ValueError("routingBackend must be bird or frr")
        if isinstance(node_ids, str):
            raise TypeError("node_ids must be a collection of IDs, not a string")
        identities = [node.node_id for node in scene.nodes] if node_ids is None else list(node_ids)
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate requested Scene identities")
        records = [scene.get_node(identity) for identity in identities]
        occupied = set(self.getHosts()) | set(self.getRouters()) | set(self.getExtensionNodes())
        prepared = []
        for record in records:
            name = seed_name(record)
            if record.node_id in self._scene_nodes or name in occupied:
                raise ValueError(f"Scene node or SEED name already exists: {record.node_id}")
            occupied.add(name)
            prepared.append((record, name))
        for record, name in prepared:
            if record.kind == "satellite":
                node = ExtensionNode(name, self.getAsn())
                self.addExtensionNode(node)
            else:
                # The parent's router inventory is also used by iBGP, DNS
                # inheritance and border-router registration. Keep one owner.
                node = self.createRouter(name, routingBackend)
            node.setLabel(SCENE_ID, record.node_id)
            node.setLabel(SCENE_KIND, record.kind)
            self._scene_nodes[record.node_id] = node

    def getNode(self, scene_id: str) -> Node:
        return self._scene_nodes[scene_id]

    def getSceneNodes(self) -> dict[str, Node]:
        """Return a lookup copy containing the same editable SEED objects."""
        return dict(self._scene_nodes)

    def registerNodes(self, emulator):
        base = emulator.getLayer("Base")
        # ExtensionNode deliberately does not inherit generic Base options;
        # Docker's node compiler nevertheless needs this explicit node option.
        options = {option.fullname(): option for option in base.getAvailableOptions()}
        for name in self.getExtensionNodes():
            node = self.getExtensionNode(name)
            if node.getOption("sysctl_netipv4_conf_rp_filter") is None:
                node.setOption(options["sysctl_netipv4_conf_rp_filter"])
        super().registerNodes(emulator)
        self._registered = True
