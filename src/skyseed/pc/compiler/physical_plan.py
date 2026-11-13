"""Portable backend instructions; all live bindings belong to Runner."""
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from ..physical.physical_results import utc_time


@dataclass(frozen=True, slots=True)
class BackendInstruction:
    opcode: str
    node_id: str | None
    parameters: Mapping
    requires: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        object.__setattr__(self, "requires", tuple(self.requires))


@dataclass(frozen=True, slots=True)
class PhysicalOperation:
    timestamp: datetime
    kind: str
    target: str | tuple[str, str]
    instructions: tuple[BackendInstruction, ...]

    def __post_init__(self):
        object.__setattr__(self, "timestamp", utc_time(self.timestamp))
        object.__setattr__(self, "instructions", tuple(self.instructions))


@dataclass(frozen=True, slots=True)
class PhysicalPlan:
    """One compiled source batch, keeping the two input streams independent.

    Initial instructions restore this stream when starting here. They are not
    replayed between already-running batches. Context is for restoration/query,
    never a second delivery of the same operation.
    """
    start_time: datetime
    end_time: datetime
    stream: str
    forwarding_backend: str
    deployment_id: str
    targets: Mapping
    initial_operations: tuple[PhysicalOperation, ...] = ()
    operations: tuple[PhysicalOperation, ...] = ()
    context_operations: tuple[PhysicalOperation, ...] = ()
    backend_abi: str = "skyseed.physical.v1"

    def __post_init__(self):
        start, end = utc_time(self.start_time), utc_time(self.end_time)
        if start >= end:
            raise ValueError("PhysicalPlan requires start_time < end_time")
        if self.stream not in {"link", "route"}:
            raise ValueError("unknown physical plan stream")
        if not self.forwarding_backend or not self.backend_abi:
            raise ValueError("physical plans require a backend name and ABI")
        object.__setattr__(self, "start_time", start)
        object.__setattr__(self, "end_time", end)
        object.__setattr__(self, "targets", MappingProxyType(dict(self.targets)))
        for name in ("initial_operations", "operations", "context_operations"):
            sequence = tuple(getattr(self, name))
            previous = start
            for operation in sequence:
                if not previous <= operation.timestamp < end:
                    raise ValueError(f"{name} must be ordered inside the physical plan interval")
                previous = operation.timestamp
            object.__setattr__(self, name, sequence)
