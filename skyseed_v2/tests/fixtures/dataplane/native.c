/* Offline helper simulation, executing the actual handler/framework C. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include "selected.c"
static unsigned char packet[128];
static unsigned int generation, version, ingress = 3, egress = 21, route = 1, active = 1;
static unsigned long long readers[8], losses, packets;
static struct sky_event observed;
static int mode, events, redirect_index, selected_exit;
static void *bpf_map_lookup_elem(const void *map, const void *key) {
    if (map == &active_gen) return mode == 10 ? 0 : &generation;
    if (map == &gen_readers) {
        unsigned int gen = *(const unsigned int *)key;
        if (mode == 20) generation++;
        if (mode == 21) return 0;
        return &readers[gen];
    }
    if (map == &traffic_losses) return &losses;
    if (map == &ingress_bind) return mode == 13 ? 0 : &ingress;
    if (map == &egress_bind) { selected_exit = *(const unsigned int *)key; return mode == 14 ? 0 : &egress; }
#ifdef DEFAULT_TEST
    if (map == &gen_versions) return mode == 15 ? 0 : &version;
    if (map == &active_links) { if (ingress == SKY_LOCAL_INGRESS) abort(); return mode == 11 ? 0 : &active; }
    if (map == &routes) {
        const struct version_key *k = key;
        if (k->version != version || k->value != 0x010012c6) abort();
        return mode == 12 ? 0 : &route;
    }
#else
    if (map == &private_packets) return &packets;
#endif
    abort();
}
static long bpf_redirect(unsigned int index, unsigned long long flags) { redirect_index = index; return 7; }
static unsigned long long bpf_ktime_get_ns(void) { return 123456; }
static long bpf_ringbuf_output(const void *map, void *data, unsigned long long size, unsigned long long flags) {
    if (size != sizeof(observed)) abort();
    memcpy(&observed, data, size); events++;
    return mode == 40 ? -1 : 0;
}
int main(int argc, char **argv) {
    mode = atoi(argv[1]);
    sky_config.telemetry = atoi(argv[2]); sky_config.continuation = atoi(argv[3]);
    if (mode == 1) generation = version = 1, route = 2, egress = 22;
    if (mode == 16) route = 0;
    if (mode == 17) ingress = 0;
    if (mode == 18) egress = 0;
    if (mode == 22) route = 0xfffffffd;
    if (mode == 23) route = 0xfffffffe;
    if (argc > 4 && atoi(argv[4])) ingress = SKY_LOCAL_INGRESS;
    if (argc > 5) route = strtoul(argv[5], 0, 10);
    packet[12] = mode == 30 ? 0 : 8; packet[14] = mode == 33 ? 0x44 : 0x45;
    packet[23] = 1; packet[26] = 100; packet[29] = 1;
    packet[30] = 198; packet[31] = 18; packet[33] = 1;
    packet[34] = 8; packet[39] = 2; packet[41] = 3;
    struct __sk_buff skb = { .len = 98, .ifindex = 11,
        .data = (uintptr_t)packet, .data_end = (uintptr_t)packet + (mode == 31 ? 13 : mode == 32 ? 33 : 42) };
    int result = skyseed_tc(&skb);
    unsigned long long total = 0;
    for (int i = 0; i < 8; ++i) total += readers[i];
    printf("{\"action\":%d,\"output\":%d,\"events\":%d,\"readers\":%llu,\"losses\":%llu,\"private\":%llu,\"reason\":%d,\"icmp\":%d,\"facts\":%u,\"generation\":%u,\"source\":%u,\"exit\":%d}\n",
        result, redirect_index, events, total, losses, packets, observed.reason,
        observed.icmp_valid, observed.facts, observed.generation, observed.source_ipv4, selected_exit);
    return 0;
}
