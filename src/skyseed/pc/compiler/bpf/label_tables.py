"""The label-first LPM table ABI shared by compiler and TC program.

One immutable inner table contains exact local labels and IPv4 ingress prefixes.
Runner publishes one shared generation selecting each node's prepared version.
A reader reference protects generation data until the program copies its rule.
"""
import ipaddress
import struct


ABI = "skyseed.label.v1"
ACTIONS = {"push": 1, "swap": 2, "pop_deliver": 3, "forward": 4, "deliver": 5}


def encode_rule(match, action, port=0, outgoing_label=0, *, byteorder="little"):
    """Return native-endian map scalars and network-endian match bytes."""
    endian = {"little": "<", "big": ">"}[byteorder]
    if match[0] == "label":
        prefix_length, kind, value = 64, 0, int(match[1])
    else:
        prefix = ipaddress.IPv4Network(match[1])
        prefix_length, kind, value = 32 + prefix.prefixlen, 1, int(prefix.network_address)
    key = struct.pack(endian + "I", prefix_length) + struct.pack("!II", kind, value)
    value = struct.pack(endian + "III", ACTIONS[action], port, outgoing_label)
    return key, value


def pipeline():
    """Ordered TC ingress filters; all programs share the node's maps."""
    return (
        {"program": "decide", "protocol": "all", "direct_action": True, "priority": 10},
        {"program": "needs_push", "protocol": "all", "direct_action": False, "priority": 20,
         "actions": ({"mpls": "push", "protocol": "mpls_uc", "label": 16,
                      "bos": 1, "ttl": 64}, {"bpf_program": "complete_push"})},
        {"program": "needs_pop", "protocol": "all", "direct_action": False, "priority": 30,
         "actions": ({"mpls": "pop", "protocol": "ip"},
                     {"bpf_program": "complete_pop"})},
    )
