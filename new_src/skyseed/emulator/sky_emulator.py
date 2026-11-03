"""Keep SEED's lifecycle and expose Scene-based construction lookups."""
from seedemu.core import Emulator

from .sky_as import SkyAS
from .scene_identity import scene_nodes


class SkyEmulator(Emulator):
    def _scene_base(self):
        # SEED also allows service-only and empty component descriptions.
        return next((layer for layer in self.getLayers() if layer.getName() == "Base"), None)

    def getNode(self, scene_id):
        base = self._scene_base()
        if base is None:
            raise KeyError(scene_id)
        found = []
        for asn in base.getAsns():
            autonomous_system = base.getAutonomousSystem(asn)
            if isinstance(autonomous_system, SkyAS):
                try:
                    found.append(autonomous_system.getNode(scene_id))
                except KeyError:
                    pass
        if not found:
            raise KeyError(scene_id)
        if len(found) != 1:
            raise ValueError(f"Scene identity appears in multiple ASs: {scene_id}")
        return found[0]

    def getSceneNodes(self):
        base = self._scene_base()
        return {} if base is None else scene_nodes(base)

    def render(self):
        self.getSceneNodes()
        return super().render()

    def dump(self, fileName):
        self.getSceneNodes()
        return super().dump(fileName)

    def compile(self, compiler, output, override=False):
        self.getSceneNodes()
        return super().compile(compiler, output, override)

    def merge(self, other, mergers=None, vnodePrefix=""):
        """Use SEED's explicit merger policies, retaining the Sky query API.

        Like SEED, the resulting description reuses the selected layer and AS
        objects. It is not a deep copy of either input.
        """
        merged = super().merge(other, [] if mergers is None else mergers, vnodePrefix)
        result = SkyEmulator()
        for layer in merged.getLayers():
            result.addLayer(layer)
        for binding in merged.getBindings():
            result.addBinding(binding)
        for hook in merged.getRegistry().getByType("seedemu", "hook"):
            result.addHook(hook)
        for name, node in merged.getVirtualNodes().items():
            result.setVirtualNode(name, node)
        result.getSceneNodes()
        return result
