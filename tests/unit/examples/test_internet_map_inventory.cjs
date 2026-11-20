const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

(async () => {
    const ids = Array.from({length: 30}, (_, i) => i.toString(16).padStart(64, '0'));
    const config = {nodes: Object.fromEntries(ids.map(id => [id, {container: id}]))};
    let calls = 0, active = 0, maximum = 0;
    class Docker {
        listContainers() { throw new Error('Global container inventory must not be used'); }
        getContainer(id) {
            assert.ok(ids.includes(id));
            return {inspect: async () => {
                calls++;
                maximum = Math.max(maximum, ++active);
                await new Promise(resolve => setImmediate(resolve));
                active--;
                if (id === ids[0]) throw {statusCode: 404};
                return {Id: id, Name: '/node-' + id, Config: {Labels: {role: 'node'}},
                    State: {Status: id === ids[1] ? 'exited' : 'running'}};
            }};
        }
    }
    const context = {exports: {}, process: {env: {}}, Date,
        require: name => ({fs: {readFileSync: () => JSON.stringify(config)},
            child_process: {}, dockerode: Docker})[name]};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,
        '../../../examples/s4_handover/experiment/internet_map/backend.js'), 'utf8'), context);
    const api = context.exports;
    const [first, second] = await Promise.all([api.inventory(), api.inventory()]);
    assert.equal(first, second);
    assert.equal(first.length, 29);
    assert.equal(calls, 30);
    assert.ok(maximum <= 12);
    assert.equal(first.find(item => item.Id === ids[1]).State, 'exited');
    assert.equal((await api.inventory()), first);
    assert.equal(calls, 30);
    assert.equal(api.resolve(ids[5]), ids[5]);
    assert.throws(() => api.resolve('0'), /ambiguous/);
    assert.throws(() => api.resolve('unknown'), /Unknown/);
    console.log('Known-container inventory: direct lookup, bounded concurrency, shared refresh, stopped/removed states passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
