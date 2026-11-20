// S4 data/interaction adapter; SEED owns the graph, panels, log and terminals.
import {capture} from './capture.mjs';

export async function start(ui, source, config) {
    let state = null, selected = null, fitted = false, stopped = false, timer;
    let edgeStyles = new Map(), byContainer = new Map(), relatedZones = new Map(), orbitLabels = [];
    const flashes = new Map(), watched = new Map();
    const storageKey = 'skyseed.s4.watchedZones';
    const zoneId = id => `zone:${id}`;
    const controls = document.createElement('div');
    controls.style.cssText = 'padding:6px 14px;font-size:13px';
    const status = document.createElement('div');
    status.style.whiteSpace = 'pre-line';
    status.innerText = 'Connecting to network…';
    const allLinks = document.createElement('input');
    allLinks.type = 'checkbox';
    const label = document.createElement('label');
    label.append(allLinks, ' 显示全部连接 / Show all links');
    controls.append(status, label);
    document.querySelector('.tabs-panel').appendChild(controls);

    function pair(data, label, value) {
        if (value !== undefined && value !== null && value !== '') data[label] = String(value);
    }

    function link(text, attributes, className = 'skyseed-action') {
        const element = document.createElement('a');
        element.href = '#';
        element.className = className;
        element.innerText = text;
        for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
        return element.outerHTML;
    }

    function actions(nodes) {
        return {title: 'Actions', data: Object.fromEntries(nodes.map(node => [node.container,
            link(`Launch console · ${node.name}`, {
                params: JSON.stringify({nodeId: node.container, label: node.name})
            }, 'console-action')]))};
    }

    function command(text) {
        return [text + ' ', link('复制 / Copy', {'data-skyseed-copy': text})];
    }

    function zonePicker(ids) {
        const select = document.createElement('select');
        select.setAttribute('data-skyseed-zones', '');
        select.setAttribute('aria-label', '选择关注的 Zone / Choose Zone');
        select.style.cssText = 'width:100%;padding:6px';
        const option = document.createElement('option');
        option.value = '';
        option.innerText = `选择关注的 Zone / Choose Zone (${ids.length})`;
        select.appendChild(option);
        for (const id of ids) {
            const zone = state.zones[id];
            const entry = document.createElement('option');
            entry.value = id;
            entry.innerText = `${watched.has(id) ? '★ ' : ''}${id} · ${zone.prefix || '未分配 IP'} · ` +
                (zone.serving.length ? zone.serving.map(id => state.nodes[id].name).join(', ') : '当前无服务星 / No service');
            select.appendChild(entry);
        }
        return {title: 'Actions', data: {zones: select.outerHTML}};
    }

    function details() {
        if (!state || !selected) return [];
        const result = [], data = {};
        const zone = selected.startsWith('zone:') && state.zones[selected.slice(5)];
        if (zone) {
            const id = selected.slice(5), address = zone.prefix.split('/')[0];
            result.push({title: `Zone · ${id}`, data});
            pair(data, 'Zone IP', zone.prefix || '未分配 / Unassigned');
            pair(data, 'Location', zone.location);
            pair(data, 'Serving satellite', zone.serving.map(id => state.nodes[id].name).join(', ') || '当前无服务星 / No service');
            if (address) data['GS → Zone'] = command(`ping ${address}`);
            const serviceActions = actions(zone.serving.map(id => state.nodes[id]));
            serviceActions.data.watch = link('取消关注 / Unwatch', {'data-skyseed-unwatch': id});
            result.push(serviceActions);
            for (const ground of zone.ground_stations) {
                const node = state.nodes[ground], data = {};
                const ip = node.identity_address.split('/')[0];
                pair(data, 'GS IP', ip || '未分配 / Unassigned');
                if (address && ip) data['Satellite → GS'] = command(`ping -I ${address} ${ip}`);
                data.Console = ['', actions([node]).data[node.container]];
                result.push({title: `GS · ${node.name}`, data});
            }
            return result;
        }
        const node = byContainer.get(selected);
        if (node) {
            result.push({title: `${node.role}: ${node.name}`, data}, actions([node]));
            pair(data, 'Container', node.container_name);
            pair(data, 'State', node.container_state || (node.running ? 'Running' : 'Stopped'));
            pair(data, 'Identity IP', node.identity_address);
            pair(data, 'Group', node.membership || node.group);
            pair(data, 'Slot', node.slot);
            pair(data, 'Location', node.location);
            if (node.position_m) {
                pair(data, 'Model ECEF · km', node.position_m.map(value => (value / 1000).toFixed(1)).join(', '));
                pair(data, 'Position sample', node.position_time);
            }
            const zones = relatedZones.get(selected) || [];
            if (zones.length) result.push(zonePicker(zones));
            if (node.role === 'UT') {
                pair(data, 'Zone', node.zone);
                pair(data, 'Serving satellite', state.service ? state.nodes[state.service].name : '—');
                for (const [id, ip] of Object.entries(node.ground_stations)) pair(data, state.nodes[id].name, ip);
            }
            const addresses = {};
            result.push({title: 'Interfaces', data: addresses});
            for (const item of node.interfaces) {
                const peer = item.peer ? ` → ${state.nodes[item.peer].name} · ${item.peer_interface}` : '';
                pair(addresses, `${item.name} · ${item.up ? 'UP' : 'DOWN'}`,
                    `${item.addresses.join(', ') || '—'}${peer}`);
            }
        } else {
            const edge = edgeStyles.get(selected);
            if (edge?.zone) {
                const zone = state.zones[edge.zone];
                result.push({title: 'Zone 服务关系 / Zone service', data: {'Zone': edge.zone, 'Zone IP': zone.prefix}},
                    actions(zone.serving.map(id => state.nodes[id])));
            } else {
                const link = state.links.find(item => item.id === selected);
                if (!link) result.push({title: '连接已移除 / Link removed', data});
                else {
                    result.push(actions(link.ends.map(end => state.nodes[end.node])));
                    for (const end of link.ends) {
                        const data = {};
                        result.push({title: state.nodes[end.node].name, data});
                        pair(data, 'Interface', `${end.name} · ifindex ${end.index}`);
                        pair(data, 'IP', end.addresses.join(', '));
                        pair(data, 'State', end.up ? 'UP' : 'DOWN');
                        if (end.parameters.delay_us !== undefined) {
                            pair(data, 'Delay', `${end.parameters.delay_us / 1000} ms`);
                            pair(data, 'Loss', `${end.parameters.drop ? 100 : end.parameters.loss_percent}%`);
                        }
                    }
                }
            }
        }
        return result;
    }

    function edgeStyle(edge) {
        const flashing = flashes.has(edge.id);
        const adjacent = edge.from === selected || edge.to === selected || edge.id === selected;
        return {...edge, hidden: !(allLinks.checked || edge.access || edge.zone || adjacent || flashing),
            ...(flashing ? {width: 4, color: {color: '#ff951a', highlight: '#ff951a', hover: '#ff951a'}} : {})};
    }

    function refreshEdges() {
        ui._edges.update([...edgeStyles.values()].map(edgeStyle));
    }

    function remember() {
        localStorage.setItem(storageKey, JSON.stringify([...watched]));
    }

    function showZone(id) {
        if (!watched.has(id)) {
            let slot = 0;
            const used = new Set(watched.values());
            while (used.has(slot)) slot++;
            watched.set(id, slot);
            remember();
        }
        selected = zoneId(id);
        render();
        ui._graph.selectNodes([selected]);
        config.details.value = details();
        config.detailsDialogVisible.value = true;
    }

    const onChange = event => {
        if (event.target.matches('[data-skyseed-zones]') && event.target.value) showZone(event.target.value);
    };
    const onAction = event => {
        const target = event.target.closest('.skyseed-action');
        if (!target) return;
        event.preventDefault();
        if (target.hasAttribute('data-skyseed-unwatch')) {
            watched.delete(target.getAttribute('data-skyseed-unwatch'));
            remember();
            selected = null;
            config.detailsDialogVisible.value = false;
            render();
        } else if (target.hasAttribute('data-skyseed-copy')) {
            navigator.clipboard.writeText(target.getAttribute('data-skyseed-copy')).catch(error => { status.innerText = error.message; });
        }
    };
    document.addEventListener('change', onChange);
    document.addEventListener('click', onAction);
    allLinks.addEventListener('change', refreshEdges);

    ui._updateInfoPlateWith = async id => {
        selected = id;
        ui._curretNode = ui._nodes.get(id);
        refreshEdges();
        return details();
    };

    const stopCapture = capture(ui, source, {
        getNode: id => byContainer.get(id), getState: () => state,
        flash: id => {
            if (!edgeStyles.has(id)) return;
            clearTimeout(flashes.get(id));
            flashes.set(id, setTimeout(() => {
                flashes.delete(id);
                const edge = edgeStyles.get(id);
                if (edge) ui._edges.update(edgeStyle(edge));
            }, 2500));
            ui._edges.update(edgeStyle(edgeStyles.get(id)));
        },
    });

    await ui.start();
    ui._macMapping = {};
    ui._graph.setOptions({physics: false, edges: {smooth: false},
        nodes: {font: {size: 14}}, interaction: {hover: true, dragNodes: false}});
    ui.allLoadingInstance?.close();
    ui._graph.on('beforeDrawing', context => {
        context.save();
        context.font = '24px sans-serif';
        context.fillStyle = '#31445a';
        context.textAlign = 'center';
        for (const orbit of orbitLabels) context.fillText(orbit.name, orbit.x, orbit.y);
        context.font = '30px sans-serif';
        context.fillStyle = '#75429b';
        if (state) {
            for (const heading of state.layout.headings) context.fillText(heading.name, heading.x, heading.y);
            const zone = state.layout.zones;
            if (watched.size) context.fillText('关注的 Zone / Watched Zones', zone.x + 200, zone.y - 100);
        }
        context.restore();
    });
    ui._graph.off('doubleClick');
    ui._graph.on('doubleClick', event => {
        const node = byContainer.get(event.nodes[0]);
        if (ui._clickTimer) clearTimeout(ui._clickTimer);
        if (node) ui.createWindow(node.container, node.name);
        else if (event.nodes[0]?.startsWith('zone:')) showZone(event.nodes[0].slice(5));
    });
    ui._graph.on('click', event => {
        if (event.nodes.length) return;
        selected = event.edges[0] || null;
        refreshEdges();
        if (selected) {
            config.details.value = details();
            config.detailsDialogVisible.value = true;
        }
    });

    function render() {
        const nodes = Object.values(state.nodes).map(node => {
            const meta = {name: node.name, displayname: node.name, role: node.role, asn: 150,
                nets: node.interfaces.flatMap(item => item.addresses.map(address => ({name: item.name, address})))};
            const object = {Id: node.container, Names: ['/' + node.container_name], Labels: {}, NetworkSettings: {Networks: {}},
                meta: {emulatorInfo: meta, relation: {parent: new Set()}}};
            const vertex = {id: node.container, type: 'node', object, collapsed: false,
                label: node.role === 'Satellite' ? String(node.slot) : node.name,
                shape: node.role === 'Satellite' ? 'circle' : 'box', size: 12,
                ...(node.role === 'Satellite' ? {margin: 1, widthConstraint: {minimum: 24},
                    heightConstraint: {minimum: 24}, chosen: {label: false}} : {}),
                color: {background: node.role === 'UT' ? '#f7b955' : node.role === 'GS' ? '#9bd3bc' : '#a4c8e8',
                    border: node.running ? '#31445a' : '#b91c1c'},
                title: `${node.name}\n${node.identity_address}`, physics: false};
            if (!fitted || !ui._nodes.get(node.container)) Object.assign(vertex, {x: node.x, y: node.y, fixed: true});
            return vertex;
        });
        source._nodes = nodes.map(node => node.object);
        const region = state.layout.zones;
        for (const [id, slot] of watched) {
            const zone = state.zones[id], prefix = zone.prefix;
            nodes.push({id: zoneId(id), type: 'network', collapsed: false, shape: 'diamond', size: 24, font: {size: 22},
                label: `${id}\n${prefix || '未分配 IP'}`, title: '关注的 Zone / Watched Zone',
                x: region.x + slot % region.columns * region.dx, y: region.y + Math.floor(slot / region.columns) * region.dy, fixed: true, physics: false,
                color: {background: '#eadcf8', border: zone.serving.length ? '#8153a2' : '#b91c1c'},
                object: {Id: zoneId(id), meta: {emulatorInfo: {name: id, scope: 'Zone', prefix, type: 'local'},
                    relation: {parent: new Set()}}}});
        }
        const ids = new Set(nodes.map(node => node.id));
        ui._nodes.remove(ui._nodes.getIds().filter(id => !ids.has(id)));
        ui._nodes.update(nodes);
        const edges = state.links.map(link => {
            const [left, right] = link.ends;
            const blocked = link.ends.some(end => !end.up || end.parameters.drop || end.parameters.loss_percent === 100);
            const access = [left, right].some(end => state.nodes[end.node].role === 'UT');
            const color = blocked ? '#d65050' : access ? '#d79626' : '#8da4b8';
            for (const [end, peer] of [[left, right], [right, left]]) {
                const item = state.nodes[end.node].interfaces.find(item => item.index === end.index);
                item.edge = link.id;
                item.peer_mac = peer.mac;
            }
            return {id: link.id, from: state.nodes[left.node].container, to: state.nodes[right.node].container,
                access, width: access ? 2.5 : 1, dashes: blocked, color: {color, highlight: color, hover: color},
                title: `${state.nodes[left.node].name} · ${left.name}\n↔ ${state.nodes[right.node].name} · ${right.name}`};
        });
        for (const id of watched.keys()) {
            for (const satellite of state.zones[id].serving) {
                edges.push({id: `service:${id}:${satellite}`, from: zoneId(id), to: state.nodes[satellite].container,
                    zone: id, width: 1.5, dashes: [6, 5], color: {color: '#9969ba', highlight: '#75429b', hover: '#75429b'},
                    title: 'Zone 服务关系 / Zone service'});
            }
        }
        edgeStyles = new Map(edges.map(edge => [edge.id, edge]));
        for (const [id, timer] of flashes) {
            if (!edgeStyles.has(id)) { clearTimeout(timer); flashes.delete(id); }
        }
        ui._edges.remove(ui._edges.getIds().filter(id => !edgeStyles.has(id)));
        refreshEdges();
        status.innerText = `${state.phase} · ${state.seconds.toFixed(1)} s · cycle ${Math.floor(state.elapsed / state.period) + 1}` +
            `\nUT → ${state.service || '—'}\n${Object.keys(state.nodes).length} containers · ${state.links.length} links · ` +
            `${watched.size} 关注的 Zone / watched Zones`;
        // Do not replace an open native Zone selector while the user chooses.
        if (config.detailsDialogVisible.value && !document.activeElement?.matches('[data-skyseed-zones]')) config.details.value = details();
        if (!fitted) { ui._graph.fit(); fitted = true; }
    }

    async function update() {
        try {
            const response = await fetch('/api/v1/skyseed');
            const value = await response.json();
            if (!value.ok) throw new Error(value.result);
            state = value.result;
            if (Date.now() / 1000 - state.updated > 6) throw new Error('网络状态更新中断 / Network state is stale');
            byContainer = new Map(Object.entries(state.nodes).map(([identity, node]) => [node.container, {...node, identity}]));
            relatedZones = new Map();
            for (const [id, zone] of Object.entries(state.zones)) {
                for (const node of [...zone.ground_stations, ...zone.serving]) {
                    const container = state.nodes[node].container;
                    if (!relatedZones.has(container)) relatedZones.set(container, []);
                    relatedZones.get(container).push(id);
                }
            }
            const ut = Object.values(state.nodes).find(node => node.role === 'UT');
            if (state.zones[ut.zone]) relatedZones.set(ut.container, [ut.zone]);
            if (!fitted) {
                const orbits = new Map();
                for (const node of Object.values(state.nodes)) {
                    if (node.role !== 'Satellite') continue;
                    if (!orbits.has(node.group)) orbits.set(node.group, {name: node.group, x: 0, y: 0, count: 0});
                    const orbit = orbits.get(node.group);
                    orbit.x += node.x;
                    orbit.y += node.y;
                    orbit.count++;
                }
                orbitLabels = [...orbits.values()].map(orbit => ({name: orbit.name,
                    x: orbit.x / orbit.count, y: orbit.y / orbit.count}));
                try {
                    const saved = localStorage.getItem(storageKey);
                    const entries = saved === null ? [] : JSON.parse(saved);
                    for (const [id, slot] of entries) if (state.zones[id]) watched.set(id, slot);
                } catch { watched.clear(); }
                // Restore extra watches without letting an old list hide the current UT Zone.
                if (state.zones[ut.zone] && !watched.has(ut.zone)) {
                    let slot = 0;
                    const used = new Set(watched.values());
                    while (used.has(slot)) slot++;
                    watched.set(ut.zone, slot);
                }
            }
            render();
        } catch (error) {
            status.innerText = String(error.message || error);
        } finally {
            if (!stopped) timer = setTimeout(update, 2000);
        }
    }
    await update();
    return () => {
        stopped = true;
        clearTimeout(timer);
        flashes.forEach(clearTimeout);
        document.removeEventListener('change', onChange);
        document.removeEventListener('click', onAction);
        stopCapture();
        controls.remove();
    };
}
