"""Fixed logical attachment selection for one material chain."""
from dataclasses import dataclass
from skyseed.shared import NodeId
from .program import BpfProgram


@dataclass(frozen=True)
class AttachmentScope:
    """Whole nodes sharing one forwarding program/profile; empty covers nothing."""
    nodes: tuple[NodeId, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'nodes', tuple(self.nodes))
        if any(not isinstance(n, NodeId) for n in self.nodes):
            raise TypeError('scopes require logical nodes')
        if len(set(self.nodes)) != len(self.nodes):
            raise ValueError('duplicate attachment scope member')


@dataclass(frozen=True)
class DataPlaneBinding:
    program: BpfProgram
    scope: AttachmentScope

    def __post_init__(self):
        if not isinstance(self.program, BpfProgram) or not isinstance(self.scope, AttachmentScope):
            raise TypeError('binding requires BpfProgram and AttachmentScope')


@dataclass(frozen=True)
class DataPlane:
    """Explicit bindings, with no fallback on uncovered nodes (including empty)."""
    bindings: tuple[DataPlaneBinding, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'bindings', tuple(self.bindings))
        if any(not isinstance(v, DataPlaneBinding) for v in self.bindings):
            raise TypeError('DataPlane bindings must contain DataPlaneBinding values')
