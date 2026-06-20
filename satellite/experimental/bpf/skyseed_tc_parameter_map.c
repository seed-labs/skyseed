// SPDX-License-Identifier: GPL-2.0
/*
 * Isolated tc-BPF ParameterEpoch parameter-map prototype for Skyseed.
 *
 * This is not canonical runtime code. It is only for netns/veth experiments
 * that model ParameterEpoch active/loss packet decisions as BPF map lookups.
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

#define EDGE_PRIMARY 1
#define EDGE_MISSING 99
#define LOSS_PPM_DENOMINATOR 1000000

#define COUNTER_PARAMETER_GENERATION_HIT 0
#define COUNTER_PARAMETER_ENTRY_HIT 1
#define COUNTER_PASS_COUNT 2
#define COUNTER_DROP_INACTIVE 3
#define COUNTER_DROP_LOSS 4
#define COUNTER_DROP_MISSING_GENERATION 5
#define COUNTER_DROP_MISSING_PARAMETER 6
#define COUNTER_FUTURE_GENERATION_NOT_USED 7
#define COUNTER_DROP_BAD_PACKET 8

#define IP4_LE(a, b, c, d) \
    ((__u32)(a) | ((__u32)(b) << 8) | ((__u32)(c) << 16) | ((__u32)(d) << 24))
#define PRIMARY_EDGE_IP IP4_LE(10, 92, 0, 2)
#define MISSING_EDGE_IP IP4_LE(10, 92, 0, 99)

struct bpf_elf_map {
    __u32 type;
    __u32 size_key;
    __u32 size_value;
    __u32 max_elem;
    __u32 flags;
    __u32 id;
    __u32 pinning;
};

struct parameter_key {
    __u32 generation;
    __u32 edge;
    __u32 flow;
};

struct parameter_value {
    __u32 active;
    __u32 loss_ppm;
    __u32 mark;
};

static void *(*bpf_map_lookup_elem)(void *map, const void *key) =
    (void *)BPF_FUNC_map_lookup_elem;
static __u32 (*bpf_get_prandom_u32)(void) = (void *)BPF_FUNC_get_prandom_u32;

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
    .max_elem = 8,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") parameter_values = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct parameter_key),
    .size_value = sizeof(struct parameter_value),
    .max_elem = 128,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") parameter_counters = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u64),
    .max_elem = 16,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") future_param_gen = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = 2,
    .pinning = PIN_GLOBAL_NS,
};

static __always_inline void count_packet(__u32 key)
{
    __u64 *counter = bpf_map_lookup_elem(&parameter_counters, &key);

    if (counter)
        __sync_fetch_and_add(counter, 1);
}

static __always_inline int parse_parameter_key(struct __sk_buff *skb, struct parameter_key *key)
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

    if (ip->daddr == PRIMARY_EDGE_IP)
        key->edge = EDGE_PRIMARY;
    else if (ip->daddr == MISSING_EDGE_IP)
        key->edge = EDGE_MISSING;
    else
        return 0;

    key->flow = ip->protocol;
    return 1;
}

SEC("classifier/parameter")
int skyseed_parameter_map(struct __sk_buff *skb)
{
    __u32 zero = 0;
    __u32 *generation = bpf_map_lookup_elem(&parameter_generation, &zero);
    __u32 *generation_ok;
    __u32 *future_generation;
    struct parameter_key key = {};
    struct parameter_value *parameter;
    int key_status;

    if (!generation) {
        count_packet(COUNTER_DROP_MISSING_GENERATION);
        return TC_ACT_SHOT;
    }

    key_status = parse_parameter_key(skb, &key);
    if (key_status == 0)
        return TC_ACT_OK;
    if (key_status < 0)
        return TC_ACT_SHOT;

    generation_ok = bpf_map_lookup_elem(&param_status, generation);
    if (!generation_ok || !*generation_ok) {
        count_packet(COUNTER_DROP_MISSING_GENERATION);
        return TC_ACT_SHOT;
    }
    count_packet(COUNTER_PARAMETER_GENERATION_HIT);

    future_generation = bpf_map_lookup_elem(&future_param_gen, &zero);
    if (future_generation && *future_generation != *generation)
        count_packet(COUNTER_FUTURE_GENERATION_NOT_USED);

    key.generation = *generation;
    parameter = bpf_map_lookup_elem(&parameter_values, &key);
    if (!parameter) {
        count_packet(COUNTER_DROP_MISSING_PARAMETER);
        return TC_ACT_SHOT;
    }
    count_packet(COUNTER_PARAMETER_ENTRY_HIT);

    if (!parameter->active) {
        count_packet(COUNTER_DROP_INACTIVE);
        return TC_ACT_SHOT;
    }

    if (parameter->mark)
        skb->mark = parameter->mark;

    if (parameter->loss_ppm >= LOSS_PPM_DENOMINATOR) {
        count_packet(COUNTER_DROP_LOSS);
        return TC_ACT_SHOT;
    }
    if (parameter->loss_ppm > 0 &&
        (bpf_get_prandom_u32() % LOSS_PPM_DENOMINATOR) < parameter->loss_ppm) {
        count_packet(COUNTER_DROP_LOSS);
        return TC_ACT_SHOT;
    }

    count_packet(COUNTER_PASS_COUNT);
    return TC_ACT_OK;
}

char __license[] SEC("license") = "GPL";
