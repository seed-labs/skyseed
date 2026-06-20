// SPDX-License-Identifier: GPL-2.0
/*
 * Isolated tc-BPF packet-path prototype for Skyseed RuntimeEpoch experiments.
 *
 * This file is deliberately outside the canonical runtime. It is intended for
 * netns/veth-only tests that prove whether an active_generation map can steer
 * packet-path pass/drop decisions before any G/Z forwarding backend exists.
 */

#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#include <linux/types.h>

#define SEC(NAME) __attribute__((section(NAME), used))
#define PIN_GLOBAL_NS 2

#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
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

static void *(*bpf_map_lookup_elem)(void *map, const void *key) =
    (void *)BPF_FUNC_map_lookup_elem;

struct bpf_elf_map SEC("maps") active_generation = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = 1,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") generation_policy = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = 16,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") packet_counters = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u64),
    .max_elem = 8,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") lookup_pad = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = 4,
    .pinning = PIN_GLOBAL_NS,
};

static __always_inline void count_packet(__u32 key)
{
    __u64 *counter = bpf_map_lookup_elem(&packet_counters, &key);

    if (counter)
        __sync_fetch_and_add(counter, 1);
}

SEC("classifier/empty")
int skyseed_empty(struct __sk_buff *skb)
{
    return TC_ACT_OK;
}

SEC("classifier/lookup1")
int skyseed_lookup1(struct __sk_buff *skb)
{
    __u32 zero = 0;
    __u32 *generation = bpf_map_lookup_elem(&active_generation, &zero);

    return generation ? TC_ACT_OK : TC_ACT_SHOT;
}

SEC("classifier/lookup3")
int skyseed_lookup3(struct __sk_buff *skb)
{
    __u32 zero = 0;
    __u32 *generation = bpf_map_lookup_elem(&active_generation, &zero);
    __u32 *policy;
    __u32 *pad;

    if (!generation)
        return TC_ACT_SHOT;

    policy = bpf_map_lookup_elem(&generation_policy, generation);
    pad = bpf_map_lookup_elem(&lookup_pad, &zero);
    if (!policy || !pad)
        return TC_ACT_SHOT;
    return TC_ACT_OK;
}

SEC("classifier/counter")
int skyseed_counter(struct __sk_buff *skb)
{
    __u32 pass_key = 0;

    count_packet(pass_key);
    return TC_ACT_OK;
}

SEC("classifier/generation")
int skyseed_generation_path(struct __sk_buff *skb)
{
    __u32 zero = 0;
    __u32 pass_key = 0;
    __u32 drop_key = 1;
    __u32 *generation = bpf_map_lookup_elem(&active_generation, &zero);
    __u32 *policy;

    if (!generation) {
        count_packet(drop_key);
        return TC_ACT_SHOT;
    }

    policy = bpf_map_lookup_elem(&generation_policy, generation);
    if (!policy || !*policy) {
        count_packet(drop_key);
        return TC_ACT_SHOT;
    }

    count_packet(pass_key);
    return TC_ACT_OK;
}

char __license[] SEC("license") = "GPL";
