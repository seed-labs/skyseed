import assert from 'node:assert/strict';
import {packetInterface} from '../../../examples/s4_handover/experiment/internet_map/capture.mjs';

const interfaces = [
    {name: 'ssfirst', index: 21, edge: 'a-b', mac: '02:11:22:33:44:55', peer_mac: '02:11:22:33:44:66'},
    {name: 'sssecond', index: 32, edge: 'a-c', mac: '02:11:22:33:44:77', peer_mac: '02:11:22:33:44:88'},
];
assert.equal(packetInterface('12:00:01.123456 ssfirst In ifindex 21 02:11:22:33:44:66 MPLS (label 31)', interfaces).edge, 'a-b');
assert.equal(packetInterface('12:00:01.123456 sssecond Out IP 192.0.2.2 > 198.18.0.1', interfaces).edge, 'a-c');
assert.equal(packetInterface('12:00:01.123456 In 02:11:22:33:44:66 MPLS (label 31)', interfaces).edge, 'a-b');
assert.equal(packetInterface('12:00:01.123456 ssremoved In ifindex 99 MPLS (label 31)', interfaces), null);
assert.equal(packetInterface('tcpdump: listening on any, link-type LINUX_SLL2', interfaces), null);
console.log('Packet mapping: SLL/SLL2, MPLS/IP, removed interfaces and status lines passed.');
