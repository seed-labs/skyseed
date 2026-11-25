// Adapt SEED capture events and its in-memory recording; never generate traffic.
export function packetInterface(line, interfaces) {
    if (!/^\d\d:\d\d:\d\d\.\d+\s/.test(line)) return null;
    const index = line.match(/\bifindex\s+(\d+)\b/);
    const indexed = index && interfaces.find(item => item.index === Number(index[1]));
    if (indexed) return indexed;
    const name = line.match(/^\S+\s+(\S+)\s+(?:In|Out|B|M|P)\s/);
    if (name) return interfaces.find(item => item.name === name[1]) || null;
    const matches = interfaces.filter(item => item.edge &&
        [item.mac, item.peer_mac].some(mac => mac && line.toLowerCase().includes(mac.toLowerCase())));
    return matches.length === 1 ? matches[0] : null;
}

export function capture(ui, source, {getNode, getState, flash, lookupIndexes}) {
    const originalPacket = source._packetEventHandler, pending = new Map();
    const lookups = new WeakMap();
    function resolveIndex(node, index) {
        const links = node.interfaces;
        if (!lookups.has(links)) {
            lookups.set(links, lookupIndexes(node).catch(() => {
                lookups.delete(links);
                return [];
            }));
        }
        return lookups.get(links).then(facts => {
            if (node.interfaces !== links) return null;
            const name = facts.find(item => item.index === index)?.name;
            return links.find(item => item.name === name);
        });
    }
    const controls = document.createElement('span');
    controls.style.cssText = 'display:inline-flex;gap:8px;padding:4px';
    const record = document.createElement('button'), save = document.createElement('button');
    for (const button of [record, save]) button.className = 'el-button el-button--primary el-button--small';
    record.innerText = '开始录制 / Record';
    save.innerText = '保存记录 / Save';
    record.onclick = () => {
        ui.recordStartStop();
        record.innerText = ui._recording ? '停止录制 / Stop recording' : '重新录制 / New recording';
    };
    save.onclick = () => {
        const text = ui._events.map(event => JSON.stringify(event)).join('\n');
        const url = URL.createObjectURL(new Blob([text ? text + '\n' : ''], {type: 'application/x-ndjson'}));
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `skyseed-capture-${new Date().toISOString().replaceAll(':', '-')}.jsonl`;
        anchor.click();
        setTimeout(() => URL.revokeObjectURL(url), 0);
    };
    controls.append(record, save);
    document.querySelector('.log-controls').appendChild(controls);
    source.on('packet', data => {
        const node = getNode(data.source);
        if (!node) return;
        const text = (pending.get(data.source) || '') + data.data;
        const lines = text.split(/\r?\n/);
        pending.set(data.source, lines.pop());
        for (const line of lines) {
            const count = ui._events.length;
            originalPacket({source: data.source, data: line + '\r\n'});
            const item = packetInterface(line, node.interfaces);
            const record = ui._events.length > count ? ui._events.at(-1) : null;
            if (record) {
                const state = getState();
                Object.assign(record, {
                    received_at: new Date().toISOString(), node: node.identity, name: node.name,
                    container_name: node.container_name, map_updated: state.updated,
                    elapsed: state.elapsed, seconds: state.seconds,
                    interface: item ? {...item, addresses: [...item.addresses]} : null,
                });
            }
            if (!/^\d\d:\d\d:\d\d\.\d+\s/.test(line)) continue;
            ui._flashQueue.add(data.source);
            if (item?.edge) flash(item.edge);
            else {
                const index = line.match(/\bifindex\s+(\d+)\b/);
                if (index) resolveIndex(node, Number(index[1])).then(end => {
                    if (!end) return;
                    if (record) record.interface = {...end, addresses: [...end.addresses]};
                    flash(end.edge);
                });
            }
        }
    });
    return () => { source.on('packet', originalPacket); controls.remove(); };
}
