"""Explain the built-in instruction ABI for the optional offline inspector.

Command equivalents are reading aids, never executable Runner requests.
Unknown opcodes stay inspectable without inventing an interpretation for them.
"""
import json
import shlex


def _command(*arguments):
    return shlex.join(str(argument) for argument in arguments)


def _scope(nodes, node_id):
    node = nodes[node_id]
    return {"node": node_id, "service": node["service"]}


def _endpoint(nodes, endpoint):
    return {**_scope(nodes, endpoint["node_id"]),
            "interface": endpoint["interface"], "address": endpoint["address"],
            "port": endpoint["port"], "mac": endpoint["mac"]}


def _step(instruction, nodes, backend):
    opcode = instruction["opcode"]
    p = instruction["parameters"]
    node_id = instruction["node_id"]
    result = {"opcode": opcode, "node": node_id, "title": opcode,
              "notes": [], "commands": [], "requires": instruction["requires"]}

    def command(node, text):
        result["commands"].append({"node": node, "text": text})

    if opcode == "veth.create":
        left, right = p["endpoints"]
        result["title"] = "创建接口对并放入节点 / Create & place veth"
        command(None, _command("ip", "link", "add", left["interface"], "type", "veth",
                               "peer", "name", right["interface"]))
        for endpoint in p["endpoints"]:
            target = nodes[endpoint["node_id"]]
            result["notes"].append(f'{endpoint["interface"]} → {endpoint["node_id"]} '
                                   f'的网络命名空间 / namespace · {target["service"]}')
    elif opcode == "interface.configure":
        result["title"] = "配置地址与对端邻居 / Configure interface"
        interface = p["interface"]
        command(node_id, _command("ip", "link", "set", "dev", interface,
                                  "address", p["mac"], "mtu", p["mtu"]))
        command(node_id, _command("ip", "addr", "replace", p["address"], "dev", interface))
        command(node_id, _command("ip", "neigh", "replace", p["peer_address"],
                                  "lladdr", p["peer_mac"], "nud", "permanent", "dev", interface))
    elif opcode == "netem.replace":
        result["title"] = "设置发送方向的链路属性 / Set egress properties"
        arguments = ["tc", "qdisc", "replace", "dev", p["interface"], "root", "netem",
                     "delay", f'{p["delay_us"]}us']
        if p["rate_bps"] is not None:
            arguments.extend(("rate", f'{p["rate_bps"]}bit'))
        arguments.extend(("loss", f'{p["loss_percent"]:g}%'))
        command(node_id, _command(*arguments))
        if p["drop"]:
            result["notes"].append("该方向配置为全部丢弃 / This direction drops all packets.")
    elif opcode == "link.activate":
        result["title"] = "启用两端并接入后端 / Activate link"
        for endpoint in p["endpoints"]:
            command(endpoint["node_id"], _command("ip", "link", "set", "dev",
                                                  endpoint["interface"], "up"))
        if backend == "bpf":
            result["notes"].append("接入 TC 标签程序；逻辑端口绑定本次运行的接口编号与 MAC。 / Attach TC; bind logical ports to live interfaces.")
        else:
            result["notes"].append("重新落实依赖此链路且仍有效的路由意图。 / Restore still-current route intents using this link.")
    elif opcode == "link.unbind":
        result["title"] = "先解除出口，再删除接口 / Unbind before deletion"
        if backend == "bpf":
            result["notes"].append("移除两端逻辑端口绑定；仍指向这些端口的规则将丢包。 / Unbound ports drop; route intent remains.")
        else:
            result["notes"].append("保留路由意图，将依赖此链路的受管路由改为 blackhole。 / Retain intents; dependent routes become blackholes.")
    elif opcode == "veth.delete":
        result["title"] = "删除接口对 / Delete veth pair"
        endpoint = p["endpoints"][0]
        command(endpoint["node_id"], _command("ip", "link", "delete", "dev", endpoint["interface"]))
        result["notes"].append("删除一端会同时移除对端；不撤销独立路由意图。 / Deleting one end removes its peer; route intents remain.")
    elif opcode == "bpf.rule":
        withdrawing = p["kind"] == "withdraw"
        result["title"] = ("从待生效表移除匹配项 / Remove table entry" if withdrawing else
                           "写入待生效的标签／入口表 / Prepare forwarding entry")
        result["notes"].append("到计划时刻由共享 generation 选择该节点版本；未变化节点复用原表。 / Shared generation selects the prepared node version.")
        result["notes"].append("图中展示此表项的匹配与动作；编码字节见原始记录。 / Match and action are shown above; encoded bytes remain in the raw record.")
    elif opcode == "linux.route":
        withdrawing = p["kind"] == "withdraw"
        verb = "del" if withdrawing else "replace"
        result["title"] = ("撤销受管路由及意图 / Withdraw managed route" if withdrawing else
                           "安装内核路由 / Install kernel route")
        if p["action"] == "deliver":
            command(node_id, _command("ip", "route", verb, "local", p["prefix"], "dev", "lo", "table", "local"))
        else:
            common = ["table", p["table"], "proto", p["protocol"], "metric", p["priority"]]
            command(node_id, _command("ip", "route", verb, p["prefix"], "via", p["gateway"],
                                      "dev", p["interface"], *common))
            if not withdrawing:
                result["commands"][-1]["condition"] = "出口链路可用时 / If exit is available"
                result["commands"].append({"node": node_id, "condition": "出口链路缺失时 / If exit is absent",
                                           "text": _command("ip", "route", "replace", "blackhole", p["prefix"], *common)})
            else:
                result["notes"].append("若当前安装的是 blackhole，则删除该丢弃项；同时删除保留意图。 / Remove the active or blackhole entry and retained intent.")
    else:
        result["notes"].append("此工具没有该指令的解释器，请查看原始记录。 / No display adapter for this opcode; inspect the raw record.")
    return result


