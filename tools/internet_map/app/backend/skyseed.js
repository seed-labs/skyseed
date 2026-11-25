const fs = require('node:fs');
const path = require('node:path');
const Docker = require('dockerode');
const {MapState} = require('./state');
const {followFile, followStore} = require('./receipts');

const root = '/run/skyseed';
const config = JSON.parse(fs.readFileSync(path.join(root, 'map-config.json')));
const {run} = config;
const state = new MapState(config), docker = new Docker();
const inventory = Object.values(config.nodes).map(node => ({
    Id: node.container, Names: ['/' + node.container_name], Labels: {
        'org.seedsecuritylabs.seedemu.meta.nodename': node.name, ...node.labels,
    },
    State: node.container_state, NetworkSettings: {Networks: {}},
}));
const known = new Set(inventory.map(node => node.Id));
exports.inventory = async () => inventory;
exports.resolve = id => {
    if (known.has(id)) return id;
    const matches = inventory.filter(node => node.Id.startsWith(id));
    if (matches.length !== 1) throw new Error('Unknown or ambiguous map container: ' + id);
    return matches[0].Id;
};

async function interfaces(container) {
    const exec = await container.exec({Cmd: ['ip', '-j', 'address'], AttachStdout: true, AttachStderr: true, Tty: true});
    const stream = await exec.start({Tty: true}), bytes = [];
    for await (const chunk of stream) bytes.push(chunk);
    return JSON.parse(Buffer.concat(bytes)).map(item => ({
        name: item.ifname, index: item.ifindex, mac: item.address, up: item.flags.includes('UP'),
        addresses: item.addr_info.map(address => address.local + '/' + address.prefixlen),
    }));
}

exports.install = app => {
    let failure = null;
    const status = error => { failure = error; };
    const apply = events => state.apply(events);
    const follow = config.store ? followStore(config.store, apply, status)
                                : followFile(path.join(root, 'execution.jsonl'), apply, status);
    follow.catch(error => status(error.message));
    const samples = config.positions ? Object.entries(JSON.parse(fs.readFileSync('/positions.json')))
        .map(([stamp, nodes]) => [Date.parse(stamp), stamp, nodes]).sort((a, b) => a[0] - b[0]) : [];

    app.get('/api/v1/skyseed/ready', (_req, res) => res.type('text').send(run));
    app.get('/api/v1/skyseed', (req, res) => {
        try {
            if (failure) throw new Error(failure);
            if (config.attachment) {
                const {file, field} = config.attachment;
                state.attach(JSON.parse(fs.readFileSync(path.join(root, file)))[field]);
            }
            const since = req.query.run === run ? Number(req.query.since) : null;
            res.json({ok: true, result: {run, ...state.response(since)}});
        } catch (error) {
            res.status(503).json({ok: false, result: error.message});
        }
    });
    app.get('/api/v1/skyseed/node/:id', async (req, res) => {
        try {
            const id = req.params.id;
            if (!Object.hasOwn(config.nodes, id)) return res.sendStatus(404);
            const container = docker.getContainer(config.nodes[id].container), info = await container.inspect();
            const result = {container_state: info.State.Status, interfaces: info.State.Running ? await interfaces(container) : []};
            const when = Date.parse(config.start) + state.response(state.revision).seconds * 1000;
            const sample = samples.findLast(item => item[0] <= when);
            if (sample) Object.assign(result, {position_m: sample[2][id], position_time: sample[1]});
            res.json({ok: true, result});
        } catch (error) {
            res.status(503).json({ok: false, result: error.message});
        }
    });
    app.get('/api/v1/network', (req, res) => res.json({ok: true, result: []}));
};
