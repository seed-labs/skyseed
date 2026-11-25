"""Lower independent logical plan streams without touching runtime resources."""
from heapq import merge
import json

from ..link import LinkPlan
from ..network import RoutePlan
from .deployment_targets import DeploymentTargets
from .physical_plan import BackendInstruction, PhysicalOperation, PhysicalPlan
from .bpf.label_tables import ABI, encode_rule, pipeline


def _link_resource(identity):
    return "link:" + json.dumps(identity, ensure_ascii=False, separators=(",", ":"))


class PlanCompiler:
    def __init__(self, deployment_dir, *, forwarding_backend="bpf",
                 node_address_pool="198.18.0.0/16", endpoint_address_pool="198.19.0.0/16",
                 link_address_pool="100.64.0.0/10"):
        if forwarding_backend not in {"bpf", "linux"}:
            raise ValueError("forwarding_backend must be 'bpf' or 'linux'")
        self.forwarding_backend = forwarding_backend
        self._targets = DeploymentTargets(
            deployment_dir, node_address_pool=node_address_pool,
            endpoint_address_pool=endpoint_address_pool, link_address_pool=link_address_pool)

    @property
    def deployment_id(self):
        return self._targets.deployment_id

    def compile_plans(self, *, link_plans=(), route_plans=()):
        """Yield one physical batch per source batch, ordered by start time.

        Each finite input must ascend by start time. Different intervals, counts
        and overlapping Delivery tails are retained. Reuse this compiler for
        later requests so address and logical-port assignments remain stable.
        """
        def checked(plans, expected):
            previous = None
            for plan in plans:
                if not isinstance(plan, expected):
                    raise TypeError(f"expected {expected.__name__}, got {type(plan).__name__}")
                if previous is not None and plan.start_time < previous:
                    raise ValueError("input plan starts must ascend within each stream")
                previous = plan.start_time
                yield plan

        plans = merge(checked(link_plans, LinkPlan), checked(route_plans, RoutePlan),
                      key=lambda plan: plan.start_time)
        for plan in plans:
            yield self._compile(plan)

    def _compile(self, plan):
        nodes, links, destinations = {}, {}, {}
        match_owners = {}

        def use_node(identity):
            nodes[identity] = self._targets.node(identity)

        def use_link(identity):
            target = self._targets.link(identity)
            links[target["link_id"]] = target
            for node_id in target["link_id"]:
                use_node(node_id)
            return target

        def compile_link(timestamp, kind, identity, configuration):
            target = use_link(identity)
            resource = _link_resource(target["link_id"])
            instructions = []
            if kind == "withdraw":
                # Unbind/drop before deleting veth; rules are retained by Runner.
                instructions.append(BackendInstruction("link.unbind", None, {"resource": resource}))
                instructions.append(BackendInstruction("veth.delete", None, target))
            else:
                if kind == "establish":
                    instructions.append(BackendInstruction("veth.create", None, target))
                    for side, endpoint in enumerate(target["endpoints"]):
                        peer = target["endpoints"][1 - side]
                        parameters = {**endpoint, "mtu": 1504 if self.forwarding_backend == "bpf" else 1500,
                                      "peer_address": peer["address"].split("/")[0],
                                      "peer_mac": peer["mac"]}
                        instructions.append(BackendInstruction("interface.configure", endpoint["node_id"],
                                                               parameters, (resource,)))
                for endpoint in target["endpoints"]:
                    properties = configuration.properties_from(endpoint["node_id"])
                    drop = properties is None or properties.bandwidth_bps == 0
                    parameters = {"interface": endpoint["interface"], "drop": drop,
                                  "delay_us": 0 if properties is None else round(properties.delay_s * 1_000_000),
                                  "rate_bps": None if drop else max(1, round(properties.bandwidth_bps)),
                                  "loss_percent": 100.0 if drop else properties.packet_loss_ppm / 10_000}
                    instructions.append(BackendInstruction("netem.replace", endpoint["node_id"],
                                                           parameters, (resource,)))
                if kind == "establish":
                    instructions.append(BackendInstruction("link.activate", None, target, (resource,)))
            return PhysicalOperation(timestamp, kind, tuple(identity), tuple(instructions))

        def compile_route(timestamp, kind, node_id, rule):
            use_node(node_id)
            prefix = self._targets.destination(rule.destination)
            destinations[(rule.destination.kind, rule.destination.identity)] = prefix
            resource, endpoint, peer = None, None, None
            if rule.exit is not None:
                target = use_link(rule.exit.link_id)
                side = target["link_id"].index(node_id)
                endpoint, peer = target["endpoints"][side], target["endpoints"][1 - side]
                resource = _link_resource(rule.exit.link_id)
            parameters = {"destination": (rule.destination.kind, rule.destination.identity),
                          "prefix": prefix, "action": rule.action, "kind": kind,
                          "resource": resource, "unavailable": "drop", "data": rule.data}
            match = ("label", rule.incoming_label) if rule.incoming_label is not None else ("prefix", prefix)
            owner_key = (node_id, match)
            if kind == "withdraw":
                match_owners.pop(owner_key, None)
            else:
                previous = match_owners.get(owner_key)
                if previous is not None and previous != rule.key:
                    raise ValueError(f"different logical rules compile to the same match at {node_id}: {match}")
                match_owners[owner_key] = rule.key
            if self.forwarding_backend == "bpf":
                port = 0 if endpoint is None else endpoint["port"]
                key, value = encode_rule(match, rule.action, port, rule.outgoing_label or 0)
                parameters.update({"match": match, "port": port, "outgoing_label": rule.outgoing_label,
                                   "key_hex_le": key.hex(), "value_hex_le": value.hex(), "table_abi": ABI})
                opcode = "bpf.rule"
            else:
                if rule.action not in {"forward", "deliver"}:
                    raise ValueError("Linux compiler requires IP rules; use label_forwarding=False in GZ routing")
                parameters.update({"interface": None if endpoint is None else endpoint["interface"],
                                   "gateway": None if peer is None else peer["address"].split("/")[0],
                                   "table": 254, "protocol": 253, "priority": 1,
                                   "unavailable": "blackhole"})
                opcode = "linux.route"
            # A logical exit is a conditional dependency, not a reason to delay
            # this operation. Missing resources activate the specified drop path.
            instruction = BackendInstruction(opcode, node_id, parameters)
            return PhysicalOperation(timestamp, kind, node_id, (instruction,))

        if isinstance(plan, LinkPlan):
            stream = "link"
            initial = tuple(compile_link(plan.start_time, "establish", identity, configuration)
                            for identity, configuration in plan.initial_links.items())
            operations = tuple(compile_link(op.timestamp, op.kind, op.link_id, op.configuration)
                               for op in plan.operations)
            context = ()
        else:
            stream = "route"
            initial = tuple(compile_route(plan.start_time, "install", node_id, rule)
                            for node_id, rules in plan.initial_rules.items() for rule in rules)
            operations, context = [], []
            ordered = merge(((op, True) for op in plan.context_operations),
                            ((op, False) for op in plan.operations), key=lambda item: item[0].timestamp)
            for op, is_context in ordered:
                compiled = compile_route(op.timestamp, op.kind, op.node_id, op.rule)
                target_sequence = context if is_context else operations
                target_sequence.append(compiled)
            operations, context = tuple(operations), tuple(context)
        targets = {"nodes": tuple(nodes.values()), "links": tuple(links.values()),
                   "address_pools": tuple(map(str, self._targets.pools[:2])),
                   "destinations": tuple({"kind": kind, "identity": identity, "prefix": prefix}
                                         for (kind, identity), prefix in destinations.items())}
        if self.forwarding_backend == "bpf":
            targets["bpf_program"] = {"abi": ABI, "source": "skyseed.pc.compiler.bpf/label_forwarding.bpf.c",
                                      "publication": "shared-generation",
                                      "shared_maps": ("active_generation", "generation_readers"),
                                      "pipeline": pipeline(), "local_ingress": "dedicated-veth",
                                      "wire": "ethernet-mpls-single-label-ipv4", "overhead_bytes": 4}
        return PhysicalPlan(plan.start_time, plan.end_time, stream, self.forwarding_backend,
                            self.deployment_id, targets, initial, operations, context)
