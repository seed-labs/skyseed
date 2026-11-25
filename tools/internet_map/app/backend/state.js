// Map changes become visible when the Runner acknowledges the matching instructions.
function microseconds(stamp) {
    const fraction = stamp.match(/\.(\d+)/)?.[1] || '';
    return Date.parse(stamp) * 1000 + Number(fraction.padEnd(6, '0').slice(3, 6));
}

function changes() {
    return {links: new Set(), zones: new Set()};
}

class MapState {
    constructor(config) {
        this.config = config;
        this.start = microseconds(config.start);
        this.nodes = config.nodes;
        this.zones = config.zones;
        this.links = new Map();
        this.cursors = new Map();
        this.history = [];
        this.revision = 0;
        this.phase = 'preparing';
        this.origin = null;
        this.elapsed = 0;
        this.service = null;
        for (const node of Object.values(this.nodes)) {
            node.interfaces = [];
            node.running = node.container_state === 'running';
        }
        for (const zone of Object.values(this.zones)) zone.serving = [];
    }

    time(stamp) {
        const offset = microseconds(stamp) - this.start;
        const period = this.config.period * 1e6;
        return period && offset >= period ? period + offset % period : offset;
    }

    offset(target, scheduled, count) {
        const last = this.cursors.get(target);
        const at = last?.scheduled === scheduled ? last.next : 0;
        this.cursors.set(target, {scheduled, next: at + count});
        return at;
    }

    setLink(id, endpoints, params, changed) {
        if (endpoints) {
            const previous = this.links.get(id);
            const ends = endpoints.map((end, i) => ({
                node: end.node_id, name: end.interface, mac: end.mac,
                addresses: end.address ? [end.address] : [], up: true,
                parameters: params.find(p => p.interface === end.interface) || previous?.ends[i].parameters || {},
            }));
            this.links.set(id, {id, ends});
        } else {
            this.links.delete(id);
        }
        changed.links.add(id);
    }

    adopt(node, scheduled, count, changed) {
        const group = this.config.routes[this.time(scheduled) + '|' + node];
        if (!group) return;
        if (count === undefined) {
            // Linux acknowledges each operation; BPF supplies instruction counts.
            const index = this.offset('operation:' + node, scheduled, 1);
            count = group.operations[index];
        }
        const at = this.offset(node, scheduled, count);
        if (!Number.isInteger(count) || at + count > group.size) {
            throw new Error('Unmatched route receipt: ' + node + ' at ' + scheduled);
        }
        for (const [index, id, enabled] of group.changes) {
            if (index < at || index >= at + count) continue;
            const serving = this.zones[id].serving;
            if (enabled === serving.includes(node)) continue;
            if (enabled) serving.push(node);
            else serving.splice(serving.indexOf(node), 1);
            changed.zones.add(id);
        }
    }

    apply(events) {
        const changed = changes();
        for (const e of events) {
            switch (e.kind) {
                case 'clock':
                    this.origin = e.origin;
                    this.phase = 'running';
                    break;
                case 'drained':
                case 'stopped':
                    this.elapsed = this.origin === null ? this.elapsed : e.completed - this.origin;
                    this.origin = null;
                    this.phase = e.kind;
                    break;
                case 'link': {
                    const id = JSON.stringify(e.target);
                    const at = this.offset(id, e.scheduled, 1);
                    const params = this.config.updates[this.time(e.scheduled) + '|' + id]?.[at];
                    if (!params) throw new Error('Unmatched link receipt: ' + id + ' at ' + e.scheduled);
                    const ends = e.operation === 'withdraw' ? null : this.config.links[id].endpoints;
                    this.setLink(id, ends, params, changed);
                    break;
                }
                case 'route':
                    if (e.adoptions) {
                        for (const adoption of e.adoptions) {
                            for (const node of adoption.nodes) {
                                this.adopt(node, adoption.scheduled, adoption.instruction_counts[node], changed);
                            }
                        }
                    } else {
                        this.adopt(e.target, e.scheduled, undefined, changed);
                    }
                    break;
            }
        }
        this.commit(changed);
    }

    attach(peer) {
        if (peer === this.service) return;
        const a = this.config.attachment, changed = changes();
        if (this.service) this.setLink(JSON.stringify([a.node, this.service]), null, [], changed);
        if (peer) this.setLink(JSON.stringify([a.node, peer]), [
            {...a.local, node_id: a.node}, {...a.remote, node_id: peer},
        ], [], changed);
        this.service = peer;
        this.commit(changed);
    }

    commit(changed) {
        if (!changed.links.size && !changed.zones.size) return;
        this.history.push({rev: ++this.revision, ...changed});
        if (this.history.length > 128) this.history.shift();
    }

    response(since) {
        const elapsed = this.origin === null ? this.elapsed : Math.max(0, Number(process.hrtime.bigint()) / 1e9 - this.origin);
        const clock = {elapsed, seconds: this.config.period ? elapsed % this.config.period : elapsed,
            period: this.config.period, phase: this.phase, updated: Date.now() / 1000,
            service: this.service, revision: this.revision};
        if (!Number.isInteger(since) || since > this.revision || since < (this.history[0]?.rev ?? 1) - 1) {
            return {...clock, layout: this.config.layout, nodes: this.nodes, zones: this.zones,
                links: [...this.links.values()]};
        }
        const links = {}, zones = {};
        for (const entry of this.history) {
            if (entry.rev <= since) continue;
            for (const id of entry.links) links[id] = this.links.get(id) || null;
            for (const id of entry.zones) zones[id] = this.zones[id].serving;
        }
        return {...clock, delta: {links, zones}};
    }
}
module.exports = {MapState};
