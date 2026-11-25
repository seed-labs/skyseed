"""Scene identity metadata shared by construction and deployment consumers."""
import hashlib
import re
from seedemu.core import ExtensionNode

SCENE_ID = "skyseed.scene_id"
SCENE_KIND = "skyseed.scene_kind"


def seed_name(node):
    """Stable Docker-safe name; neither the Scene ID nor its scope includes ASN."""
    display = re.sub("[^a-z0-9-]", "-", node.name.lower()).strip("-")[:24] or "node"
    suffix = hashlib.sha256(node.node_id.encode("utf-8")).hexdigest()[:20]
    return f"{display}-{suffix}"


def scene_nodes(base):
    """Collect one globally unambiguous identity map from the AS owners."""
    from .sky_as import SkyAS
    result = {}
    for asn in base.getAsns():
        autonomous_system = base.getAutonomousSystem(asn)
        if isinstance(autonomous_system, SkyAS):
            for identity, node in autonomous_system.getSceneNodes().items():
                if identity in result:
                    raise ValueError(f"Scene identity appears in multiple ASs: {identity}")
                if node.getLabel().get(SCENE_ID) != identity:
                    raise ValueError(f"reserved Scene identity label was changed: {identity}")
                kind = "satellite" if isinstance(node, ExtensionNode) else "ground_station"
                if node.getLabel().get(SCENE_KIND) != kind:
                    raise ValueError(f"reserved Scene kind label was changed: {identity}")
                result[identity] = node
    return result
