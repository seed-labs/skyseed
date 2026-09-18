"""Optional OSPF interface explanation from captured settings and recorded links."""


def _prepare_ospf(descriptors, nodes):
    route = (descriptors or {}).get('route', {})
    config = route.get('configuration')
    if (not isinstance(config, dict)
            or config.get('routing_semantics') != 'ospf'
            or not {'interfaces', 'default_area', 'cost_metric'} <= config.keys()
            or config['cost_metric'] not in {'unit', 'delay_us'}):
        return None
    index = {n['node_id']: i for i, n in enumerate(nodes)}
    explicit, configured = {}, {i: set() for i, n in enumerate(nodes) if n['kind'] == 'satellite'}
    def node_id(value):
        return index[f"{value['asn']}/{value['entity_kind']}/{value['entity_id']}"]
    try:
        for item in config['interfaces']:
            connection = item['connection_id']
            a, b = node_id(connection['endpoint_a']), node_id(connection['endpoint_b'])
            area = item['area_id']
            explicit[a, b] = area, item['cost_a_to_b'], item['cost_b_to_a']
            if area is not None:
                for n in (a, b):
                    if n in configured:
                        configured[n].add(area)
    except (KeyError, TypeError):
        # Incomplete descriptors cannot establish a known interface layout.
        return None
    return {
        'explicit': explicit, 'configured': configured,
        'default_area': config['default_area'], 'basis': config['cost_metric'],
    }


def _ospf_metadata(prepared):
    return {
        'metric': 'OSPF selected-path interface cost', 'units': 'cost units',
        'basis': prepared['basis'], 'default_area': prepared['default_area'],
        'configured_interface_count': len(prepared['explicit']),
        'configured_memberships': [[n, sorted(areas)] for n, areas in prepared['configured'].items()],
        'authority': 'captured interface configuration plus recorded operational Connections; no SPF',
    }


def _ospf_frame(prepared, connections):
    """Annotate this recorded graph; never choose a next hop or infer reachability."""
    members = {n: set(areas) for n, areas in prepared['configured'].items()}
    active = {n: set() for n in members}
    links = []
    for kind, a, b, _, ab_delay, _, _, ba_delay, _ in connections:
        item = prepared['explicit'].get((a, b))
        area, ab, ba = item if item is not None else (prepared['default_area'], None, None)
        if kind == 'inter-satellite' and area is not None:
            for n in (a, b):
                members[n].add(area)
                active[n].add(area)
        costs = (1, 1) if prepared['basis'] == 'unit' else (max(1, ab_delay), max(1, ba_delay))
        links.append([a, b, area, ab or costs[0], ba or costs[1], kind, item is not None])
    for areas in members.values():
        if not areas and prepared['default_area'] is not None:
            areas.add(prepared['default_area'])
    for row in links:
        a, b, area, _, _, kind, explicit = row
        if kind == 'ground-station-access':
            satellite = a if a in members else b
            areas = ([] if area is None else [area]) if explicit else sorted(members[satellite])
            row[2] = area if explicit else areas
            active[satellite].update(areas)
    return {
        'links': links,
        'memberships': [[n, sorted(areas), sorted(active[n])] for n, areas in members.items()],
        'abrs': [n for n, areas in members.items() if len(areas) > 1],
    }
