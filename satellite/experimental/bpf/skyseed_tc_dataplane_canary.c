// SPDX-License-Identifier: GPL-2.0
/*
 * Experimental Skyseed backend dataplane classifier.
 *
 * This classifier is opt-in only. Userspace loads a bounded selector-derived
 * subset from Skyseed G/Z FEC and ParameterEpoch intent while the canonical
 * Linux/direct backend remains in place. Packets outside the loaded BPF scope
 * fall through to the canonical path.
 */

#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/pkt_cls.h>
#include <linux/types.h>

#define SEC(NAME) __attribute__((section(NAME), used))
#define PIN_GLOBAL_NS 2

#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
#endif

#define ACTION_FORWARD 1
#define ACTION_PASS 2

#define LOSS_PPM_DENOMINATOR 1000000

#define COUNTER_PASS 0
#define COUNTER_DROP 1
#define COUNTER_REDIRECT 2
#define COUNTER_CANONICAL_FALLBACK 3
#define COUNTER_RUNTIME_GENERATION_HIT 4
#define COUNTER_FEC_HIT 5
#define COUNTER_DROP_MISSING_GENERATION 6
#define COUNTER_DROP_MISSING_FEC 7
#define COUNTER_DROP_INACTIVE_FEC 8
#define COUNTER_PARAMETER_GENERATION_HIT 9
#define COUNTER_PARAMETER_ENTRY_HIT 10
#define COUNTER_DROP_INACTIVE_PARAMETER 11
#define COUNTER_DROP_LOSS 12
#define COUNTER_DROP_MISSING_PARAMETER 13
#define COUNTER_DROP_BAD_PACKET 14

#ifndef SKYSEED_BPF_MAX_RUNTIME_GENERATIONS
#define SKYSEED_BPF_MAX_RUNTIME_GENERATIONS 64
#endif

#ifndef SKYSEED_BPF_MAX_FEC_ENTRIES
#define SKYSEED_BPF_MAX_FEC_ENTRIES 16
#endif

#ifndef SKYSEED_BPF_MAX_PARAMETER_GENERATIONS
#define SKYSEED_BPF_MAX_PARAMETER_GENERATIONS 64
#endif

#ifndef SKYSEED_BPF_MAX_PARAMETER_ENTRIES
#define SKYSEED_BPF_MAX_PARAMETER_ENTRIES 16
#endif

struct bpf_elf_map {
    __u32 type;
    __u32 size_key;
    __u32 size_value;
    __u32 max_elem;
    __u32 flags;
    __u32 id;
    __u32 pinning;
};

struct route_key {
    __u32 generation;
    __u32 dst_ipv4;
};

struct route_value {
    __u32 active;
    __u32 action;
    __u32 ifindex;
};

struct parameter_key {
    __u32 generation;
    __u32 dst_ipv4;
    __u32 flow;
};

struct parameter_value {
    __u32 active;
    __u32 loss_ppm;
    __u32 mark;
};

static void *(*bpf_map_lookup_elem)(void *map, const void *key) =
    (void *)BPF_FUNC_map_lookup_elem;
static int (*bpf_redirect)(__u32 ifindex, __u64 flags) = (void *)BPF_FUNC_redirect;
static __u32 (*bpf_get_prandom_u32)(void) = (void *)BPF_FUNC_get_prandom_u32;

struct bpf_elf_map SEC("maps") active_generation = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = 1,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") generation_status = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = SKYSEED_BPF_MAX_RUNTIME_GENERATIONS,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") fec_forwarding = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct route_key),
    .size_value = sizeof(struct route_value),
    .max_elem = SKYSEED_BPF_MAX_FEC_ENTRIES,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") packet_counters = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u64),
    .max_elem = 16,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") parameter_generation = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = 1,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") param_status = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = SKYSEED_BPF_MAX_PARAMETER_GENERATIONS,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") parameter_values = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct parameter_key),
    .size_value = sizeof(struct parameter_value),
    .max_elem = SKYSEED_BPF_MAX_PARAMETER_ENTRIES,
    .pinning = PIN_GLOBAL_NS,
};

static __always_inline void count_packet(__u32 key)
{
    __u64 *counter = bpf_map_lookup_elem(&packet_counters, &key);

    if (counter)
        __sync_fetch_and_add(counter, 1);
}

