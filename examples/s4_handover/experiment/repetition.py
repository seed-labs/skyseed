"""Repeat saved physical plans on a forward-only clock, retaining live nodes.

This manual experiment jumps back to its initial topology at each boundary.
It is not orbital propagation beyond the saved interval or a Runner clock reset.
"""
from dataclasses import replace
import json

from skyseed.pc.compiler import BackendInstruction, PhysicalOperation


class RepeatedPlans:
    def __init__(self, plans):
        self.plans = tuple(plans)
        self.start = self.plans[0].start_time
        self.period = max(plan.end_time for plan in self.plans) - self.start
        self.seconds = self.period.total_seconds()
        self.boundaries = self._boundaries()

    def _boundaries(self):
        initial = {"link": {}, "route": {}}
        current = {"link": {}, "route": {}}
        links = {}
        seen = set()
        for plan in self.plans:
            links.update((tuple(item["link_id"]), item) for item in plan.targets["links"])
            operations = plan.operations
            if plan.stream not in seen:
                operations = (*plan.initial_operations, *plan.context_operations, *operations)
                seen.add(plan.stream)
            for operation in sorted(operations, key=lambda item: item.timestamp):
                self._apply(current[plan.stream], plan.stream, operation)
                if operation.timestamp == self.start:
                    self._apply(initial[plan.stream], plan.stream, operation)
        boundaries = {"link": [], "route": []}
        for identity, instructions in current["link"].items():
            if identity not in initial["link"]:
                resource = "link:" + json.dumps(identity, separators=(",", ":"))
                remove = (BackendInstruction("link.unbind", None, {"resource": resource}),
                          BackendInstruction("veth.delete", None, links[identity]))
                boundaries["link"].append(PhysicalOperation(self.start, "withdraw", identity, remove))
        for identity, instructions in initial["link"].items():
            previous = current["link"].get(identity)
            if previous is None:
                kind, changes = "establish", instructions
            else:
                kind = "update"
                changes = tuple(item for item in instructions if item.opcode == "netem.replace"
                                and item not in previous)
            if changes:
                boundaries["link"].append(PhysicalOperation(self.start, kind, identity, changes))
        for key, instruction in current["route"].items():
            if key not in initial["route"]:
                boundaries["route"].append(self._rule_operation(instruction, "withdraw"))
        for key, instruction in initial["route"].items():
            previous = current["route"].get(key)
            desired = dict(instruction.parameters)
            before = {} if previous is None else dict(previous.parameters)
            desired.pop("kind")
            before.pop("kind", None)
            if desired != before:
                kind = "install" if previous is None else "replace"
                boundaries["route"].append(self._rule_operation(instruction, kind))
        return boundaries

    @staticmethod
    def _apply(state, stream, operation):
        if stream == "link":
            if operation.kind == "withdraw":
                state.pop(operation.target, None)
            elif operation.kind == "establish":
                state[operation.target] = operation.instructions
            else:
                updates = {(item.opcode, item.node_id): item for item in operation.instructions}
                state[operation.target] = tuple(updates.get((item.opcode, item.node_id), item)
                                                for item in state[operation.target])
        else:
            for instruction in operation.instructions:
                parameters = instruction.parameters
                key = instruction.node_id, tuple(parameters["match"])
                if parameters["kind"] == "withdraw":
                    state.pop(key, None)
                else:
                    state[key] = instruction

    def _rule_operation(self, instruction, kind):
        instruction = replace(instruction, parameters={**instruction.parameters, "kind": kind})
        return PhysicalOperation(self.start, kind, instruction.node_id, (instruction,))

    def cycle(self, index):
        if index == 0:
            yield from self.plans
            return
        offset = self.period * index
        seen = set()
        for plan in self.plans:
            operations = plan.operations
            if plan.stream not in seen:
                operations = (*self.boundaries[plan.stream],
                              *(item for item in (*plan.context_operations, *operations)
                                if item.timestamp != self.start))
                operations = sorted(operations, key=lambda item: item.timestamp)
                seen.add(plan.stream)
            yield replace(plan, start_time=plan.start_time + offset, end_time=plan.end_time + offset,
                          initial_operations=(), context_operations=(),
                          operations=tuple(replace(item, timestamp=item.timestamp + offset)
                                           for item in operations))

    def iterate(self, *, cycles=None):
        index = 0
        while cycles is None or index < cycles:
            yield from self.cycle(index)
            index += 1
