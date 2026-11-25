const assert = require('node:assert/strict');
const {MapState} = require('../../../tools/internet_map/app/backend/state');

const start = '2026-01-01T00:00:00.000Z';
const stamp = ms => new Date(Date.parse(start) + ms).toISOString();
const id = JSON.stringify(['a', 'b']);
const ends = ['a', 'b'].map(node => ({node_id: node, interface: 'ss' + node}));
const config = {start, period: 600, nodes: {a: {}, b: {}, ut: {}}, zones: {z: {}}, layout: {},
    links: {[id]: {endpoints: ends}}, updates: {'0|["a","b"]': [[], []]},
    routes: {}, attachment: {node: 'ut', local: {interface: 'ssut'}, remote: {interface: 'ssut'}}};
const state = new MapState(config);
function link(operation) {state.apply([{kind: 'link', scheduled: start, target: ['a','b'], operation}]);}
assert.equal(state.response().links.length, 0);
state.apply([{kind: 'available', scheduled: start}]);
assert.deepEqual(state.response(0).delta, {links: {}, zones: {}});
link('establish');
assert.deepEqual(state.response(0).delta.links[id].ends.map(end => end.name), ['ssa', 'ssb']);
link('withdraw');
assert.equal(state.response(1).delta.links[id], null);
state.attach('a');
state.attach('b');
assert.equal(state.response(3).delta.links['["ut","a"]'], null);
assert.ok(state.response(3).delta.links['["ut","b"]']);

const group = {size: 3, changes: [[0,'z',true],[2,'z',false]]};
config.routes['0|a'] = config.routes['600000000|a'] = group;
for (const scheduled of [start, stamp(600000), stamp(1200000)]) {
    function adopt() {state.apply([{kind:'route', adoptions:[{scheduled, nodes:['a'], instruction_counts:{a:1}}]}]);}
    adopt();
    assert.deepEqual(config.zones.z.serving, ['a']);
    const revision = state.revision;
    adopt();
    assert.equal(state.revision, revision);
    adopt();
    assert.deepEqual(state.response(revision).delta.zones, {z: []});
}
config.routes['0|b'] = {...group, operations:[2,1]};
const linux = {kind:'route', target:'b', scheduled:start};
state.apply([linux]);
assert.deepEqual(config.zones.z.serving, ['b']);
state.apply([linux]);
assert.deepEqual(config.zones.z.serving, []);