static __always_inline int parse_dst_ipv4(struct __sk_buff *skb, __u32 *dst_ipv4, __u32 *flow)
{
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;
    struct ethhdr *eth = data;
    struct iphdr *ip;

    if ((void *)(eth + 1) > data_end) {
        count_packet(COUNTER_DROP_BAD_PACKET);
        return -1;
    }

    if (eth->h_proto != __builtin_bswap16(ETH_P_IP))
        return 0;

    ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end) {
        count_packet(COUNTER_DROP_BAD_PACKET);
        return -1;
    }

    *dst_ipv4 = ip->daddr;
    *flow = ip->protocol;
    return 1;
}

SEC("classifier/canary")
int skyseed_dataplane_canary(struct __sk_buff *skb)
{
    __u32 zero = 0;
    __u32 dst_ipv4 = 0;
    __u32 flow = 0;
    __u32 *generation;
    __u32 *generation_ok;
    __u32 *parameter_generation_value;
    __u32 *parameter_generation_ok;
    struct route_key route_key = {};
    struct route_value *route;
    struct parameter_key parameter_key = {};
    struct parameter_value *parameter;
    int parse_status;

    parse_status = parse_dst_ipv4(skb, &dst_ipv4, &flow);
    if (parse_status == 0) {
        count_packet(COUNTER_CANONICAL_FALLBACK);
        count_packet(COUNTER_PASS);
        return TC_ACT_OK;
    }
    if (parse_status < 0) {
        count_packet(COUNTER_DROP);
        return TC_ACT_SHOT;
    }

    generation = bpf_map_lookup_elem(&active_generation, &zero);
    if (!generation) {
        count_packet(COUNTER_DROP_MISSING_GENERATION);
        count_packet(COUNTER_DROP);
        return TC_ACT_SHOT;
    }

    route_key.generation = *generation;
    route_key.dst_ipv4 = dst_ipv4;
    route = bpf_map_lookup_elem(&fec_forwarding, &route_key);
    if (!route) {
        count_packet(COUNTER_CANONICAL_FALLBACK);
        count_packet(COUNTER_PASS);
        return TC_ACT_OK;
    }

    generation_ok = bpf_map_lookup_elem(&generation_status, generation);
    if (!generation_ok || !*generation_ok) {
        count_packet(COUNTER_DROP_MISSING_GENERATION);
        count_packet(COUNTER_DROP);
        return TC_ACT_SHOT;
    }
    count_packet(COUNTER_RUNTIME_GENERATION_HIT);
    count_packet(COUNTER_FEC_HIT);

    if (!route->active) {
        count_packet(COUNTER_DROP_INACTIVE_FEC);
        count_packet(COUNTER_DROP);
        return TC_ACT_SHOT;
    }

    parameter_generation_value = bpf_map_lookup_elem(&parameter_generation, &zero);
    if (!parameter_generation_value) {
        count_packet(COUNTER_DROP_MISSING_PARAMETER);
        count_packet(COUNTER_DROP);
        return TC_ACT_SHOT;
    }
    parameter_generation_ok = bpf_map_lookup_elem(&param_status, parameter_generation_value);
    if (!parameter_generation_ok || !*parameter_generation_ok) {
        count_packet(COUNTER_DROP_MISSING_PARAMETER);
        count_packet(COUNTER_DROP);
        return TC_ACT_SHOT;
    }
    count_packet(COUNTER_PARAMETER_GENERATION_HIT);

    parameter_key.generation = *parameter_generation_value;
    parameter_key.dst_ipv4 = dst_ipv4;
    parameter_key.flow = flow;
    parameter = bpf_map_lookup_elem(&parameter_values, &parameter_key);
    if (!parameter) {
        count_packet(COUNTER_DROP_MISSING_PARAMETER);
        count_packet(COUNTER_DROP);
        return TC_ACT_SHOT;
    }
    count_packet(COUNTER_PARAMETER_ENTRY_HIT);

    if (!parameter->active) {
        count_packet(COUNTER_DROP_INACTIVE_PARAMETER);
        count_packet(COUNTER_DROP);
        return TC_ACT_SHOT;
    }
    if (parameter->mark)
        skb->mark = parameter->mark;
    if (parameter->loss_ppm >= LOSS_PPM_DENOMINATOR ||
        (parameter->loss_ppm > 0 &&
         (bpf_get_prandom_u32() % LOSS_PPM_DENOMINATOR) < parameter->loss_ppm)) {
        count_packet(COUNTER_DROP_LOSS);
        count_packet(COUNTER_DROP);
        return TC_ACT_SHOT;
    }

    if (route->action == ACTION_FORWARD && route->ifindex) {
        count_packet(COUNTER_REDIRECT);
        return bpf_redirect(route->ifindex, 0);
    }

    count_packet(COUNTER_PASS);
    return TC_ACT_OK;
}

char __license[] SEC("license") = "GPL";
