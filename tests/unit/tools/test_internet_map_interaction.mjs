import assert from 'node:assert/strict';
import {start} from '../../../tools/internet_map/app/frontend/src/skyseed.mjs';

// Exercise the adapter against SEED's graph/panel contract, without a browser or Docker API.
class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.style = {}; this.innerText = ''; }
    append(...items) { this.children.push(...items); }
    appendChild(item) { this.append(item); }
    setAttribute(key, value) { this.attrs[key] = String(value); }
    getAttribute(key) { return this.attrs[key]; }
    hasAttribute(key) { return key in this.attrs; }
    addEventListener(name, callback) { this[name] = callback; }
    matches(selector) { return this.hasAttribute(selector.slice(1, -1)); }
    closest() { return this; }
    remove() {}
    get outerHTML() { return `<${this.tag} ${JSON.stringify(this.attrs)}>${this.innerText}${this.children.map(v => v.outerHTML || v).join('')}</${this.tag}>`; }
}
class DataSet {
    data = new Map();
    get(id) { return this.data.get(id); }
    getIds() { return [...this.data.keys()]; }
    update(items) { for (const item of Array.isArray(items) ? items : [items]) this.data.set(item.id, {...this.data.get(item.id), ...item}); }
    remove(ids) { for (const id of ids) this.data.delete(id); }
}
const listeners = {}, root = new Element('div'), timers = new Map(), storage = new Map();
let timerId = 0;
global.document = {createElement: tag => new Element(tag), querySelector: () => root,
    addEventListener: (name, callback) => { listeners[name] = callback; },
    removeEventListener: name => { delete listeners[name]; }};
global.localStorage = {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)};
global.setTimeout = (callback, delay) => { timers.set(++timerId, {callback, delay}); return timerId; };
global.clearTimeout = id => timers.delete(id);
async function advance(delay) {
    const entry = [...timers].find(([, timer]) => timer.delay === delay);
    assert.ok(entry, `timer ${delay} exists`);
    timers.delete(entry[0]);
    await entry[1].callback();
}
function node(id, role) {
    return {name: id, role, container: id, container_name: id, running: true, zones: [],
        group: 'orbit-1', slot: 1, identity_address: '198.18.0.1/32', interfaces: [],
        x: 0, y: 0, zone: 'z1', ground_stations: {gs: '198.18.0.1'}};
}
let index = 10;
const state = {nodes: {ut: node('ut', 'UT'), a: node('a', 'Satellite'), b: node('b', 'Satellite'), gs: node('gs', 'GS')},
    zones: {z1: {prefix: '198.19.0.1/32', location: '30°, 120°', ground_stations: ['gs'], serving: ['a']},
        z2: {prefix: '198.19.0.2/32', location: '40°, 120°', ground_stations: ['gs'], serving: ['b']}},
    layout: {headings: [], zones: {x: 20, y: 400, columns: 3, dx: 350, dy: 160}},
    links: [], service: 'a', elapsed: 10, seconds: 10, period: 600, phase: 'running'};
function connect(a, b) {
    const ends = [a, b].map(id => ({node: id, name: `ss${++index}`, index, addresses: [], up: true, parameters: {}}));
    ends.forEach(end => state.nodes[end.node].interfaces.push({...end}));
    return {id: `${a}-${b}`, ends};
}
state.links = [connect('ut', 'a'), connect('a', 'b'), connect('b', 'gs')];
state.revision = 1;
const transit = state.links[1].ends[0], kernelIndex = transit.index;
delete transit.index;  // Compiled endpoints carry names and MACs, not kernel indices.
let transitInspects = 0;
global.fetch = async url => {
    let result;
    if (url.endsWith('/node/a')) {
        transitInspects++;
        result = {container_state: 'running', interfaces: [{name: transit.name, index: kernelIndex}]};
    } else if (url.includes('/node/')) result = {container_state: 'running', interfaces: []};
    else if (url.includes('?since=')) result = {revision: 2, updated: Date.now()/1000,
        delta: {links: {}, zones: {z1: state.zones.z1.serving}}};
    else result = {...state, updated: Date.now()/1000};
    return {json: async () => ({ok: true, result: structuredClone(result)})};
};
const events = {}, opened = [], logs = [];
const ui = {_events: [], _recording: false,
    recordStartStop() { this._recording = !this._recording; if (this._recording) this._events = []; },
    _nodes: new DataSet(), _edges: new DataSet(), _flashQueue: new Set(),
    start: async () => {}, createWindow: (...args) => opened.push(args),
    _graph: {setOptions() {}, fit() {}, selectNodes() {}, off() {}, on(name, callback) { events[name] = callback; }}};
const source = {_packetEventHandler: event => { logs.push(event);
    if (ui._recording) ui._events.push({source: event.source, lines: [event.data.trim()]}); }, on(name, callback) { this[name] = callback; }};
