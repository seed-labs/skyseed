/* Executes the authored handler and generated layout against encoded material. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include "selected.c"
static unsigned char packet[256];
static struct threshold_route rule;
static __u32 generation, version, ingress = 1, active = 1, egress;
static __u64 readers, losses, counter;
static int offset, output;
static void *bpf_map_lookup_elem(const void *map, const void *key) {
    if (map == &active_gen) return &generation;
    if (map == &gen_readers) return &readers;
    if (map == &ingress_bind) return &ingress;
    if (map == &egress_bind) { egress = *(__u32 *)key + offset; return &egress; }
    if (map == &gen_versions) return &version;
    if (map == &active_links) { if (ingress == SKY_LOCAL_INGRESS) abort(); return &active; }
    if (map == &routes) return &rule;
    if (map == &packets) return &counter;
    if (map == &traffic_losses) return &losses;
    abort();
}
static long bpf_redirect(unsigned int index, unsigned long long flags) { output = index; return TC_ACT_REDIRECT; }
static unsigned long long bpf_ktime_get_ns(void) { return 1; }
static long bpf_ringbuf_output(const void *map, void *data, unsigned long long size, unsigned long long flags) { return 0; }
int main(int argc, char **argv) {
    FILE *input = fopen(argv[1], "rb");
    if (!input || fread(&rule, sizeof(rule), 1, input) != 1) return 2;
    fclose(input);
    offset = atoi(argv[3]);
    int local_mode = argc > 5 ? atoi(argv[5]) : 0;
    packet[12] = 8; packet[14] = 0x45;
    struct __sk_buff skb = {.len = atoi(argv[2]), .ifindex = 7,
        .data = (uintptr_t)packet, .data_end = (uintptr_t)packet + sizeof(packet)};
    int action = 0;
    for (int i = 0; i < (argc > 4 ? atoi(argv[4]) : 1); i++) {
        ingress = local_mode == 1 || (local_mode == 2 && i) ? SKY_LOCAL_INGRESS : 1;
        action = skyseed_tc(&skb);
    }
    printf("%d %d %llu %llu\n", action, output, (unsigned long long)readers, (unsigned long long)counter);
    return 0;
}
