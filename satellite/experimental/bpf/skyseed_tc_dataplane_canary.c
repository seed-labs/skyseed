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
#define COUNTER_RETURN_HANDOFF_HIT 15
#define COUNTER_SEEN 16

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

#ifndef SKYSEED_BPF_MAX_INTERFACE_COUNTER_ENTRIES
#define SKYSEED_BPF_MAX_INTERFACE_COUNTER_ENTRIES 8192
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
    __u32 ingress_ifindex;
    __u32 dst_ipv4;
};

struct route_value {
    __u32 active;
    __u32 action;
    __u32 ifindex;
    __u32 new_dst_ipv4;
    unsigned char dst_mac[6];
    unsigned char src_mac[6];
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

struct interface_counter_key {
    __u32 ifindex;
    __u32 counter;
};

static void *(*bpf_map_lookup_elem)(void *map, const void *key) =
    (void *)BPF_FUNC_map_lookup_elem;
static int (*bpf_map_update_elem)(void *map, const void *key, const void *value,
                                  __u64 flags) = (void *)BPF_FUNC_map_update_elem;
static int (*bpf_skb_store_bytes)(struct __sk_buff *skb, __u32 offset, const void *from,
                                  __u32 len, __u64 flags) =
    (void *)BPF_FUNC_skb_store_bytes;
static int (*bpf_l3_csum_replace)(struct __sk_buff *skb, __u32 offset, __u64 from,
                                  __u64 to, __u64 size) =
    (void *)BPF_FUNC_l3_csum_replace;
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
    .max_elem = 17,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") interface_counters = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct interface_counter_key),
    .size_value = sizeof(__u64),
    .max_elem = SKYSEED_BPF_MAX_INTERFACE_COUNTER_ENTRIES,
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

static __always_inline void count_interface_packet(__u32 ifindex, __u32 key)
{
    struct interface_counter_key counter_key = {
        .ifindex = ifindex,
        .counter = key,
    };
    __u64 initial = 1;
    __u64 *counter = bpf_map_lookup_elem(&interface_counters, &counter_key);

    if (counter) {
        __sync_fetch_and_add(counter, 1);
        return;
    }
    bpf_map_update_elem(&interface_counters, &counter_key, &initial, BPF_ANY);
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

static __always_inline int rewrite_l2(struct __sk_buff *skb, struct route_value *route)
{
    unsigned char rewrite[12];

    if (!route->dst_mac[0])
        return 0;

    rewrite[0] = route->dst_mac[0];
    rewrite[1] = route->dst_mac[1];
    rewrite[2] = route->dst_mac[2];
    rewrite[3] = route->dst_mac[3];
    rewrite[4] = route->dst_mac[4];
    rewrite[5] = route->dst_mac[5];
    rewrite[6] = route->src_mac[0];
    rewrite[7] = route->src_mac[1];
    rewrite[8] = route->src_mac[2];
    rewrite[9] = route->src_mac[3];
    rewrite[10] = route->src_mac[4];
    rewrite[11] = route->src_mac[5];

    return bpf_skb_store_bytes(skb, 0, rewrite, sizeof(rewrite), 0);
}

static __always_inline int rewrite_dst_ipv4(struct __sk_buff *skb, __u32 old_dst_ipv4,
                                            struct route_value *route)
{
    __u32 new_dst_ipv4 = route->new_dst_ipv4;

    if (!new_dst_ipv4)
        return 0;
    if (bpf_l3_csum_replace(skb, 24, old_dst_ipv4, new_dst_ipv4, 4) < 0)
        return -1;
    return bpf_skb_store_bytes(skb, 30, &new_dst_ipv4, sizeof(new_dst_ipv4), 0);
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

    count_packet(COUNTER_SEEN);
    count_interface_packet(skb->ifindex, COUNTER_SEEN);

    parse_status = parse_dst_ipv4(skb, &dst_ipv4, &flow);
    if (parse_status == 0) {
        count_packet(COUNTER_CANONICAL_FALLBACK);
        count_interface_packet(skb->ifindex, COUNTER_CANONICAL_FALLBACK);
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
    route_key.ingress_ifindex = skb->ifindex;
    route_key.dst_ipv4 = dst_ipv4;
    route = bpf_map_lookup_elem(&fec_forwarding, &route_key);
    if (!route) {
        count_packet(COUNTER_CANONICAL_FALLBACK);
        count_interface_packet(skb->ifindex, COUNTER_CANONICAL_FALLBACK);
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
    count_interface_packet(skb->ifindex, COUNTER_RUNTIME_GENERATION_HIT);
    count_packet(COUNTER_FEC_HIT);
    count_interface_packet(skb->ifindex, COUNTER_FEC_HIT);
    if (route->new_dst_ipv4)
        count_packet(COUNTER_RETURN_HANDOFF_HIT);

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
        if (rewrite_dst_ipv4(skb, dst_ipv4, route) < 0) {
            count_packet(COUNTER_DROP_BAD_PACKET);
            count_packet(COUNTER_DROP);
            return TC_ACT_SHOT;
        }
        if (rewrite_l2(skb, route) < 0) {
            count_packet(COUNTER_DROP_BAD_PACKET);
            count_packet(COUNTER_DROP);
            return TC_ACT_SHOT;
        }
        count_packet(COUNTER_REDIRECT);
        count_interface_packet(skb->ifindex, COUNTER_REDIRECT);
        return bpf_redirect(route->ifindex, 0);
    }

    count_packet(COUNTER_PASS);
    return TC_ACT_OK;
}

char __license[] SEC("license") = "GPL";