const config = {details: {value: []}, detailsDialogVisible: {value: false}};
const stop = await start(ui, source, config);
assert.ok(ui._nodes.get('zone:z1'));
assert.equal(ui._nodes.get('zone:z2'), undefined);
assert.equal(source._nodes.length, 4, 'logical Zones never enter container capture inventory');
assert.equal(ui._edges.get('ut-a').hidden, false);
assert.equal(ui._edges.get('a-b').hidden, true);
await ui._updateInfoPlateWith('gs');
assert.equal(ui._edges.get('b-gs').hidden, false);
assert.equal(ui._edges.get('a-b').hidden, true);
// Start native recording, then receive a fragmented tcpdump line.
const captureControls = root.children[1], record = captureControls.children[0];
record.onclick();
source.packet({source: 'a', data: '12:00:'});
source.packet({source: 'a', data: `01.123456 ifindex ${kernelIndex} MPLS (label 31)\n`});
await new Promise(resolve => setImmediate(resolve));
assert.equal(transitInspects, 1);
assert.equal(ui._edges.get('a-b').hidden, false, 'traffic reveals links outside selection');
assert.equal(ui._edges.get('a-b').width, 4);
assert.ok(logs[0].data.includes('label 31'));
assert.equal(ui._events.length, 1);
const captured = JSON.stringify(ui._events[0]);
assert.equal(ui._events[0].node, 'a');
assert.equal(ui._events[0].interface.edge, 'a-b');
record.onclick();
source.packet({source: 'a', data: '12:00:02.123456 unrelated packet\n'});
assert.equal(ui._events.length, 1, 'stop recording preserves events while capture continues');
await advance(2500);
assert.equal(ui._edges.get('a-b').hidden, true);
function choose(id) {
    const target = new Element('select'); target.setAttribute('data-skyseed-zones', ''); target.value = id;
    listeners.change({target});
}
choose('z2');
const firstPosition = [ui._nodes.get('zone:z1').x, ui._nodes.get('zone:z1').y];
const secondPosition = [ui._nodes.get('zone:z2').x, ui._nodes.get('zone:z2').y];
assert.notDeepEqual(firstPosition, secondPosition);
assert.ok(JSON.stringify(config.details.value).includes('ping -I 198.19.0.2 198.18.0.1'));
await ui._updateInfoPlateWith('gs');
assert.ok(ui._nodes.get('zone:z2'), 'changing selection keeps a watched Zone');
events.doubleClick({nodes: ['a']});
assert.deepEqual(opened, [['a', 'a']]);
state.zones.z1.serving = ['b'];
await advance(1000);
assert.equal(JSON.stringify(ui._events[0]), captured, 'record retains interface facts through later snapshots');
assert.equal(ui._edges.get('service:z1:a'), undefined);
assert.ok(ui._edges.get('service:z1:b'));
assert.deepEqual([ui._nodes.get('zone:z1').x, ui._nodes.get('zone:z1').y], firstPosition);
assert.deepEqual(opened, [['a', 'a']], 'handover leaves existing terminals alone');
const button = new Element('a'); button.setAttribute('data-skyseed-unwatch', 'z1');
listeners.click({target: button, preventDefault() {}});
assert.equal(ui._nodes.get('zone:z1'), undefined);
assert.deepEqual([ui._nodes.get('zone:z2').x, ui._nodes.get('zone:z2').y], secondPosition);
stop();
const stopAgain = await start(ui, source, config);
assert.ok(ui._nodes.get('zone:z1'), 'opening the page restores the current UT Zone');
assert.deepEqual([ui._nodes.get('zone:z2').x, ui._nodes.get('zone:z2').y], secondPosition, 'refresh keeps saved slots');
stopAgain();
storage.set('skyseed.watchedZones', JSON.stringify([['z2', 0]]));
const stopOldList = await start(ui, source, config);
assert.ok(ui._nodes.get('zone:z1'), 'an old watch list cannot hide the UT Zone');
assert.notEqual(ui._nodes.get('zone:z1').x, ui._nodes.get('zone:z2').x, 'restored Zones do not overlap');
listeners.click({target: button, preventDefault() {}});
assert.equal(ui._nodes.get('zone:z1'), undefined, 'the default Zone can still be unwatched');
stopOldList();
storage.set('skyseed.watchedZones', '[]');
const stopEmptyList = await start(ui, source, config);
assert.ok(ui._nodes.get('zone:z1'), 'an empty saved list still opens with the UT Zone');
stopEmptyList();
assert.equal(timers.size, 0);
assert.deepEqual(listeners, {});
console.log('Zone selection/persistence/handover, manual terminal ownership and traffic visibility passed.');