def operation_views(plan):
    """Build views using only this physical batch's recorded targets and steps."""
    nodes = {node["node_id"]: node for node in plan["targets"]["nodes"]}
    links = {tuple(link["link_id"]): link for link in plan["targets"]["links"]}
    result = {}
    for group in ("initial_operations", "operations", "context_operations"):
        views = []
        for operation in plan[group]:
            steps = [_step(item, nodes, plan["forwarding_backend"]) for item in operation["instructions"]]
            view = {"steps": steps, "diagram": None}
            if plan["stream"] == "link" and tuple(operation["target"]) in links:
                link = links[tuple(operation["target"])]
                directions = []
                for instruction in operation["instructions"]:
                    if instruction["opcode"] == "netem.replace":
                        p = instruction["parameters"]
                        directions.append({"node": instruction["node_id"], "delay_us": p["delay_us"],
                                           "rate_bps": p["rate_bps"], "loss_percent": p["loss_percent"],
                                           "drop": p["drop"]})
                view["diagram"] = {"type": "link", "endpoints": [_endpoint(nodes, e) for e in link["endpoints"]],
                                   "directions": directions}
            elif plan["stream"] == "route":
                route = next((item for item in operation["instructions"]
                              if item["opcode"] in {"bpf.rule", "linux.route"}), None)
                if route is not None:
                    p = route["parameters"]
                    match = p.get("match", ["prefix", p["prefix"]])
                    diagram = {"type": "route", "node": _scope(nodes, route["node_id"]),
                               "destination": p["destination"], "prefix": p["prefix"],
                               "match": match, "action": p["action"], "outgoing_label": p.get("outgoing_label"),
                               "port": p.get("port"), "unavailable": p["unavailable"], "exit": None}
                    if p["resource"] is not None:
                        link_id = tuple(json.loads(p["resource"].removeprefix("link:")))
                        link = links[link_id]
                        local = next(e for e in link["endpoints"] if e["node_id"] == route["node_id"])
                        peer = next(e for e in link["endpoints"] if e["node_id"] != route["node_id"])
                        diagram["exit"] = {"local": _endpoint(nodes, local), "peer": _endpoint(nodes, peer)}
                    view["diagram"] = diagram
            views.append(view)
        result[group] = views
    return result
