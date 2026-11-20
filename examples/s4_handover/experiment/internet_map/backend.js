// The SEED server still owns container terminals and sniffer sessions.
const fs = require('fs');
const {spawn} = require('child_process');
const Docker = require('dockerode');
const docker = new Docker();
const root = process.env.SKYSEED_STATE_DIR || '/s4';
const config = JSON.parse(fs.readFileSync(`${root}/map-config.json`, 'utf8'));
const containers = new Set(Object.values(config.nodes).map(node => node.container));

exports.includes = id => containers.has(id);
exports.resolve = id => {
    const matches = [...containers].filter(value => value.startsWith(id));
    if (matches.length !== 1) throw new Error(`Unknown or ambiguous S4 container: ${id}`);
    return matches[0];
};

// Inspect only this run's containers. Terminal lookup never waits for a global list.
let inventory = [], updated = 0, pending = null;
exports.inventory = async () => {
    if (Date.now() - updated < 2000) return inventory;
    if (!pending) {
        pending = (async () => {
            const ids = [...containers], found = [];
            await Promise.all(Array.from({length: Math.min(12, ids.length)}, async () => {
                while (ids.length) {
                    const id = ids.pop();
                    try {
                        const item = await docker.getContainer(id).inspect();
                        found.push({Id: item.Id, Names: [item.Name], Labels: item.Config.Labels,
                            State: item.State.Status, Status: item.State.Status,
                            Image: item.Config.Image, NetworkSettings: item.NetworkSettings});
                    } catch (error) {
                        if (error.statusCode !== 404) throw error;
                    }
                }
            }));
            inventory = found;
            updated = Date.now();
            return found;
        })().finally(() => { pending = null; });
    }
    return pending;
};

exports.install = app => {
    let failure = null;
    const child = spawn('chroot', ['/host', process.env.SKYSEED_PYTHON,
        process.env.SKYSEED_COLLECTOR, process.env.SKYSEED_CONFIG], {stdio: 'inherit'});
    child.on('error', error => { failure = error.message; });
    child.on('exit', code => { failure = `Network observer stopped (${code}); see docker logs.`; });
    app.get('/api/v1/skyseed', async (req, res) => {
        try {
            if (failure) throw new Error(failure);
            const state = JSON.parse(fs.readFileSync(`${root}/map-state.json`, 'utf8'));
            const inventory = new Map((await exports.inventory()).map(item => [item.Id, item]));
            for (const node of Object.values(state.nodes)) {
                const container = inventory.get(node.container);
                node.container_state = container ? container.State : 'removed';
            }
            res.json({ok: true, result: state});
        } catch (error) {
            res.status(503).json({ok: false, result: error.code === 'ENOENT' ? 'Preparing network view…' : error.message});
        }
    });
    // The S4 map consists of direct veth links, supplied by the observer.
    app.get('/api/v1/network', (req, res) => res.json({ok: true, result: []}));
};
